"""London: volatility expansion out of the Asian range, then trend continuation.

Breakouts are only taken with confirmation: two consecutive closes beyond the
Asian range (plus buffer), ADX above threshold and EMA structure agreeing. A
breakout the market immediately rejects is not chased.
"""

from __future__ import annotations

from app.domain import Direction, Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class LondonTrendStrategy(Strategy):
    name = "LONDON_TREND"
    sessions = frozenset({SessionName.LONDON})
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOLATILITY, Regime.RANGE})
    breakout_minutes = 180

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        if ctx.session.minutes_into_primary <= self.breakout_minutes:
            sig = self._breakout(ctx)
            if sig:
                return sig
        if ctx.regime is Regime.RANGE:
            return None
        sig = self.pullback_continuation(ctx, self)
        if sig:
            sig.strategy = f"{self.name}_PULLBACK"
        return sig

    def _breakout(self, ctx: StrategyContext) -> StrategySignal | None:
        london_open = ctx.session_engine.window(SessionName.LONDON).bounds_for(ctx.now)[0]
        rng = self.asian_range(ctx, until=london_open)
        if rng is None:
            return None
        high, low, _ = rng
        last, prev, p = ctx.last, ctx.prev, ctx.params
        buf = p.breakout_buffer_atr * ctx.atr
        if last["adx"] < p.adx_threshold or last["range_atr"] < 0.8:
            return None
        if (last["close"] > high + buf and prev["close"] > high and last["ema20"] > last["ema50"]
                and last["body_ratio"] > 0.3):
            stop = self.atr_stop(ctx, Direction.BUY, structure=high, buffer_atr=0.5)
            return self.build(ctx, Direction.BUY, stop, "ASIAN_RANGE_BREAKOUT_UP", tp1_r=p.tp1_r,
                              range_high=high, range_low=low)
        if (last["close"] < low - buf and prev["close"] < low and last["ema20"] < last["ema50"]
                and last["body_ratio"] < -0.3):
            stop = self.atr_stop(ctx, Direction.SELL, structure=low, buffer_atr=0.5)
            return self.build(ctx, Direction.SELL, stop, "ASIAN_RANGE_BREAKOUT_DOWN", tp1_r=p.tp1_r,
                              range_high=high, range_low=low)
        return None
