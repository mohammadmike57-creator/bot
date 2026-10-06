"""Portfolio-level exposure aggregation (for risk decisions and the dashboard)."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from app.domain import OpenPosition, SymbolSpec
from app.risk.exposure import notional_exposure, position_risk_money


@dataclass
class ExposureReport:
    total_risk: float = 0.0
    total_risk_pct: float = 0.0
    unrealized_pnl: float = 0.0
    by_symbol: dict[str, float] = field(default_factory=dict)
    by_strategy: dict[str, float] = field(default_factory=dict)
    by_direction: dict[str, float] = field(default_factory=dict)
    net_notional: dict[str, float] = field(default_factory=dict)
    positions_without_stop: int = 0
    hedged_symbols: list[str] = field(default_factory=list)


def aggregate(positions: list[OpenPosition], specs: dict[str, SymbolSpec], equity: float) -> ExposureReport:
    rep = ExposureReport()
    by_symbol: dict[str, float] = defaultdict(float)
    by_strategy: dict[str, float] = defaultdict(float)
    by_direction: dict[str, float] = defaultdict(float)
    notional: dict[str, float] = defaultdict(float)
    directions: dict[str, set[str]] = defaultdict(set)
    for p in positions:
        spec = specs.get(p.symbol)
        rep.unrealized_pnl += p.profit
        directions[p.symbol].add(p.direction.value)
        if spec is None:
            rep.positions_without_stop += 1
            continue
        risk = position_risk_money(p, spec)
        if math.isinf(risk):
            rep.positions_without_stop += 1
            continue
        rep.total_risk += risk
        by_symbol[p.symbol] += risk
        by_strategy[p.strategy or "UNKNOWN"] += risk
        by_direction[p.direction.value] += risk
        notional[p.symbol] += notional_exposure(p, spec)
    rep.total_risk_pct = rep.total_risk / equity if equity > 0 else 0.0
    rep.by_symbol, rep.by_strategy, rep.by_direction = dict(by_symbol), dict(by_strategy), dict(by_direction)
    rep.net_notional = dict(notional)
    rep.hedged_symbols = sorted(s for s, d in directions.items() if len(d) == 2)
    return rep
