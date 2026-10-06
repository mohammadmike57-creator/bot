"""EA data ingestion: heartbeat, account (+ symbol specs) and position sync."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_container, require_ea, require_reader
from app.api.schemas import AccountIn, AccountOut, HeartbeatIn, HeartbeatOut, PositionsSyncIn
from app.database import repositories as repo
from app.services.container import Container
from app.services.ingest_service import IngestService
from app.services.security import Principal
from app.sessions.timezone import utcnow

router = APIRouter()


@router.post("/v1/heartbeat", response_model=HeartbeatOut)
def heartbeat(body: HeartbeatIn, c: Container = Depends(get_container),
              _: Principal = Depends(require_ea)) -> HeartbeatOut:
    return IngestService(c).heartbeat(body, utcnow())


@router.post("/v1/account", response_model=AccountOut)
def account(body: AccountIn, c: Container = Depends(get_container), _: Principal = Depends(require_ea)) -> Any:
    return IngestService(c).account(body, utcnow())


@router.post("/v1/position")
def positions(body: PositionsSyncIn, c: Container = Depends(get_container),
              _: Principal = Depends(require_ea)) -> dict[str, int]:
    return IngestService(c).positions(body, utcnow())


@router.get("/v1/account")
def get_account(c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> dict[str, Any]:
    with c.db.session() as s:
        out: dict[str, Any] = {}
        for label, acc in (("mt5", repo.latest_mt5_account(s)), ("paper", repo.get_account(s, repo.PAPER_LOGIN))):
            out[label] = None if acc is None else {
                "login": acc.login, "server": acc.server, "company": acc.company, "currency": acc.currency,
                "leverage": acc.leverage, "trade_mode": acc.trade_mode, "margin_mode": acc.margin_mode,
                "balance": acc.balance, "equity": acc.equity, "margin": acc.margin, "free_margin": acc.free_margin,
                "margin_level": acc.margin_level, "updated_at": acc.updated_at.isoformat(),
            }
        out["symbols"] = [{
            "broker_symbol": r.broker_symbol, "logical_symbol": r.logical_symbol, "digits": r.digits,
            "point": r.point, "tick_size": r.tick_size, "tick_value": r.tick_value, "contract_size": r.contract_size,
            "volume_min": r.volume_min, "volume_max": r.volume_max, "volume_step": r.volume_step,
            "stops_level": r.stops_level, "margin_per_lot": r.margin_per_lot, "trade_mode": r.trade_mode,
            "spread_points": r.spread_points, "updated_at": r.updated_at.isoformat(),
        } for r in repo.symbol_specs(s)]
        return out
