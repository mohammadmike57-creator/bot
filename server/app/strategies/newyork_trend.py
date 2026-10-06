"""New York: trend continuation, plus exhaustion reversals in ranges.

The first 15 minutes after the session opens (heavy US data flow) are skipped;
scheduled releases are additionally blocked by the news filter. Staying flat
is the default outcome.
"""

from __future__ import annotations

from app.domain import Direction, Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class NewYorkTrendStrategy(Strategy):
    name = "NEWYORK_TREND"
    sessions = frozenset({SessionName.NEW_YORK})
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN, Regime.RANGE, Regime.HIGH_VOLATILITY})
    skip_first_minutes = 15

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        if ctx.session.minutes_into_primary < self.skip_first_minutes:
            return None
        if ctx.session.minutes_to_primary_close < 30:
            return None
        if ctx.regime is Regime.RANGE:
            return self._reversal(ctx)
        sig = self.pullback_continuation(ctx, self, rsi_cap=0.72)
        if sig:
            sig.strategy = f"{self.name}_CONTINUATION"
        return sig

    def _reversal(self, ctx: StrategyContext) -> StrategySignal | None:
        last, p = ctx.last, ctx.params
        if last["dist_high20_atr"] <= 0.2 and last["upper_wick_ratio"] >= 0.5 and last["rsi_14"] >= p.rsi_overbought:
            stop = self.atr_stop(ctx, Direction.SELL, structure=float(last["high"]), buffer_atr=0.3)
            sig = self.build(ctx, Direction.SELL, stop, "RANGE_EXHAUSTION_SHORT", tp1_r=1.2)
        elif last["dist_low20_atr"] <= 0.2 and last["lower_wick_ratio"] >= 0.5 and last["rsi_14"] <= p.rsi_oversold:
            stop = self.atr_stop(ctx, Direction.BUY, structure=float(last["low"]), buffer_atr=0.3)
            sig = self.build(ctx, Direction.BUY, stop, "RANGE_EXHAUSTION_LONG", tp1_r=1.2)
        else:
            return None
        if sig:
            sig.strategy = f"{self.name}_REVERSAL"
        return sig
