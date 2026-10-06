from datetime import UTC, datetime, timedelta

from app.database.database import Database
from app.monitoring.alerts import Notifier
from app.risk.drawdown import EquityRiskState, trading_day_for, update_equity_state
from app.services.risk_state_service import RiskStateService
from tests.conftest import make_settings


def test_trading_day_rolls_at_17_new_york():
    before = datetime(2026, 10, 6, 20, 59, tzinfo=UTC)  # 16:59 NY (EDT)
    after = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # 17:00 NY
    assert trading_day_for(after) == trading_day_for(before) + timedelta(days=1)


def test_daily_loss_lock_and_new_day_reset():
    st = EquityRiskState()
    t0 = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
    update_equity_state(st, 100.0, t0, max_daily_loss=0.02, max_drawdown=0.08)
    chk = update_equity_state(st, 97.9, t0 + timedelta(hours=1), max_daily_loss=0.02, max_drawdown=0.08)
    assert chk.daily_loss_breached and st.daily_loss_locked
    # Recovering intraday does NOT unlock
    update_equity_state(st, 100.0, t0 + timedelta(hours=2), max_daily_loss=0.02, max_drawdown=0.08)
    assert st.daily_loss_locked
    # Next trading day resets the daily baseline
    update_equity_state(st, 99.0, t0 + timedelta(days=1), max_daily_loss=0.02, max_drawdown=0.08)
    assert not st.daily_loss_locked and st.day_start_equity == 99.0


def test_drawdown_is_sticky_across_days():
    st = EquityRiskState()
    t0 = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
    for i, eq in enumerate([100, 110, 105, 101.0]):
        update_equity_state(st, eq, t0 + timedelta(days=i), max_daily_loss=0.5, max_drawdown=0.08)
    assert st.drawdown_locked  # (110 - 101) / 110 = 8.2%
    update_equity_state(st, 115, t0 + timedelta(days=10), max_daily_loss=0.5, max_drawdown=0.08)
    assert st.drawdown_locked  # never silently restarts


def test_daily_loss_state_survives_restart(tmp_path):
    """Simulates a backend/EA restart: state is re-read from the database."""
    settings = make_settings(tmp_path)
    db = Database(f"sqlite:///{tmp_path / 'risk.db'}")
    db.create_all()
    now = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)
    svc = RiskStateService(db, settings, Notifier())
    svc.update_equity("123", 100.0, now)
    svc.update_equity("123", 97.5, now + timedelta(minutes=5))
    assert svc.equity_state("123").daily_loss_locked

    db2 = Database(f"sqlite:///{tmp_path / 'risk.db'}")  # "restart"
    svc2 = RiskStateService(db2, settings, Notifier())
    svc2.update_equity("123", 99.0, now + timedelta(minutes=10))  # same trading day
    st = svc2.equity_state("123")
    assert st.daily_loss_locked
    assert st.day_start_equity == 100.0


def test_kill_switch_requires_manual_reset(tmp_path):
    settings = make_settings(tmp_path)
    db = Database("sqlite://")
    db.create_all()
    svc = RiskStateService(db, settings, Notifier())
    ctl = svc.activate_kill_switch("test", None, "tester")
    assert ctl.kill_switch and ctl.safe_mode
    assert not ctl.close_positions  # default policy KEEP_POSITIONS
    assert svc.controls().kill_switch  # persists
    ctl = svc.reset(["kill_switch"], "tester", "", {})
    assert not ctl.kill_switch and not ctl.safe_mode
