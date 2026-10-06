"""Order/position simulator shared by PAPER mode and the backtester.

Price conventions: charts/bars are BID prices. BUY positions open at ASK and
close at BID; SELL positions open at BID and close at ASK (= bid + spread).
When a stop and a target are both touched inside one bar, the stop is assumed
to have been hit first (conservative). Gaps through a stop fill at the gap
price, not at the stop.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime

from app.domain import Direction, SymbolSpec
from app.execution.trade_management import ManagedState, ManagementParams, manage, partial_volume


@dataclass(frozen=True)
class CostModel:
    commission_per_lot: float = 0.0  # round trip, account currency
    slippage_points: float = 0.0  # adverse slippage applied to market fills and stops
    swap_long_per_lot: float = 0.0  # per night, account currency (negative = cost)
    swap_short_per_lot: float = 0.0


@dataclass
class SimPosition:
    ticket: str
    symbol: str
    direction: Direction
    volume: float
    entry: float
    sl: float
    tp: float
    tp1: float
    strategy: str
    opened_at: datetime
    spec: SymbolSpec
    initial_sl: float = 0.0
    initial_volume: float = 0.0
    realized: float = 0.0
    commission: float = 0.0
    swap: float = 0.0
    risk_money: float = 0.0
    signal_id: str = ""
    session: str = ""
    regime: str = ""
    confidence: float = 0.0
    partial_done: bool = False
    be_done: bool = False
    requested_price: float = 0.0
    last_swap_day: object = None
    meta: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.initial_sl:
            self.initial_sl = self.sl
        if not self.initial_volume:
            self.initial_volume = self.volume

    def managed(self) -> ManagedState:
        return ManagedState(self.direction, self.entry, self.initial_sl, self.sl, self.volume, self.initial_volume,
                            self.partial_done, self.be_done)


@dataclass
class ClosedTrade:
    ticket: str
    symbol: str
    direction: Direction
    strategy: str
    entry: float
    exit: float
    volume: float  # volume closed in this event
    opened_at: datetime
    closed_at: datetime
    profit: float  # net of commission/swap share for this volume
    reason: str
    risk_money: float
    session: str = ""
    regime: str = ""
    confidence: float = 0.0
    final: bool = True  # False for partial closes

    @property
    def r_multiple(self) -> float:
        return self.profit / self.risk_money if self.risk_money > 0 else 0.0


@dataclass(frozen=True)
class SimEvent:
    kind: str  # OPEN / PARTIAL / CLOSE / MODIFY
    ticket: str
    detail: str
    closed: ClosedTrade | None = None


def pnl_money(spec: SymbolSpec, direction: Direction, entry: float, exit_: float, volume: float) -> float:
    move = (exit_ - entry) * direction.sign
    tick_value = spec.tick_value if move >= 0 else spec.loss_tick_value()
    return move / spec.tick_size * tick_value * volume


class TradeSimulator:
    def __init__(self, costs: CostModel, management: ManagementParams):
        self.costs = costs
        self.management = management
        self.positions: dict[str, SimPosition] = {}
        self._ids = itertools.count(1)

    def next_ticket(self, prefix: str = "SIM") -> str:
        return f"{prefix}-{next(self._ids)}"

    # ------------------------------------------------------------------ open
    def fill_price(self, direction: Direction, bid: float, ask: float, spec: SymbolSpec) -> float:
        slip = self.costs.slippage_points * spec.point
        return ask + slip if direction is Direction.BUY else bid - slip

    def open(self, pos: SimPosition) -> SimEvent:
        pos.commission = self.costs.commission_per_lot * pos.volume
        self.positions[pos.ticket] = pos
        return SimEvent("OPEN", pos.ticket, f"{pos.direction.value} {pos.volume} {pos.symbol} @ {pos.entry}")

    # ----------------------------------------------------------------- price
    def on_bar(self, symbol: str, ts: datetime, bid_open: float, bid_high: float, bid_low: float, bid_close: float,
               spread_price: float, atr: float) -> list[SimEvent]:
        """Process one bar (or one quote with open=high=low=close) for ``symbol``."""
        events: list[SimEvent] = []
        for pos in [p for p in self.positions.values() if p.symbol == symbol]:
            events.extend(self._process(pos, ts, bid_open, bid_high, bid_low, bid_close, spread_price, atr))
        return events

    def _process(self, pos: SimPosition, ts: datetime, o: float, h: float, low: float, c: float, spread: float,
                 atr: float) -> list[SimEvent]:
        events: list[SimEvent] = []
        slip = self.costs.slippage_points * pos.spec.point
        if pos.direction is Direction.BUY:
            open_, high, low_, close = o, h, low, c
        else:  # SELL closes at ask
            open_, high, low_, close = o + spread, h + spread, low + spread, c + spread
        d = pos.direction
        adverse_extreme = low_ if d is Direction.BUY else high
        favorable_extreme = high if d is Direction.BUY else low_

        def touched(level: float, extreme: float, adverse: bool) -> bool:
            if level <= 0:
                return False
            return (extreme - level) * d.sign <= 0 if adverse else (extreme - level) * d.sign >= 0

        # 1. Stop loss (checked first - conservative).
        if touched(pos.sl, adverse_extreme, adverse=True):
            gapped = (open_ - pos.sl) * d.sign < 0
            price = (open_ if gapped else pos.sl) - d.sign * slip
            events.append(self._close(pos, pos.volume, price, ts, "STOP_LOSS" if not pos.be_done else "PROTECTIVE_STOP"))
            return events
        # 2. Partial at TP1.
        if self.management.partial_enabled and not pos.partial_done and pos.tp1 > 0 \
                and touched(pos.tp1, favorable_extreme, adverse=False):
            vol = partial_volume(pos.managed(), self.management, pos.spec)
            pos.partial_done = True
            if vol > 0:
                events.append(self._close(pos, vol, pos.tp1, ts, "PARTIAL_TP1"))
        # 3. Final take profit.
        if touched(pos.tp, favorable_extreme, adverse=False):
            gapped = (open_ - pos.tp) * d.sign > 0
            price = open_ if gapped else pos.tp
            events.append(self._close(pos, pos.volume, price, ts, "TAKE_PROFIT"))
            return events
        # 4. Stop management on the close (applies from the next bar onward).
        state = pos.managed()
        for action in manage(state, close, atr, self.management, pos.spec):
            if action.new_sl is not None:
                pos.sl = action.new_sl
                pos.be_done = state.be_done
                events.append(SimEvent("MODIFY", pos.ticket, f"{action.reason} sl={action.new_sl}"))
            elif action.close_volume > 0:
                events.append(self._close(pos, action.close_volume, close, ts, action.reason))
        pos.partial_done = pos.partial_done or state.partial_done
        return events

    def apply_swap(self, ts: datetime, day: object) -> None:
        for pos in self.positions.values():
            if pos.last_swap_day is None:
                pos.last_swap_day = day
            elif day != pos.last_swap_day:
                per_lot = self.costs.swap_long_per_lot if pos.direction is Direction.BUY else self.costs.swap_short_per_lot
                pos.swap += per_lot * pos.volume
                pos.last_swap_day = day

    def close_all(self, prices: dict[str, tuple[float, float]], ts: datetime, reason: str) -> list[SimEvent]:
        events = []
        for pos in list(self.positions.values()):
            bid, ask = prices.get(pos.symbol, (0.0, 0.0))
            if bid <= 0:
                continue
            price = bid if pos.direction is Direction.BUY else ask
            events.append(self._close(pos, pos.volume, price, ts, reason))
        return events

    def floating(self, prices: dict[str, tuple[float, float]]) -> float:
        total = 0.0
        for pos in self.positions.values():
            bid, ask = prices.get(pos.symbol, (0.0, 0.0))
            if bid <= 0:
                continue
            price = bid if pos.direction is Direction.BUY else ask
            total += pnl_money(pos.spec, pos.direction, pos.entry, price, pos.volume) + pos.swap
        return total

    # ----------------------------------------------------------------- close
    def _close(self, pos: SimPosition, volume: float, price: float, ts: datetime, reason: str) -> SimEvent:
        volume = min(volume, pos.volume)
        fraction = volume / pos.volume if pos.volume > 0 else 1.0
        gross = pnl_money(pos.spec, pos.direction, pos.entry, price, volume)
        commission_share = pos.commission * fraction
        swap_share = pos.swap * fraction
        profit = gross - commission_share + swap_share
        pos.commission -= commission_share
        pos.swap -= swap_share
        pos.volume = round(pos.volume - volume, 8)
        pos.realized += profit
        final = pos.volume <= 1e-9
        risk_share = pos.risk_money * (volume / pos.initial_volume) if pos.initial_volume else pos.risk_money
        closed = ClosedTrade(pos.ticket, pos.symbol, pos.direction, pos.strategy, pos.entry, price, volume,
                             pos.opened_at, ts, profit, reason, risk_share, pos.session, pos.regime, pos.confidence,
                             final)
        if final:
            self.positions.pop(pos.ticket, None)
        return SimEvent("CLOSE" if final else "PARTIAL", pos.ticket, f"{reason} {volume}@{price}", closed)
