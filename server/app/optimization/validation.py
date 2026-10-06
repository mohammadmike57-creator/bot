"""Robustness-oriented objective. Never "maximise historical profit".

The score rewards profit factor, expectancy, risk-adjusted return and
return/drawdown, and penalises high drawdown, few trades, and instability.
"""

from __future__ import annotations

import math
from typing import Any


def _num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def robust_score(m: dict[str, Any], min_trades: int = 30, max_dd: float = 0.15) -> float:
    trades = int(m.get("trades", 0) or 0)
    if trades == 0:
        return -10.0
    pf = min(_num(m.get("profit_factor"), 0.0), 3.0)  # cap: PF 10 on 12 trades is noise
    exp_r = _num(m.get("avg_r"))
    sharpe = max(min(_num(m.get("sharpe")), 3.0), -3.0)
    sortino = max(min(_num(m.get("sortino")), 4.0), -4.0)
    rdd = max(min(_num(m.get("return_over_drawdown")), 5.0), -5.0)
    dd = _num(m.get("max_drawdown"))
    score = 1.0 * (pf - 1.0) + 2.0 * exp_r + 0.3 * sharpe + 0.2 * sortino + 0.2 * rdd
    if trades < min_trades:
        score -= 2.0 * (1 - trades / min_trades)  # statistically weak
    if dd > max_dd:
        score -= 5.0 * (dd - max_dd) / max_dd
    return round(score, 6)


def degradation(in_sample: dict[str, Any], out_of_sample: dict[str, Any]) -> float:
    """Fractional drop from IS to OOS score (large -> likely overfit)."""
    a, b = robust_score(in_sample), robust_score(out_of_sample)
    if a <= 0:
        return 0.0
    return max(0.0, (a - b) / abs(a))


def stability_penalty(neighbour_scores: list[float], center: float) -> float:
    """Penalty for parameter sensitivity: neighbours should score close to the centre."""
    if not neighbour_scores:
        return 0.0
    worst_drop = max(0.0, center - min(neighbour_scores))
    spread = max(neighbour_scores) - min(neighbour_scores)
    return 0.5 * worst_drop + 0.25 * spread
