"""Position sizing from equity, risk %, stop distance and the broker contract spec.

lot_size = risk_money / money_loss_per_lot_at_stop, then normalised DOWN to the
broker's volume step. If the result is below the broker minimum the trade is
rejected - risk is never increased to reach the minimum volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain import SymbolSpec

MIN_VOLUME_MESSAGE = "Minimum broker volume exceeds allowed account risk."


@dataclass(frozen=True)
class SizingResult:
    ok: bool
    volume: float
    risk_money: float  # money lost at SL for ``volume`` (incl. commission)
    loss_per_lot: float
    reason: str = ""
    min_volume_risk: float = 0.0  # what the broker minimum would risk


def loss_per_lot(spec: SymbolSpec, entry: float, stop_loss: float, commission_per_lot: float = 0.0) -> float:
    """Money lost by 1.0 lot if price travels from entry to stop."""
    distance = abs(entry - stop_loss)
    ticks = distance / spec.tick_size
    return ticks * spec.loss_tick_value() + max(commission_per_lot, 0.0)


def normalize_volume(volume: float, spec: SymbolSpec) -> float:
    """Round DOWN to the volume step and clamp to the broker max. Returns 0 if below min."""
    if volume <= 0:
        return 0.0
    steps = math.floor(volume / spec.volume_step + 1e-9)
    normalized = steps * spec.volume_step
    normalized = min(normalized, spec.volume_max)
    decimals = max(0, -int(math.floor(math.log10(spec.volume_step)))) if spec.volume_step < 1 else 0
    normalized = round(normalized, decimals + 2)
    if normalized + 1e-12 < spec.volume_min:
        return 0.0
    return normalized


def calculate_position_size(
    *,
    equity: float,
    risk_fraction: float,
    entry: float,
    stop_loss: float,
    spec: SymbolSpec,
    commission_per_lot: float = 0.0,
    max_volume: float | None = None,
) -> SizingResult:
    problems = spec.validate()
    if problems:
        return SizingResult(False, 0.0, 0.0, 0.0, f"INVALID_SYMBOL_SPEC: {'; '.join(problems)}")
    if equity <= 0:
        return SizingResult(False, 0.0, 0.0, 0.0, "NON_POSITIVE_EQUITY")
    if not 0 < risk_fraction < 1:
        return SizingResult(False, 0.0, 0.0, 0.0, "INVALID_RISK_FRACTION")
    if entry <= 0 or stop_loss <= 0 or entry == stop_loss:
        return SizingResult(False, 0.0, 0.0, 0.0, "INVALID_STOP_LOSS")

    per_lot = loss_per_lot(spec, entry, stop_loss, commission_per_lot)
    if per_lot <= 0:
        return SizingResult(False, 0.0, 0.0, 0.0, "INVALID_LOSS_PER_LOT")

    risk_money = equity * risk_fraction
    raw_volume = risk_money / per_lot
    if max_volume is not None:
        raw_volume = min(raw_volume, max_volume)
    volume = normalize_volume(raw_volume, spec)
    min_risk = spec.volume_min * per_lot
    if volume <= 0:
        return SizingResult(
            False,
            0.0,
            0.0,
            per_lot,
            f"MIN_VOLUME_EXCEEDS_RISK: {MIN_VOLUME_MESSAGE} "
            f"(budget={risk_money:.2f}, min_volume_risk={min_risk:.2f})",
            min_volume_risk=min_risk,
        )
    return SizingResult(True, volume, volume * per_lot, per_lot, "", min_volume_risk=min_risk)
