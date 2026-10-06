"""Economic calendar and per-symbol news blackout status."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_reader
from app.news.news_filter import is_relevant
from app.services.container import Container
from app.services.security import Principal
from app.sessions.timezone import utcnow

router = APIRouter()


@router.get("/v1/news")
def news(hours: int = Query(48, ge=1, le=168), c: Container = Depends(get_container),
         _: Principal = Depends(require_reader)) -> dict[str, Any]:
    now = utcnow()
    c.calendar.refresh_if_due(now)
    snap = c.calendar.snapshot(now)
    symbols = c.resolver.logical_symbols() or ["XAUUSD", "OIL", "US30"]
    upcoming = [e for e in c.calendar.events_between(now - timedelta(hours=1), now + timedelta(hours=hours))
                if e.importance in ("HIGH", "MEDIUM")]
    return {
        "status": snap.status.value,
        "provider": c.calendar.provider.name if c.calendar.provider else "none",
        "provider_error": c.provider_error,
        "last_success": snap.last_success.isoformat() if snap.last_success else None,
        "last_error": snap.last_error,
        "blackout": {sym: c.news_filter.check(sym, now).reason for sym in symbols},
        "events": [{
            "event": e.event, "currency": e.currency, "country": e.country, "importance": e.importance,
            "scheduled_at": e.scheduled_at.isoformat(), "previous": e.previous, "forecast": e.forecast,
            "actual": e.actual, "relevant_to": [s for s in symbols if is_relevant(e, s)],
        } for e in upcoming[:300]],
    }
