"""Per-position risk measures.

* ``position_risk_money`` - CAPITAL at risk: money lost versus the position's
  cost basis if the stop is hit, measured from the WORSE of entry and current
  price. A position whose stop is at break-even or better risks 0, which frees
  risk budget for new trades. Used for per-trade/symbol/strategy/portfolio and
  correlation limits.
* ``giveback_to_stop`` - fall in EQUITY if the stop is hit from the current
  price (includes giving back floating profit). Used for the daily-loss limit.
"""

from __future__ import annotations

import math

from app.domain import OpenPosition, SymbolSpec


def _adverse_money(position: OpenPosition, spec: SymbolSpec, reference: float) -> float:
    if position.sl <= 0:
        return math.inf
    adverse = (reference - position.sl) * position.direction.sign
    return 0.0 if adverse <= 0 else adverse * spec.money_per_price_unit(position.volume)


def position_risk_money(position: OpenPosition, spec: SymbolSpec) -> float:
    current = position.current_price if position.current_price > 0 else position.entry_price
    worse = min(current, position.entry_price) if position.direction.sign > 0 else max(current, position.entry_price)
    return _adverse_money(position, spec, worse)


def giveback_to_stop(position: OpenPosition, spec: SymbolSpec) -> float:
    current = position.current_price if position.current_price > 0 else position.entry_price
    return _adverse_money(position, spec, current)


def notional_exposure(position: OpenPosition, spec: SymbolSpec) -> float:
    price = position.current_price if position.current_price > 0 else position.entry_price
    return position.volume * spec.contract_size * price * position.direction.sign
