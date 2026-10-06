"""US30 (Dow CFD): opening-range breakout after the 09:30 New York cash open,
then trend pullbacks. Trades only while the US cash market is active.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.domain import Direction, Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal

NY = ZoneInfo("America/New_York")


class US30Strategy(Strategy):
    name = "US30_NY"
    sessions = frozenset({SessionName.NEW_YORK, SessionName.OVERLAP})
    regimes = frozenset({Regime.TREND_UP, Regime.TREND_DOWN, Regime.RANGE, Regime.HIGH_VOLATILITY})
    opening_range_minutes = 30

    def generate(self, ctx: StrategyContext) -> StrategySignal | None:
        local = ctx.now.astimezone(NY)
        cash_open = datetime.combine(local.date(), time(9, 30), tzinfo=NY)
        or_end = datetime.combine(local.date(), time(10, 0), tzinfo=NY)
        if local < cash_open:
            return None  # pre-market: thin and gappy
        if local >= or_end and (local - or_end).total_seconds() <= 2 * 3600:
            sig = self._opening_range_breakout(ctx, cash_open, or_end)
            if sig:
                return sig
        if ctx.regime is Regime.RANGE:
            return None
        sig = self.pullback_continuation(ctx, self)
        if sig:
            sig.strategy = f"{self.name}_PULLBACK"
        return sig

    def _opening_range_breakout(self, ctx: StrategyContext, start: datetime, end: datetime) -> StrategySignal | None:
        f = ctx.features
        orb = f[(f["time"] >= pd.Timestamp(start)) & (f["time"] < pd.Timestamp(end))]
        if len(orb) < 2:
            return None
        high, low = float(orb["high"].max()), float(orb["low"].min())
        last, prev, p = ctx.last, ctx.prev, ctx.params
        if high - low > 3 * ctx.atr or last["adx"] < p.adx_threshold:
            return None
        buf = p.breakout_buffer_atr * ctx.atr
        if last["close"] > high + buf and prev["close"] <= high + buf and last["body_ratio"] > 0.4:
            stop = self.atr_stop(ctx, Direction.BUY, structure=(high + low) / 2, buffer_atr=0.0)
            return self.build(ctx, Direction.BUY, stop, "OPENING_RANGE_BREAKOUT_UP", or_high=high, or_low=low)
        if last["close"] < low - buf and prev["close"] >= low - buf and last["body_ratio"] < -0.4:
            stop = self.atr_stop(ctx, Direction.SELL, structure=(high + low) / 2, buffer_atr=0.0)
            return self.build(ctx, Direction.SELL, stop, "OPENING_RANGE_BREAKOUT_DOWN", or_high=high, or_low=low)
        return None
