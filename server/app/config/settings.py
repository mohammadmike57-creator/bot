"""Application configuration.

All values come from environment variables (or a local ``.env`` file during
development). Nothing secret is hardcoded. Safety-critical defaults are
conservative: the platform starts in PAPER mode with live trading disabled.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_LIVE_TRADING_RISKS"

# Repository root (server/app/config/settings.py -> parents[3]). The .env file, the local
# SQLite database and the models/ directory are resolved from here, so the backend (run
# from server/) and the scripts (run from the repo root) always agree.
REPO_ROOT = Path(__file__).resolve().parents[3]


class TradingMode(str, Enum):
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


class NewsFailurePolicy(str, Enum):
    SAFE_MODE = "SAFE_MODE"  # block new trades on every symbol
    ALLOW = "ALLOW"  # NOT recommended; only for offline research


class EmergencyClosePolicy(str, Enum):
    KEEP_POSITIONS = "KEEP_POSITIONS"  # stop new trades, keep managing open ones
    CLOSE_ALL = "CLOSE_ALL"  # instruct EA to flatten own positions


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # "KEY=" in .env means "use the default"
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    app_name: str = "trading-ai-platform"
    environment: str = Field(default="development", description="development|test|production")
    log_level: str = "INFO"
    display_timezone: str = "UTC"

    # ------------------------------------------------------------ trading mode
    trading_mode: TradingMode = TradingMode.PAPER
    enable_live_trading: bool = False
    live_trading_confirmation: str = ""

    # --------------------------------------------------------------- security
    # EA authentication (HMAC signed requests)
    ea_api_key_id: str = Field(default="", description="Public key id sent by the EA")
    ea_hmac_secret: SecretStr = SecretStr("")
    # Dashboard / admin authentication
    api_key: SecretStr = SecretStr("")
    jwt_secret: SecretStr = SecretStr("")
    jwt_ttl_minutes: int = 60
    signature_max_skew_seconds: int = 30
    rate_limit_per_minute: int = 240
    rate_limit_auth_per_minute: int = 10
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --------------------------------------------------------------- storage
    database_url: str = f"sqlite:///{REPO_ROOT / 'trading_dev.db'}"
    redis_url: str = ""
    models_dir: str = str(REPO_ROOT / "models")

    # ------------------------------------------------------------ risk limits
    risk_per_trade: float = 0.005
    max_daily_loss: float = 0.02
    max_drawdown: float = 0.08
    max_symbol_risk: float = 0.015
    max_strategy_risk: float = 0.02
    max_portfolio_risk: float = 0.03
    max_margin_usage: float = 0.40
    # Position counts are a secondary limit: open RISK (below) is what really caps exposure.
    # Trades whose stop is at break-even or better carry zero open risk, which frees budget
    # for new trades - so the bot can hold many positions while the money at risk stays capped.
    max_positions: int = 15
    max_positions_per_symbol: int = 5
    max_correlated_exposure: float = 0.02
    correlation_threshold: float = 0.6
    # Losing-streak protection: after N losers trade smaller, after M losers pause.
    loss_streak_reduce_after: int = 2
    loss_streak_risk_scale: float = 0.5
    loss_streak_cooldown_after: int = 3
    loss_streak_cooldown_minutes: int = 120
    risk_multiplier_min: float = 0.75
    risk_multiplier_max: float = 1.25
    # equity growth (equity / initial equity) -> risk multiplier.
    # With a $100 start: $125 -> 1.05, $150 -> 1.10, $200 -> 1.20 (always clamped to the bounds).
    equity_scaling_steps: str = "1.0:1.0,1.25:1.05,1.5:1.10,2.0:1.20"
    initial_equity: float = 100.0
    emergency_close_policy: EmergencyClosePolicy = EmergencyClosePolicy.KEEP_POSITIONS
    drawdown_recovery_requires_manual_reset: bool = True
    trading_day_timezone: str = "America/New_York"
    trading_day_rollover_hour: int = 17

    # --------------------------------------------------------------- execution
    max_spread_points: float = 0.0  # 0 disables the absolute cap; ATR ratio still applies
    max_spread_atr_ratio: float = 0.15
    max_slippage_points: float = 50.0
    abnormal_slippage_atr_ratio: float = 0.10
    slippage_window: int = 20
    signal_ttl_seconds: int = 60
    heartbeat_timeout_seconds: int = 90
    paper_commission_per_lot: float = 0.0
    paper_slippage_points: float = 2.0

    # --------------------------------------------------------- trade management
    sl_atr_multiplier: float = 1.5
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    tp1_close_fraction: float = 0.5
    be_trigger_r: float = 1.0
    be_offset_r: float = 0.05
    tighten_trigger_r: float = 0.8
    tighten_to_r: float = -0.5
    trail_start_r: float = 1.0
    trail_atr_multiplier: float = 1.0

    # ----------------------------------------------------------------- signals
    confidence_threshold: float = 0.60
    min_probability_edge: float = 0.15
    max_hold_probability: float = 0.50
    allow_rule_based_fallback: bool = True  # PAPER/DEMO only unless the next flag is set
    allow_rule_based_in_live: bool = False
    allow_sydney_session: bool = False

    # ------------------------------------------------------------------- news
    news_provider: str = Field(default="none", description="none|fmp|finnhub|tradingeconomics|csv")
    news_api_key: SecretStr = SecretStr("")
    news_api_base_url: str = ""
    news_csv_path: str = ""
    news_refresh_seconds: int = 900
    news_stale_after_seconds: int = 3600
    news_high_before_minutes: int = 15
    news_high_after_minutes: int = 20
    news_medium_before_minutes: int = 5
    news_medium_after_minutes: int = 5
    news_block_medium: bool = False
    news_failure_policy: NewsFailurePolicy = NewsFailurePolicy.SAFE_MODE

    # --------------------------------------------------------------- symbols
    # Logical name -> broker symbol. Never assume the broker's naming.
    xauusd_symbol: str = ""
    oil_symbol: str = ""
    us30_symbol: str = ""
    extra_symbols: str = Field(default="", description="LOGICAL=BROKER,... e.g. EURUSD=EURUSD.r")

    # ------------------------------------------------------------------ model
    model_version: str = ""
    model_max_drift_psi: float = 0.25
    model_min_recent_trades: int = 20
    model_min_recent_profit_factor: float = 0.9
    model_max_error_rate: float = 0.05

    # ----------------------------------------------------------- notifications
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    alert_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    alert_email_to: str = ""
    alert_email_from: str = ""
    large_profit_r: float = 2.0
    large_loss_r: float = 1.5

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return value

    @field_validator(
        "risk_per_trade",
        "max_daily_loss",
        "max_drawdown",
        "max_symbol_risk",
        "max_strategy_risk",
        "max_portfolio_risk",
        "max_margin_usage",
        "max_correlated_exposure",
    )
    @classmethod
    def _fraction(cls, value: float) -> float:
        if not 0 < value < 1:
            raise ValueError("risk limits are fractions of equity and must be in (0, 1)")
        return value

    @field_validator("confidence_threshold")
    @classmethod
    def _confidence(cls, value: float) -> float:
        if not 0.5 <= value < 1:
            raise ValueError("confidence_threshold must be in [0.5, 1)")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        if self.risk_per_trade > self.max_symbol_risk:
            raise ValueError("risk_per_trade cannot exceed max_symbol_risk")
        if self.max_symbol_risk > self.max_portfolio_risk:
            raise ValueError("max_symbol_risk cannot exceed max_portfolio_risk")
        if not 0 < self.risk_multiplier_min <= 1 <= self.risk_multiplier_max <= 2:
            raise ValueError("risk multiplier bounds must satisfy 0 < min <= 1 <= max <= 2")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if not 0 < self.loss_streak_risk_scale <= 1:
            raise ValueError("loss_streak_risk_scale must be in (0, 1] - risk is never increased after losses")
        if self.loss_streak_cooldown_after < 0 or self.loss_streak_reduce_after < 0:
            raise ValueError("loss streak counts must be >= 0 (0 disables)")
        return self

    # ----------------------------------------------------------- derived values
    @property
    def live_trading_permitted(self) -> bool:
        """LIVE requires three independent, explicit user actions."""
        return (
            self.trading_mode == TradingMode.LIVE
            and self.enable_live_trading
            and self.live_trading_confirmation == LIVE_CONFIRMATION_PHRASE
        )

    @property
    def effective_trading_mode(self) -> TradingMode:
        """LIVE silently downgrades to PAPER unless every safeguard is set."""
        if self.trading_mode == TradingMode.LIVE and not self.live_trading_permitted:
            return TradingMode.PAPER
        return self.trading_mode

    def symbol_map(self) -> dict[str, str]:
        """Logical symbol -> broker symbol for every configured instrument."""
        mapping: dict[str, str] = {}
        for logical, broker in (
            ("XAUUSD", self.xauusd_symbol),
            ("OIL", self.oil_symbol),
            ("US30", self.us30_symbol),
        ):
            if broker.strip():
                mapping[logical] = broker.strip()
        for item in self.extra_symbols.split(","):
            if "=" in item:
                logical, broker = item.split("=", 1)
                if logical.strip() and broker.strip():
                    mapping[logical.strip().upper()] = broker.strip()
        return mapping

    def equity_steps(self) -> list[tuple[float, float]]:
        steps: list[tuple[float, float]] = []
        for item in self.equity_scaling_steps.split(","):
            if ":" not in item:
                continue
            threshold, mult = item.split(":", 1)
            steps.append((float(threshold), float(mult)))
        return sorted(steps)

    def is_production(self) -> bool:
        return self.environment.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
