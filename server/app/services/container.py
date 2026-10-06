"""Application service container (built once at startup, injected into routes)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from app.ai.features import FeatureCache
from app.ai.inference import HealthThresholds, InferenceService
from app.ai.model_registry import ModelRegistry
from app.config.settings import Settings
from app.config.symbols import SymbolResolver
from app.database import repositories as repo
from app.database.database import Database
from app.execution.slippage import SlippageMonitor
from app.monitoring.alerts import Notifier
from app.monitoring.metrics import Metrics, metrics
from app.news.economic_calendar import EconomicCalendar
from app.news.news_filter import BlackoutConfig, NewsFilter
from app.news.news_provider import NewsProvider, NewsProviderError, build_provider
from app.portfolio.allocation import AllocationPolicy
from app.portfolio.correlation import CorrelationMatrix
from app.portfolio.portfolio_manager import PortfolioManager, spec_from_record
from app.risk.limits import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.services.paper_service import PaperTradingService
from app.services.risk_state_service import RiskStateService
from app.services.security import RateLimiter, RedisRateLimiter
from app.sessions.session_engine import SessionEngine
from app.strategies.router import StrategyRouter

RUNTIME_NOTE = "runtime"


@dataclass
class RuntimeOverrides:
    """Dashboard overrides. ``effective_*`` never loosen the env limits."""

    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any) -> Any:
        v = self.values.get(key)
        return default if v is None else v


class Container:
    def __init__(self, settings: Settings, db: Database, news_provider: NewsProvider | None = None,
                 notifier: Notifier | None = None):
        self.settings = settings
        self.db = db
        self.metrics: Metrics = metrics
        self.notifier = notifier or Notifier.from_settings(settings)
        self.resolver = SymbolResolver(settings.symbol_map())
        self.session_engine = SessionEngine()
        self.provider_error = ""
        if news_provider is None:
            try:
                news_provider = build_provider(settings.news_provider, settings.news_api_key.get_secret_value(),
                                               settings.news_api_base_url, settings.news_csv_path)
            except NewsProviderError as exc:
                self.provider_error = str(exc)
        self.calendar = EconomicCalendar(news_provider, settings.news_refresh_seconds, settings.news_stale_after_seconds)
        self.news_filter = NewsFilter(self.calendar, BlackoutConfig(
            high_before_min=settings.news_high_before_minutes, high_after_min=settings.news_high_after_minutes,
            medium_before_min=settings.news_medium_before_minutes, medium_after_min=settings.news_medium_after_minutes,
            block_medium=settings.news_block_medium, failure_policy=settings.news_failure_policy,
        ))
        self.registry = ModelRegistry(db, settings.models_dir)
        self.inference = InferenceService(
            self.registry,
            HealthThresholds(settings.model_max_drift_psi, settings.model_min_recent_trades,
                             settings.model_min_recent_profit_factor, settings.model_max_error_rate),
            allow_rule_based=settings.allow_rule_based_fallback,
            pinned_version=settings.model_version,
        )
        self.correlation = CorrelationMatrix()
        self.feature_cache = FeatureCache()
        self.limits = RiskLimits.from_settings(settings)
        self.risk_engine = RiskEngine(self.limits, self.correlation.get, settings.equity_steps())
        self.risk_state = RiskStateService(db, settings, self.notifier)
        self.paper = PaperTradingService(db, settings, self.notifier)
        self.portfolio = PortfolioManager(db, self.resolver, self.correlation)
        self.allocation = AllocationPolicy()
        self.slippage = SlippageMonitor(settings.slippage_window, settings.max_slippage_points)
        self.router = StrategyRouter(allow_sydney=settings.allow_sydney_session)
        self.rate_limiter: RateLimiter | RedisRateLimiter = (
            RedisRateLimiter(settings.redis_url) if settings.redis_url else RateLimiter()
        )
        self.last_atr: dict[str, float] = {}
        self.last_regime: dict[str, str] = {}
        self.last_confidence: dict[str, float] = {}
        self.overrides = RuntimeOverrides()
        self._lock = threading.Lock()

    # ----------------------------------------------------------------- startup
    def startup(self) -> None:
        self.inference.reload()
        self.load_overrides()
        with self.db.session() as s:
            specs = {spec.symbol: spec for spec in (spec_from_record(r) for r in repo.symbol_specs(s))}
            repo.add_system_event(s, "backend", "STARTUP", f"mode={self.settings.effective_trading_mode.value}")
            repo.record_config_version(s, self.public_settings(), "system", "startup")
        self.paper.bootstrap(specs)
        self.calendar.refresh_if_due(force=True)

    def load_overrides(self) -> None:
        with self.db.session() as s:
            rec = repo.latest_config_version(s, RUNTIME_NOTE)
            self.overrides = RuntimeOverrides(dict(rec.settings) if rec else {})
        self._apply_overrides()

    def set_overrides(self, values: dict[str, Any], actor: str) -> None:
        with self.db.session() as s:
            repo.record_config_version(s, values, actor, RUNTIME_NOTE)
        self.overrides = RuntimeOverrides(values)
        self._apply_overrides()

    def _apply_overrides(self) -> None:
        base = RiskLimits.from_settings(self.settings)
        o = self.overrides
        self.limits = RiskLimits(**{
            **base.__dict__,
            "risk_per_trade": min(base.risk_per_trade, o.get("risk_per_trade", base.risk_per_trade)),
            "max_positions": min(base.max_positions, o.get("max_positions", base.max_positions)),
            "max_portfolio_risk": min(base.max_portfolio_risk, o.get("max_portfolio_risk", base.max_portfolio_risk)),
        })
        self.risk_engine.limits = self.limits

    def confidence_threshold(self) -> float:
        return max(self.settings.confidence_threshold,
                   self.overrides.get("confidence_threshold", self.settings.confidence_threshold))

    def symbol_enabled(self, logical: str) -> bool:
        enabled = self.overrides.get("enabled_symbols", None)
        return enabled is None or logical in enabled

    def session_enabled(self, session: str) -> bool:
        enabled = self.overrides.get("enabled_sessions", None)
        return enabled is None or session in enabled

    def public_settings(self) -> dict[str, Any]:
        """Effective, NON-SECRET settings (safe to show on the dashboard)."""
        s = self.settings
        secret_fields = {"ea_hmac_secret", "api_key", "jwt_secret", "news_api_key", "telegram_bot_token",
                         "smtp_password", "database_url", "redis_url", "live_trading_confirmation"}
        data = {k: (v.value if hasattr(v, "value") else v) for k, v in s.model_dump().items() if k not in secret_fields}
        data["effective_trading_mode"] = s.effective_trading_mode.value
        data["live_trading_permitted"] = s.live_trading_permitted
        data["symbol_map"] = s.symbol_map()
        data["effective_limits"] = self.limits.__dict__
        data["runtime_overrides"] = self.overrides.values
        return data
