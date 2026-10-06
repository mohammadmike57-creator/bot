"""Live/paper performance from persisted trades and equity snapshots."""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.backtesting.metrics import TradeResult, compute_metrics, drawdown_series
from app.database import repositories as repo
from app.database.database import Database
from app.database.models import Position


def _to_result(p: Position) -> TradeResult:
    return TradeResult(
        ticket=p.ticket, symbol=p.logical_symbol or p.symbol, strategy=p.strategy or "UNKNOWN",
        direction=p.direction, session=p.session or "UNKNOWN", regime=p.regime or "UNKNOWN",
        opened_at=p.opened_at, closed_at=p.closed_at or p.updated_at, profit=p.profit, risk_money=p.risk_money,
    )


def performance(db: Database, source: str, login: str, initial_equity: float) -> dict[str, Any]:
    with db.session() as s:
        closed = list(reversed(repo.closed_positions(s, source, limit=10000)))
        curve = repo.equity_curve(s, login)
    trades = [_to_result(p) for p in closed]
    times = [e.ts for e in curve]
    values = [e.equity for e in curve]
    base = values[0] if values else initial_equity
    metrics = compute_metrics(trades, times, values, base)
    dd = drawdown_series(pd.Series(values, index=pd.to_datetime(times, utc=True))) if values else pd.Series(dtype=float)
    metrics["equity_curve"] = [{"t": t.isoformat(), "equity": round(v, 2)} for t, v in zip(times, values, strict=True)]
    metrics["drawdown_curve"] = [{"t": t.isoformat(), "dd": round(float(x), 4)} for t, x in zip(times, dd.to_numpy(), strict=True)]
    metrics["source"] = source
    return metrics
