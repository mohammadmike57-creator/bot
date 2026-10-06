"""Per-position trade management: tighten, break-even, partial close, trailing.

Pure logic shared by the paper broker and the backtester; the EA implements
the same rules in MQL5 (TrailingStop.mqh) so open trades stay managed even
when the backend is offline.

Invariant: a stop may only move in the direction that protects the position.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Direction, SymbolSpec
from app.risk.position_sizing import normalize_volume


@dataclass(frozen=True)
class ManagementParams:
    tighten_trigger_r: float = 0.8
    tighten_to_r: float = -0.5
    be_trigger_r: float = 1.0
    be_offset_r: float = 0.05
    trail_start_r: float = 1.0
    trail_atr_mult: float = 1.0
    tp1_r: float = 1.0
    tp1_close_fraction: float = 0.5
    partial_enabled: bool = True


@dataclass
class ManagedState:
    direction: Direction
    entry: float
    initial_sl: float
    sl: float
    volume: float
    initial_volume: float
    partial_done: bool = False
    be_done: bool = False

    @property
    def initial_risk(self) -> float:
        return abs(self.entry - self.initial_sl)

    def r_at(self, price: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        return (price - self.entry) * self.direction.sign / self.initial_risk


@dataclass(frozen=True)
class ManagementAction:
    new_sl: float | None = None
    close_volume: float = 0.0
    reason: str = ""


def is_tighter(direction: Direction, candidate: float, current: float) -> bool:
    return (candidate - current) * direction.sign > 0


def partial_volume(state: ManagedState, params: ManagementParams, spec: SymbolSpec) -> float:
    """Volume to close at TP1; 0 if the split would violate the broker minimum."""
    if not params.partial_enabled or params.tp1_close_fraction <= 0:
        return 0.0
    close = normalize_volume(state.initial_volume * params.tp1_close_fraction, spec)
    remaining = round(state.volume - close, 8)
    if close <= 0 or remaining + 1e-12 < spec.volume_min:
        return 0.0
    return close


def manage(state: ManagedState, price: float, atr: float, params: ManagementParams, spec: SymbolSpec) -> list[ManagementAction]:
    """Return management actions for the current (closing-side) price.

    ``price`` is the price the position would close at (bid for BUY, ask for SELL).
    """
    actions: list[ManagementAction] = []
    if state.initial_risk <= 0:
        return actions
    d = state.direction
    r = state.r_at(price)
    risk = state.initial_risk

    # Partial profit at TP1 (once).
    if not state.partial_done and r >= params.tp1_r:
        vol = partial_volume(state, params, spec)
        state.partial_done = True  # even if skipped (min volume), don't retry every tick
        if vol > 0:
            actions.append(ManagementAction(close_volume=vol, reason=f"PARTIAL_TP1@{params.tp1_r}R"))

    candidates: list[tuple[float, str]] = []
    if r >= params.tighten_trigger_r:
        candidates.append((state.entry + d.sign * params.tighten_to_r * risk, "TIGHTEN"))
    if r >= params.be_trigger_r:
        candidates.append((state.entry + d.sign * params.be_offset_r * risk, "BREAK_EVEN"))
    if r >= params.trail_start_r and atr > 0:
        candidates.append((price - d.sign * params.trail_atr_mult * atr, "TRAIL_ATR"))
    if not candidates:
        return actions

    # Broker constraint: a stop must stay at least stops_level away from price.
    # Candidates that violate it are dropped; the most protective valid one wins.
    min_gap = max(max(spec.stops_level, spec.freeze_level, 0) * spec.point, spec.tick_size)
    best_sl, best_reason = state.sl, ""
    for level, reason in candidates:
        level = round(round(level / spec.tick_size) * spec.tick_size, spec.digits)
        if (price - level) * d.sign < min_gap:
            continue
        if is_tighter(d, level, best_sl) and abs(level - best_sl) >= spec.tick_size / 2:
            best_sl, best_reason = level, reason
    if not best_reason:
        return actions
    state.sl = best_sl
    if best_reason == "BREAK_EVEN" or (best_sl - state.entry) * d.sign >= 0:
        state.be_done = True
    actions.append(ManagementAction(new_sl=best_sl, reason=best_reason))
    return actions
