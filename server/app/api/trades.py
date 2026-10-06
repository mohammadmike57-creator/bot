"""Trade events from the EA and the trade table for the dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_ea, require_reader
from app.api.schemas import TradeEventIn
from app.database import repositories as repo
from app.services.container import Container
from app.services.ingest_service import IngestService
from app.services.security import Principal
from app.sessions.timezone import utcnow

router = APIRouter()


@router.post("/v1/trade")
def trade_event(body: TradeEventIn, c: Container = Depends(get_container),
                _: Principal = Depends(require_ea)) -> dict[str, Any]:
    return IngestService(c).trade_event(body, utcnow())


@router.get("/v1/trades")
def trades(status: str | None = Query(None, pattern="^(open|closed|OPEN|CLOSED)$"),
           source: str | None = Query(None, pattern="^(MT5|PAPER|mt5|paper)$"),
           limit: int = Query(200, ge=1, le=2000), c: Container = Depends(get_container),
           _: Principal = Depends(require_reader)) -> list[dict[str, Any]]:
    now = utcnow()
    with c.db.session() as s:
        rows = repo.positions(s, status, source, limit)
        out = []
        for p in rows:
            end = p.closed_at or now
            out.append({
                "ticket": p.ticket, "source": p.source, "symbol": p.symbol, "logical_symbol": p.logical_symbol,
                "strategy": p.strategy, "direction": p.direction, "entry": p.entry_price,
                "current": p.close_price if p.status == "CLOSED" else p.current_price, "sl": p.sl, "tp": p.tp,
                "lot": p.volume if p.status == "OPEN" else p.initial_volume, "risk": round(p.risk_money, 2),
                "profit": round(p.profit, 2), "r_multiple": p.r_multiple,
                "duration_minutes": round((end - p.opened_at).total_seconds() / 60, 1),
                "confidence": p.confidence, "session": p.session, "regime": p.regime, "status": p.status,
                "close_reason": p.close_reason, "opened_at": p.opened_at.isoformat(),
                "closed_at": p.closed_at.isoformat() if p.closed_at else None, "magic": p.magic,
                "be_applied": p.be_applied, "partial_done": p.partial_done,
            })
        return out
