"""POST /v1/signal - the EA asks for a decision once per closed candle."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_container, require_ea, require_reader
from app.api.schemas import SignalRequest, SignalResponse
from app.database import repositories as repo
from app.services.container import Container
from app.services.security import Principal
from app.services.signal_service import SignalService, UnknownSymbolError
from app.sessions.timezone import utcnow

router = APIRouter()


@router.post("/v1/signal", response_model=SignalResponse)
def signal(body: SignalRequest, request: Request, c: Container = Depends(get_container),
           _: Principal = Depends(require_ea)) -> SignalResponse:
    with c.metrics.timer("signal_latency"):
        try:
            resp = SignalService(c).process(body, utcnow(), getattr(request.state, "request_id", ""))
        except UnknownSymbolError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    with c.db.session() as s:
        repo.get_ea_status(s, body.account_login).last_signal_at = utcnow()
    return resp


@router.get("/v1/signals")
def recent_signals(limit: int = Query(100, ge=1, le=1000), c: Container = Depends(get_container),
                   _: Principal = Depends(require_reader)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [{
            "signal_id": r.signal_id, "created_at": r.created_at.isoformat(), "symbol": r.symbol,
            "logical_symbol": r.logical_symbol, "action": r.action, "strategy": r.strategy,
            "confidence": r.confidence, "regime": r.regime, "session": r.session, "status": r.status,
            "reason": r.reason, "volume": r.volume, "risk_percent": r.risk_percent, "trading_mode": r.trading_mode,
        } for r in repo.recent_signals(s, limit)]
