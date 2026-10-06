"""Emergency stop, manual risk reset and pause/resume controls (admin only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_container, require_admin
from app.api.schemas import ControlIn, KillSwitchIn, ResetRiskIn
from app.database import repositories as repo
from app.services.container import Container
from app.services.security import Principal
from app.sessions.timezone import utcnow

router = APIRouter()


def _controls(c: Container) -> dict[str, Any]:
    ctl = c.risk_state.controls()
    return {"kill_switch": ctl.kill_switch, "kill_reason": ctl.kill_reason, "close_positions": ctl.close_positions,
            "paused": ctl.paused, "safe_mode": ctl.safe_mode, "safe_mode_reason": ctl.safe_mode_reason}


@router.post("/v1/kill-switch")
def kill_switch(body: KillSwitchIn, c: Container = Depends(get_container),
                who: Principal = Depends(require_admin)) -> dict[str, Any]:
    ctl = c.risk_state.activate_kill_switch(body.reason, body.close_positions, who.subject)
    if ctl.close_positions and c.paper.sim.positions:
        c.paper.close_all("KILL_SWITCH_CLOSE", utcnow())
    return _controls(c)


@router.post("/v1/reset-risk")
def reset_risk(body: ResetRiskIn, c: Container = Depends(get_container),
               who: Principal = Depends(require_admin)) -> dict[str, Any]:
    with c.db.session() as s:
        equities = {}
        paper = repo.get_account(s, repo.PAPER_LOGIN)
        if paper:
            equities[paper.login] = paper.equity
        mt5 = repo.latest_mt5_account(s)
        if mt5:
            equities[mt5.login] = mt5.equity
    c.risk_state.reset(list(body.items), who.subject, body.note, equities)
    return _controls(c)


@router.post("/v1/control")
def control(body: ControlIn, c: Container = Depends(get_container),
            who: Principal = Depends(require_admin)) -> dict[str, Any]:
    c.risk_state.set_paused(body.action == "PAUSE", who.subject)
    return _controls(c)


@router.get("/v1/controls")
def controls(c: Container = Depends(get_container), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    return _controls(c)
