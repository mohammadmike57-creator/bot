"""Performance metrics, equity and drawdown curves, breakdowns."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_container, require_reader
from app.database import repositories as repo
from app.services.container import Container
from app.services.performance_service import performance as compute_performance
from app.services.security import Principal

router = APIRouter()


@router.get("/v1/performance")
def performance(source: str = Query("PAPER", pattern="^(MT5|PAPER)$"), c: Container = Depends(get_container),
                _: Principal = Depends(require_reader)) -> dict[str, Any]:
    if source == "PAPER":
        login = repo.PAPER_LOGIN
    else:
        with c.db.session() as s:
            acc = repo.latest_mt5_account(s)
            login = acc.login if acc else ""
    return compute_performance(c.db, source, login, c.settings.initial_equity)
