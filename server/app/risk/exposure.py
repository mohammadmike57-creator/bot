"""Per-position risk (money lost if every stop is hit from the current price)."""

from __future__ import annotations

import math

from app.domain import OpenPosition, SymbolSpec


def position_risk_money(position: OpenPosition, spec: SymbolSpec) -> float:
    """Remaining money at risk for one position.

    Measured from the current price to the stop: a stop already in profit has
    zero remaining risk. A position without a stop has unbounded risk (inf).
    """
    if position.sl <= 0:
        return math.inf
    reference = position.current_price if position.current_price > 0 else position.entry_price
    adverse = (reference - position.sl) * position.direction.sign
    if adverse <= 0:
        return 0.0
    return adverse * spec.money_per_price_unit(position.volume)


def notional_exposure(position: OpenPosition, spec: SymbolSpec) -> float:
    price = position.current_price if position.current_price > 0 else position.entry_price
    return position.volume * spec.contract_size * price * position.direction.sign
