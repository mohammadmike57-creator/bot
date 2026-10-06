"""Strategy framework.

Strategies look at causal features up to the last *closed* bar and may
propose a trade with a defined stop and target. They never size positions and
never bypass the risk engine. Returning ``None`` (no trade) is the normal case.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import pandas as pd

from app.domain import Direction, Regime, SessionName, SymbolSpec
from app.sessions.session_engine import SessionEngine, SessionInfo


@dataclass(frozen=True)
class StrategyParams:
    sl_atr_mult: float = 1.5
    tp1_r: float = 1.0
    tp2_r: float = 2.0
    adx_threshold: float = 22.0
    rsi_overbought: float = 0.70
    rsi_oversold: float = 0.30
    pullback_tolerance_atr: float = 0.3
    breakout_buffer_atr: float = 0.1
    min_range_bars: int = 8
    max_spread_atr: float = 0.15
    high_vol_risk_scale: float = 0.5

    def with_overrides(self, **overrides: Any) -> StrategyParams:
        return replace(self, **{k: v for k, v in overrides.items() if hasattr(self, k)})


@dataclass
class StrategyContext:
    symbol: str  # logical symbol
    timeframe: str
    features: pd.DataFrame  # last row = last CLOSED bar
    regime: Regime
    session: SessionInfo
    bid: float
    ask: float
    spec: SymbolSpec | None = None
    params: StrategyParams = field(default_factory=StrategyParams)
    session_engine: SessionEngine = field(default_factory=SessionEngine)

    @property
    def last(self) -> pd.Series:
        return self.features.iloc[-1]

    @property
    def prev(self) -> pd.Series:
        return self.features.iloc[-2]

    @property
    def atr(self) -> float:
        return float(self.last["atr"])

    @property
    def now(self) -> datetime:
        return self.session.ts

    def entry_for(self, d: Direction) -> float:
        return self.ask if d is Direction.BUY else self.bid

    def bars_since(self, start: datetime) -> pd.DataFrame:
        return self.features[self.features["time"] >= pd.Timestamp(start)]


@dataclass
class StrategySignal:
    direction: Direction
    entry: float
    stop_loss: float
    take_profit: float
    take_profit_2: float
    strategy: str
    reason: str
    risk_scale: float = 1.0
    setup: dict[str, float] = field(default_factory=dict)


class Strategy(ABC):
    name: str = "BASE"
    sessions: frozenset[SessionName] = frozenset()
    regimes: frozenset[Regime] = frozenset()

    def applicable(self, ctx: StrategyContext) -> bool:
        return ctx.session.primary in self.sessions and ctx.regime in self.regimes

    @abstractmethod
    def generate(self, ctx: StrategyContext) -> StrategySignal | None: ...

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        if len(ctx.features) < 60 or not self.applicable(ctx):
            return None
        last = ctx.last
        if any(math.isnan(float(last[c])) for c in ("atr", "ema20", "ema50", "adx", "rsi")):
            return None
        if ctx.atr <= 0 or ctx.ask <= 0 or ctx.bid <= 0 or ctx.ask < ctx.bid:
            return None
        signal = self.generate(ctx)
        if signal and ctx.regime is Regime.HIGH_VOLATILITY:
            signal.risk_scale = min(signal.risk_scale, ctx.params.high_vol_risk_scale)
        return signal

    # ----------------------------------------------------------- shared helpers
    def build(self, ctx: StrategyContext, d: Direction, stop: float, reason: str,
              tp1_r: float | None = None, risk_scale: float = 1.0, **setup: float) -> StrategySignal | None:
        entry = ctx.entry_for(d)
        risk = (entry - stop) * d.sign
        if risk <= 0:
            return None
        p = ctx.params
        r1 = tp1_r if tp1_r is not None else p.tp1_r
        tp = entry + d.sign * risk * max(r1, p.tp1_r)
        tp2 = entry + d.sign * risk * max(p.tp2_r, r1)
        return StrategySignal(d, entry, stop, tp, tp2, self.name, reason, risk_scale, dict(setup))

    @staticmethod
    def atr_stop(ctx: StrategyContext, d: Direction, mult: float | None = None, structure: float | None = None,
                 buffer_atr: float = 0.2) -> float:
        """Wider of the ATR stop and the structural stop (beyond swing +/- buffer)."""
        entry = ctx.entry_for(d)
        m = mult if mult is not None else ctx.params.sl_atr_mult
        atr_stop = entry - d.sign * m * ctx.atr
        if structure is None:
            return atr_stop
        struct_stop = structure - d.sign * buffer_atr * ctx.atr
        return min(atr_stop, struct_stop) if d is Direction.BUY else max(atr_stop, struct_stop)

    @staticmethod
    def pullback_continuation(ctx: StrategyContext, strategy: Strategy, rsi_cap: float = 0.75) -> StrategySignal | None:
        """Trend-following entry after a pullback to the 20 EMA, confirmed by a close back in trend direction."""
        last, p = ctx.last, ctx.params
        if last["adx"] < p.adx_threshold:
            return None
        tol = p.pullback_tolerance_atr * ctx.atr
        swing = ctx.features.tail(5)
        if ctx.regime is Regime.TREND_UP or (ctx.regime is Regime.HIGH_VOLATILITY and last["ema20"] > last["ema50"]):
            touched = swing["low"].min() <= last["ema20"] + tol
            confirmed = last["close"] > last["ema20"] and last["close"] > last["open"] and last["rsi_14"] < rsi_cap
            if touched and confirmed:
                stop = strategy.atr_stop(ctx, Direction.BUY, structure=float(swing["low"].min()))
                return strategy.build(ctx, Direction.BUY, stop, "TREND_PULLBACK_LONG", adx=float(last["adx"]))
        if ctx.regime is Regime.TREND_DOWN or (ctx.regime is Regime.HIGH_VOLATILITY and last["ema20"] < last["ema50"]):
            touched = swing["high"].max() >= last["ema20"] - tol
            confirmed = last["close"] < last["ema20"] and last["close"] < last["open"] and last["rsi_14"] > 1 - rsi_cap
            if touched and confirmed:
                stop = strategy.atr_stop(ctx, Direction.SELL, structure=float(swing["high"].max()))
                return strategy.build(ctx, Direction.SELL, stop, "TREND_PULLBACK_SHORT", adx=float(last["adx"]))
        return None

    @staticmethod
    def asian_range(ctx: StrategyContext, until: datetime | None = None) -> tuple[float, float, int] | None:
        """High, low and bar count of the most recent Tokyo session (closed bars only).

        ``until`` cuts the window early - e.g. at the London open, because in
        summer Tokyo (00:00-09:00 UTC) overlaps London's first two hours and
        those bars belong to the breakout, not to the range.
        """
        start, end = ctx.session_engine.asian_range_window(ctx.now)
        if until is not None:
            end = min(end, until)
        bars = ctx.features[(ctx.features["time"] >= pd.Timestamp(start)) & (ctx.features["time"] < pd.Timestamp(end))]
        if len(bars) < ctx.params.min_range_bars:
            return None
        return float(bars["high"].max()), float(bars["low"].min()), len(bars)
