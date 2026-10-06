"""Crude oil: trend pullbacks during London / New York only.

Oil gaps on inventory data (EIA/API) and OPEC headlines; those events are
blocked by the news filter. Stops are wider (volatility) and mean reversion is
not attempted in high volatility.
"""

from __future__ import annotations

from app.domain import Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class OilStrategy(Strategy):
    name = "OIL_TREND"
    sessions = frozenset({SessionName.LONDON, SessionName.NEW_YORK, SessionName.OVERLAP})
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOLATILITY})

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        oil_params = ctx.params.with_overrides(sl_atr_mult=max(ctx.params.sl_atr_mult, 2.0),
                                               adx_threshold=max(ctx.params.adx_threshold, 22.0))
        original = ctx.params
        ctx.params = oil_params
        try:
            sig = self.pullback_continuation(ctx, self, rsi_cap=0.72)
        finally:
            ctx.params = original
        return sig
