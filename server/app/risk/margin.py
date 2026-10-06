"""Margin usage checks. Margin per lot is reported by MT5 (OrderCalcMargin)."""

from __future__ import annotations

from app.domain import SymbolSpec


def margin_usage(current_margin: float, equity: float) -> float:
    return current_margin / equity if equity > 0 else 1.0


def projected_margin_usage(current_margin: float, equity: float, spec: SymbolSpec, volume: float) -> float:
    return margin_usage(current_margin + spec.margin_per_lot * volume, equity)


def max_volume_for_margin(current_margin: float, equity: float, spec: SymbolSpec, max_usage: float) -> float:
    """Largest volume keeping margin usage <= max_usage (inf when margin/lot unknown)."""
    if spec.margin_per_lot <= 0:
        return float("inf")
    room = max_usage * equity - current_margin
    return max(0.0, room / spec.margin_per_lot)
