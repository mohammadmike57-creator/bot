"""Health, status and observability endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_reader
from app.database import repositories as repo
from app.monitoring.health import system_status
from app.services.container import Container
from app.services.security import Principal
from app.sessions.timezone import utcnow

router = APIRouter()


@router.get("/health")
def health(c: Container = Depends(get_container)) -> dict[str, Any]:
    """Unauthenticated liveness probe (used by Render). Reveals no account data."""
    db_ok = c.db.ping()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok,
            "trading_mode": c.settings.effective_trading_mode.value, "time": utcnow().isoformat()}


@router.get("/v1/status")
def status(c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> dict[str, Any]:
    return system_status(c, utcnow())


@router.get("/v1/metrics")
def get_metrics(c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> dict[str, Any]:
    return c.metrics.snapshot()


@router.get("/v1/logs")
def decision_logs(limit: int = Query(100, ge=1, le=1000), decision: str | None = Query(None, max_length=16),
                  c: Container = Depends(get_container), _: Principal = Depends(require_reader)) -> list[dict[str, Any]]:
    with c.db.session() as s:
        return [{"ts": r.ts.isoformat(), "symbol": r.symbol, "decision": r.decision, "reason": r.reason,
                 "signal_id": r.signal_id, "line": r.line} for r in repo.trade_logs(s, limit, decision)]


@router.get("/v1/events")
def events(limit: int = Query(100, ge=1, le=500), c: Container = Depends(get_container),
           _: Principal = Depends(require_reader)) -> dict[str, Any]:
    with c.db.session() as s:
        risk = [{"ts": e.ts.isoformat(), "type": e.type, "severity": e.severity, "reason": e.reason,
                 "symbol": e.symbol} for e in repo.risk_events(s, limit)]
        system = [{"ts": e.ts.isoformat(), "component": e.component, "event": e.event, "level": e.level,
                   "message": e.message} for e in repo.system_events(s, limit)]
    alerts = [{"ts": a.created_at.isoformat(), "type": a.type.value, "severity": a.severity.value,
               "message": a.message} for a in reversed(c.notifier.history[-limit:])]
    return {"risk_events": risk, "system_events": system, "alerts": alerts}
