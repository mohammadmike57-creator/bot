"""Portfolio exposure, risk usage and correlation."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_reader
from app.services.container import Container
from app.services.security import Principal

router = APIRouter()


@router.get("/v1/portfolio")
def portfolio(source: str = Query("PAPER", pattern="^(MT5|PAPER)$"), c: Container = Depends(get_container),
              _: Principal = Depends(require_reader)) -> dict[str, Any]:
    snap = c.portfolio.snapshot(source)
    eq = snap.account.equity
    exp = snap.exposure
    symbols = sorted({*snap.specs.keys(), *c.resolver.logical_symbols()})
    return {
        "source": source,
        "equity": eq,
        "open_positions": len(snap.positions),
        "total_risk": round(exp.total_risk, 2),
        "total_risk_pct": round(exp.total_risk_pct, 5),
        "unrealized_pnl": round(exp.unrealized_pnl, 2),
        "risk_by_symbol": {k: round(v, 2) for k, v in exp.by_symbol.items()},
        "risk_by_strategy": {k: round(v, 2) for k, v in exp.by_strategy.items()},
        "risk_by_direction": {k: round(v, 2) for k, v in exp.by_direction.items()},
        "net_notional": {k: round(v, 2) for k, v in exp.net_notional.items()},
        "hedged_symbols": exp.hedged_symbols,
        "positions_without_stop": exp.positions_without_stop,
        "limits": asdict(c.limits),
        "correlation": c.correlation.matrix(symbols),
        "margin_usage": round(snap.account.margin / eq, 4) if eq > 0 else None,
    }
