"""Pre-trade validation of a concrete order against the broker spec.

Mirrors the EA-side checks (the EA re-validates independently).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Direction, SymbolSpec
from app.risk.position_sizing import normalize_volume


@dataclass(frozen=True)
class OrderCheck:
    ok: bool
    reason: str = ""


def validate_order(direction: Direction, volume: float, price: float, sl: float, tp: float, spec: SymbolSpec,
                   bid: float, ask: float) -> OrderCheck:
    if spec.validate():
        return OrderCheck(False, "INVALID_SYMBOL_SPEC")
    if not spec.trade_allowed:
        return OrderCheck(False, "SYMBOL_NOT_TRADABLE")
    if bid <= 0 or ask <= 0 or ask < bid:
        return OrderCheck(False, "INVALID_QUOTE")
    if volume <= 0 or volume < spec.volume_min - 1e-12 or volume > spec.volume_max + 1e-12:
        return OrderCheck(False, "INVALID_VOLUME")
    if abs(normalize_volume(volume, spec) - volume) > 1e-9:
        return OrderCheck(False, "VOLUME_NOT_ON_STEP")
    if sl <= 0:
        return OrderCheck(False, "STOP_LOSS_REQUIRED")
    min_gap = max(spec.stops_level, 0) * spec.point
    ref = bid if direction is Direction.BUY else ask  # stops trigger on the closing side
    if (ref - sl) * direction.sign <= min_gap:
        return OrderCheck(False, "INVALID_SL")
    if tp > 0 and (tp - ref) * direction.sign <= min_gap:
        return OrderCheck(False, "INVALID_TP")
    if price <= 0:
        return OrderCheck(False, "INVALID_PRICE")
    return OrderCheck(True)
