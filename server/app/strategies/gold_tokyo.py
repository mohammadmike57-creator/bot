"""Gold during Tokyo / East Asia.

Asian sessions are typically lower-volatility and range-bound for gold, so
this is NOT the London breakout logic:

* RANGE / LOW_VOLATILITY -> fade the edges of the developing Asian range back
  toward its midpoint, only after a rejection candle and an RSI extreme.
* TREND_UP / TREND_DOWN -> pullback-continuation entries with the trend.
* Spread must be tighter than in liquid sessions (Asian spreads widen).
"""

from __future__ import annotations

from app.domain import Direction, Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal


class GoldTokyoStrategy(Strategy):
    name = "TOKYO_GOLD"
    sessions = frozenset({SessionName.TOKYO})
    regimes = frozenset({Regime.RANGE, Regime.LOW_VOLATILITY, Regime.TREND_UP, Regime.TREND_DOWN})
    max_spread_atr = 0.10

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        last = ctx.last
        if float(last.get("spread_atr", 0.0) or 0.0) > self.max_spread_atr:
            return None
        if ctx.regime.is_trend:
            sig = self.pullback_continuation(ctx, self, rsi_cap=0.70)
            if sig:
                sig.strategy = f"{self.name}_PULLBACK"
            return sig
        rng = self.asian_range(ctx)
        if rng is None:
            return None
        high, low, _ = rng
        width = high - low
        if width < 0.8 * ctx.atr:  # too narrow to fade profitably after costs
            return None
        mid = (high + low) / 2
        p = ctx.params
        edge = 0.15 * width
        if last["high"] >= high - edge and last["rsi_14"] >= p.rsi_overbought and last["close"] < last["open"] \
                and last["upper_wick_ratio"] >= 0.3:
            stop = max(high, float(last["high"])) + 0.5 * ctx.atr
            sig = self.build(ctx, Direction.SELL, stop, "ASIAN_RANGE_FADE_HIGH", range_width=width)
            return self._target_mid(sig, mid)
        if last["low"] <= low + edge and last["rsi_14"] <= p.rsi_oversold and last["close"] > last["open"] \
                and last["lower_wick_ratio"] >= 0.3:
            stop = min(low, float(last["low"])) - 0.5 * ctx.atr
            sig = self.build(ctx, Direction.BUY, stop, "ASIAN_RANGE_FADE_LOW", range_width=width)
            return self._target_mid(sig, mid)
        return None

    def _target_mid(self, sig: StrategySignal | None, mid: float) -> StrategySignal | None:
        """Mean reversion targets the range midpoint; skip if that gives < 1R."""
        if sig is None:
            return None
        risk = abs(sig.entry - sig.stop_loss)
        reward = (mid - sig.entry) * sig.direction.sign
        if reward < risk:
            return None
        sig.take_profit = mid
        sig.take_profit_2 = mid
        sig.strategy = f"{self.name}_RANGE"
        return sig
