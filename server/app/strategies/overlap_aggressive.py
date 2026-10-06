"""London / New York overlap: higher-activity momentum continuation.

"Aggressive" means looser entry confirmation (single-bar momentum through the
20-bar extreme in a trend), never larger risk: sizing and every limit remain
with the risk engine.
"""

from __future__ import annotations

from app.domain import Direction, Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class OverlapAggressiveStrategy(Strategy):
    name = "OVERLAP_MOMENTUM"
    sessions = frozenset({SessionName.OVERLAP})
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN, Regime.HIGH_VOLATILITY})

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        last, prev, p = ctx.last, ctx.prev, ctx.params
        adx_rising = last["adx"] >= p.adx_threshold and last["adx"] > prev["adx"]
        if not adx_rising:
            sig = self.pullback_continuation(ctx, self)
            if sig:
                sig.strategy = f"{self.name}_PULLBACK"
            return sig
        prior_high = float(ctx.features["high"].iloc[-21:-1].max())
        prior_low = float(ctx.features["low"].iloc[-21:-1].min())
        if ctx.regime is not Regime.TREND_DOWN and last["close"] > prior_high and last["body_ratio"] > 0.5 \
                and last["ema20"] > last["ema50"] and last["rsi_14"] < 0.80:
            stop = self.atr_stop(ctx, Direction.BUY, structure=float(last["low"]))
            return self.build(ctx, Direction.BUY, stop, "MOMENTUM_BREAK_HIGH20")
        if ctx.regime is not Regime.TREND_UP and last["close"] < prior_low and last["body_ratio"] < -0.5 \
                and last["ema20"] < last["ema50"] and last["rsi_14"] > 0.20:
            stop = self.atr_stop(ctx, Direction.SELL, structure=float(last["high"]))
            return self.build(ctx, Direction.SELL, stop, "MOMENTUM_BREAK_LOW20")
        return None
