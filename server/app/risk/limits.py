"""Hard risk limits. Built from settings; no component may loosen them at runtime."""

from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import Settings


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.005
    max_daily_loss: float = 0.02
    max_drawdown: float = 0.08
    max_symbol_risk: float = 0.015
    max_strategy_risk: float = 0.02
    max_portfolio_risk: float = 0.03
    max_margin_usage: float = 0.40
    max_positions: int = 8
    max_positions_per_symbol: int = 3
    max_correlated_exposure: float = 0.02
    correlation_threshold: float = 0.6
    risk_multiplier_min: float = 0.75
    risk_multiplier_max: float = 1.25
    min_reward_risk: float = 1.0
    commission_per_lot: float = 0.0

    @classmethod
    def from_settings(cls, s: Settings) -> RiskLimits:
        return cls(
            risk_per_trade=s.risk_per_trade,
            max_daily_loss=s.max_daily_loss,
            max_drawdown=s.max_drawdown,
            max_symbol_risk=s.max_symbol_risk,
            max_strategy_risk=s.max_strategy_risk,
            max_portfolio_risk=s.max_portfolio_risk,
            max_margin_usage=s.max_margin_usage,
            max_positions=s.max_positions,
            max_positions_per_symbol=s.max_positions_per_symbol,
            max_correlated_exposure=s.max_correlated_exposure,
            correlation_threshold=s.correlation_threshold,
            risk_multiplier_min=s.risk_multiplier_min,
            risk_multiplier_max=s.risk_multiplier_max,
            commission_per_lot=s.paper_commission_per_lot,
        )
