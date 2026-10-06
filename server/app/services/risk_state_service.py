"""Persisted risk state + global controls (kill switch, pause, safe mode).

Equity tracking is per account (``PAPER`` or the MT5 login). Controls live on
the ``GLOBAL`` row and apply to every account. Nothing here resets on restart:
the trading-day baseline changes only on a real trading-day rollover.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.config.settings import EmergencyClosePolicy, Settings
from app.database import repositories as repo
from app.database.database import Database
from app.database.models import RiskState, utcnow
from app.monitoring.alerts import Alert, AlertType, Notifier, Severity
from app.risk.drawdown import EquityCheck, EquityRiskState, update_equity_state
from app.risk.risk_engine import ControlFlags

GLOBAL = "GLOBAL"


@dataclass(frozen=True)
class ControlState:
    kill_switch: bool
    kill_reason: str
    close_positions: bool
    paused: bool
    safe_mode: bool
    safe_mode_reason: str

    def flags(self) -> ControlFlags:
        return ControlFlags(self.kill_switch, self.paused, self.safe_mode, self.safe_mode_reason)


def _to_equity_state(r: RiskState) -> EquityRiskState:
    return EquityRiskState(r.trading_day, r.day_start_equity, r.initial_equity, r.peak_equity, r.last_equity,
                           r.daily_loss_locked, r.drawdown_locked)


def _apply(r: RiskState, e: EquityRiskState) -> None:
    r.trading_day, r.day_start_equity, r.initial_equity = e.trading_day, e.day_start_equity, e.initial_equity
    r.peak_equity, r.last_equity = e.peak_equity, e.last_equity
    r.daily_loss_locked, r.drawdown_locked = e.daily_loss_locked, e.drawdown_locked
    r.updated_at = utcnow()


class RiskStateService:
    def __init__(self, db: Database, settings: Settings, notifier: Notifier):
        self.db = db
        self.settings = settings
        self.notifier = notifier

    # ------------------------------------------------------------------ reads
    def equity_state(self, login: str) -> EquityRiskState:
        with self.db.session() as s:
            return _to_equity_state(repo.get_risk_state(s, login))

    def controls(self) -> ControlState:
        with self.db.session() as s:
            g = repo.get_risk_state(s, GLOBAL)
            return ControlState(g.kill_switch, g.kill_reason, g.close_positions_requested, g.paused, g.safe_mode,
                                g.safe_mode_reason)

    # ---------------------------------------------------------------- updates
    def update_equity(self, login: str, equity: float, now: datetime | None = None) -> EquityCheck:
        now = now or utcnow()
        with self.db.session() as s:
            row = repo.get_risk_state(s, login)
            state = _to_equity_state(row)
            was_daily, was_dd = state.daily_loss_locked, state.drawdown_locked
            check = update_equity_state(
                state, equity, now, max_daily_loss=self.settings.max_daily_loss, max_drawdown=self.settings.max_drawdown,
                tz_name=self.settings.trading_day_timezone, rollover_hour=self.settings.trading_day_rollover_hour,
            )
            _apply(row, state)
            new_daily = state.daily_loss_locked and not was_daily
            new_dd = state.drawdown_locked and not was_dd
            if new_daily:
                repo.add_risk_event(s, "DAILY_LOSS_REACHED", f"daily loss {check.daily_loss_fraction:.2%}",
                                    "CRITICAL", login)
            if new_dd:
                repo.add_risk_event(s, "MAX_DRAWDOWN_REACHED", f"drawdown {check.drawdown_fraction:.2%}", "CRITICAL",
                                    login)
            if (new_daily or new_dd) and self.settings.emergency_close_policy is EmergencyClosePolicy.CLOSE_ALL:
                repo.get_risk_state(s, GLOBAL).close_positions_requested = True
        if new_daily:
            self.notifier.notify(Alert(AlertType.DAILY_LOSS_REACHED,
                                       f"{login}: daily loss limit reached - new trades stopped", Severity.CRITICAL))
        if new_dd:
            self.notifier.notify(Alert(AlertType.DRAWDOWN_REACHED,
                                       f"{login}: max drawdown reached - manual reset required", Severity.CRITICAL))
        return check

    def activate_kill_switch(self, reason: str, close_positions: bool | None, actor: str) -> ControlState:
        close = close_positions if close_positions is not None else \
            self.settings.emergency_close_policy is EmergencyClosePolicy.CLOSE_ALL
        with self.db.session() as s:
            g = repo.get_risk_state(s, GLOBAL)
            g.kill_switch = True
            g.kill_reason = reason
            g.kill_at = utcnow()
            g.close_positions_requested = g.close_positions_requested or close
            g.safe_mode = True
            g.safe_mode_reason = f"KILL_SWITCH: {reason}"
            repo.add_risk_event(s, "KILL_SWITCH", reason, "CRITICAL", details={"actor": actor, "close": close})
            repo.add_system_event(s, "risk", "KILL_SWITCH_ON", reason, "CRITICAL", {"actor": actor})
        self.notifier.notify(Alert(AlertType.KILL_SWITCH, f"EMERGENCY STOP by {actor}: {reason}", Severity.CRITICAL,
                                   {"close_positions": close}))
        return self.controls()

    def set_paused(self, paused: bool, actor: str) -> ControlState:
        with self.db.session() as s:
            g = repo.get_risk_state(s, GLOBAL)
            g.paused = paused
            repo.add_system_event(s, "control", "PAUSED" if paused else "RESUMED", actor)
        return self.controls()

    def set_safe_mode(self, reason: str) -> None:
        with self.db.session() as s:
            g = repo.get_risk_state(s, GLOBAL)
            if g.safe_mode:
                return
            g.safe_mode = True
            g.safe_mode_reason = reason
            repo.add_risk_event(s, "SAFE_MODE", reason, "CRITICAL")
        self.notifier.notify(Alert(AlertType.SAFE_MODE, f"SAFE MODE: {reason}", Severity.CRITICAL))

    def acknowledge_positions_closed(self) -> None:
        with self.db.session() as s:
            repo.get_risk_state(s, GLOBAL).close_positions_requested = False

    def reset(self, items: list[str], actor: str, note: str, equities: dict[str, float]) -> ControlState:
        """Explicit manual reset. Each item is logged as a risk event."""
        with self.db.session() as s:
            g = repo.get_risk_state(s, GLOBAL)
            if "kill_switch" in items:
                g.kill_switch, g.kill_reason, g.kill_at = False, "", None
                g.close_positions_requested = False
                if g.safe_mode_reason.startswith("KILL_SWITCH"):
                    g.safe_mode, g.safe_mode_reason = False, ""
            if "safe_mode" in items and not g.kill_switch:
                g.safe_mode, g.safe_mode_reason = False, ""
            for login, equity in equities.items():
                row = repo.get_risk_state(s, login)
                if "daily_loss" in items:
                    row.daily_loss_locked = False
                    row.day_start_equity = equity
                if "drawdown" in items:
                    row.drawdown_locked = False
                    row.peak_equity = equity
            repo.add_risk_event(s, "MANUAL_RESET", ",".join(items), "WARNING", details={"actor": actor, "note": note})
            repo.add_system_event(s, "risk", "MANUAL_RESET", f"{actor}: {','.join(items)} {note}", "WARNING")
        return self.controls()
