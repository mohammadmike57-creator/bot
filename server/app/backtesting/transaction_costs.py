"""Transaction-cost assumptions for backtests.

Spread comes from the bar data when available (MT5 exports the spread in
points per bar); otherwise ``fallback_spread_points`` is used. Commission and
slippage are explicit, conservative inputs - set them from your broker's
actual contract specification and fills, never leave them optimistic.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import SymbolSpec
from app.execution.simulator import CostModel


@dataclass(frozen=True)
class CostAssumptions:
    commission_per_lot: float = 0.0  # round trip, account currency
    slippage_points: float = 2.0
    fallback_spread_points: float = 30.0
    spread_multiplier: float = 1.0  # stress test: 1.5 = 50% wider spreads
    latency_bars: int = 1  # decision at bar close, fill at the next bar open
    swap_long_per_lot: float | None = None  # None -> use spec value if provided
    swap_short_per_lot: float | None = None


def cost_model(assumptions: CostAssumptions, spec: SymbolSpec, swap_long: float = 0.0, swap_short: float = 0.0) -> CostModel:
    return CostModel(
        commission_per_lot=assumptions.commission_per_lot,
        slippage_points=assumptions.slippage_points,
        swap_long_per_lot=assumptions.swap_long_per_lot if assumptions.swap_long_per_lot is not None else swap_long,
        swap_short_per_lot=assumptions.swap_short_per_lot if assumptions.swap_short_per_lot is not None else swap_short,
    )


def spread_price(spread_points: float | None, spec: SymbolSpec, a: CostAssumptions) -> float:
    pts = spread_points if spread_points and spread_points > 0 else a.fallback_spread_points
    return pts * a.spread_multiplier * spec.point
