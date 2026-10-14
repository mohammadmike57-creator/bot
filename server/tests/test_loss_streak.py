"""Losing-streak protection: smaller size after losses, pause after a streak, never bigger."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.database import repositories as repo
from app.risk.drawdown import (
    EquityRiskState,
    LossStreakPolicy,
    in_cooldown,
    loss_streak_scale,
    record_trade_result,
)
from app.risk.limits import RiskLimits
from app.risk.risk_engine import RiskEngine
from tests.conftest import FIXED_NOW
from tests.test_risk_engine import SPECS, account, proposal, state

P = LossStreakPolicy(reduce_after=2, reduced_scale=0.5, cooldown_after=3, cooldown_minutes=120)


def test_streak_counts_losses_and_resets_on_win():
    st = EquityRiskState()
    record_trade_result(st, -5, FIXED_NOW, P)
    record_trade_result(st, 0.0, FIXED_NOW, P)  # scratch trade: unchanged
    record_trade_result(st, -5, FIXED_NOW, P)
    assert st.consecutive_losses == 2 and loss_streak_scale(st, P) == 0.5
    record_trade_result(st, 3, FIXED_NOW, P)
    assert st.consecutive_losses == 0 and loss_streak_scale(st, P) == 1.0


def test_cooldown_after_third_loss_and_every_third_after():
    st = EquityRiskState()
    started = [record_trade_result(st, -1, FIXED_NOW, P) for _ in range(6)]
    assert started == [False, False, True, False, False, True]
    assert in_cooldown(st, FIXED_NOW + timedelta(minutes=119))
    assert not in_cooldown(st, FIXED_NOW + timedelta(minutes=121))


def test_engine_rejects_during_cooldown_and_halves_risk_after():
    eng = RiskEngine(RiskLimits(loss_streak=P), lambda a, b: 0.0)
    st = state()
    for _ in range(3):
        record_trade_result(st, -10, FIXED_NOW, P)
    d = eng.evaluate(proposal(), account(), [], SPECS, st, now=FIXED_NOW + timedelta(minutes=30))
    assert not d.approved and d.reason.startswith("LOSS_STREAK_COOLDOWN")
    d = eng.evaluate(proposal(), account(), [], SPECS, st, now=FIXED_NOW + timedelta(hours=3))
    assert d.approved and d.volume == pytest.approx(0.05)  # half of the normal 0.10
    record_trade_result(st, 20, FIXED_NOW, P)
    d = eng.evaluate(proposal(), account(), [], SPECS, st, now=FIXED_NOW + timedelta(hours=3))
    assert d.volume == pytest.approx(0.10)  # back to normal after a winner, never above it


def test_streak_scale_can_never_increase_risk():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, loss_streak_risk_scale=1.5)  # type: ignore[call-arg]


def test_many_positions_allowed_when_risk_is_off_the_table():
    """Break-even positions carry no open risk, so the bot can keep adding trades."""
    eng = RiskEngine(RiskLimits(), lambda a, b: 0.0)
    from tests.test_risk_engine import pos

    # 10 winners with stops above entry: +$95 floating each (0.01 lot OIL = $10 per 1.0 move).
    at_be = [pos(ticket=str(i), symbol="OIL", volume=0.01, entry=2000, sl=2000.5, current=2010, strategy=f"S{i}")
             for i in range(10)]
    d = eng.evaluate(proposal(), account(equity=10_950), at_be, SPECS, state())
    assert d.approved, d.reason  # 10 open trades, zero capital at risk
    assert d.metrics["portfolio_risk"] == 0.0
    at_risk = [pos(ticket=str(i), symbol="OIL", volume=0.01, entry=2000, sl=1995, current=2000, strategy=f"S{i}")
               for i in range(4)]  # 4 x $50 = 2% capital at risk
    # If all 4 stops hit, equity lands exactly on the 2% daily floor -> no room for a 5th trade.
    assert eng.evaluate(proposal(), account(), at_risk, SPECS, state()).reason == "MAX_DAILY_RISK_REACHED"


def test_paper_losses_trigger_persisted_cooldown(container):
    from app.services.risk_state_service import apply_trade_result

    with container.db.session() as s:
        for _ in range(3):
            apply_trade_result(s, repo.PAPER_LOGIN, -7.0, FIXED_NOW, container.limits.loss_streak)
    st = container.risk_state.equity_state(repo.PAPER_LOGIN)  # re-read from the database
    assert st.consecutive_losses == 3 and in_cooldown(st, FIXED_NOW + timedelta(minutes=5))
    container.risk_state.reset(["loss_streak"], "tester", "", {repo.PAPER_LOGIN: 10000.0})
    st = container.risk_state.equity_state(repo.PAPER_LOGIN)
    assert st.consecutive_losses == 0 and st.cooldown_until is None


def test_daily_limit_counts_floating_profit_giveback():
    """Floating profit that would be given back at the stops still counts against the daily floor."""
    from tests.test_risk_engine import pos

    eng = RiskEngine(RiskLimits(), lambda a, b: 0.0)
    # Equity 10_100 (+100 floating) but stops would give back 290 -> worst case 9_810, floor is 9_800.
    runner = pos(symbol="OIL", volume=0.01, entry=2000, sl=1981, current=2010)
    d = eng.evaluate(proposal(), account(equity=10_100), [runner], SPECS, state(10_000))
    assert d.approved and d.risk_money <= 10 + 1e-9  # only the $10 left above the floor may be risked
    assert d.metrics["portfolio_risk"] == pytest.approx(0.0188, abs=1e-4)  # capital at risk: (2000-1981) x $10
