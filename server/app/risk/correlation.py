"""Correlated-exposure evaluation.

Two positions add to the same hidden concentration when their instruments are
strongly correlated *in the direction held*: long gold + long US30 with
corr=+0.7 are concentrated; long gold + short US30 with corr=+0.7 offset.
"""

from __future__ import annotations

from collections.abc import Callable

from app.domain import Direction


def correlated_risk(
    symbol: str,
    direction: Direction,
    open_risks: list[tuple[str, Direction, float]],
    correlation: Callable[[str, str], float],
    threshold: float,
) -> float:
    """Sum of open risk that moves together with a new (symbol, direction) trade."""
    total = 0.0
    for other_symbol, other_dir, risk in open_risks:
        rho = 1.0 if other_symbol == symbol else correlation(symbol, other_symbol)
        effective = rho * direction.sign * other_dir.sign
        if effective >= threshold:
            total += risk
    return total
