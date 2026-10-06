from dataclasses import replace

import pytest

from app.domain import AccountState, Direction, HedgePurpose, OpenPosition, TradeProposal
from app.risk.drawdown import EquityRiskState
from app.risk.limits import RiskLimits
from app.risk.risk_engine import ControlFlags, RiskEngine, risk_multiplier
from tests.conftest import gold_spec

SPEC = gold_spec()
OIL = gold_spec(symbol="OIL", tick_value=10.0, margin_per_lot=500.0)
SPECS = {"XAUUSD": SPEC, "OIL": OIL, "US30": gold_spec(symbol="US30")}


def engine(corr=0.0, **limits):
    return RiskEngine(RiskLimits(**limits), lambda a, b: 1.0 if a == b else corr)


def account(equity=10_000.0, margin=0.0):
    return AccountState(login="1", balance=equity, equity=equity, margin=margin, free_margin=equity - margin)


def state(equity=10_000.0):
    return EquityRiskState(day_start_equity=equity, initial_equity=equity, peak_equity=equity, last_equity=equity)


def proposal(**kw):
    base = dict(symbol="XAUUSD", direction=Direction.BUY, entry_price=2000.0, stop_loss=1995.0, take_profit=2010.0,
                strategy="TEST")
    base.update(kw)
    return TradeProposal(**base)


def pos(ticket="1", symbol="XAUUSD", direction=Direction.BUY, volume=0.1, entry=2000.0, sl=1995.0, current=2000.0,
        strategy="TEST"):
    return OpenPosition(ticket=ticket, symbol=symbol, direction=direction, volume=volume, entry_price=entry, sl=sl,
                        tp=0, current_price=current, strategy=strategy)


def test_approves_valid_trade_with_risk_sized_volume():
    d = engine().evaluate(proposal(), account(), [], SPECS, state())
    assert d.approved, d.reason
    assert d.volume == pytest.approx(0.10)
    assert d.risk_fraction <= 0.005 + 1e-9


@pytest.mark.parametrize("flags,reason", [
    (ControlFlags(kill_switch=True), "KILL_SWITCH_ACTIVE"),
    (ControlFlags(paused=True), "NEW_TRADES_PAUSED"),
    (ControlFlags(safe_mode=True, safe_mode_reason="x"), "SAFE_MODE: x"),
])
def test_global_controls_block(flags, reason):
    d = engine().evaluate(proposal(), account(), [], SPECS, state(), flags)
    assert not d.approved and d.reason == reason


@pytest.mark.parametrize("kw,reason", [
    (dict(is_martingale=True), "MARTINGALE_PROHIBITED"),
    (dict(volume_multiplier_after_loss=2.0), "MARTINGALE_PROHIBITED"),
    (dict(is_grid=True), "GRID_PROHIBITED"),
    (dict(averaging_down=True), "AVERAGING_DOWN_PROHIBITED"),
    (dict(risk_scale=1.5), "INVALID_RISK_SCALE"),
    (dict(risk_scale=-1.0), "INVALID_RISK_SCALE"),
])
def test_prohibited_patterns(kw, reason):
    d = engine().evaluate(proposal(**kw), account(), [], SPECS, state())
    assert not d.approved and d.reason == reason


def test_detects_averaging_down_from_positions():
    losing = pos(entry=2010.0, sl=2000.0, current=2002.0)
    d = engine().evaluate(proposal(entry_price=2002.0, stop_loss=1997.0, take_profit=2012.0), account(), [losing],
                          SPECS, state())
    assert d.reason == "AVERAGING_DOWN_PROHIBITED"


@pytest.mark.parametrize("kw,reason", [
    (dict(stop_loss=0.0), "STOP_LOSS_REQUIRED"),
    (dict(stop_loss=2005.0), "STOP_LOSS_WRONG_SIDE"),
    (dict(take_profit=1990.0), "TAKE_PROFIT_WRONG_SIDE"),
    (dict(take_profit=0.0), "TAKE_PROFIT_REQUIRED"),
    (dict(take_profit=2003.0), "REWARD_RISK_TOO_LOW"),
    (dict(stop_loss=1500.0, take_profit=3000.0), "STOP_LOSS_TOO_WIDE"),
])
def test_invalid_stops(kw, reason):
    d = engine().evaluate(proposal(**kw), account(), [], SPECS, state())
    assert not d.approved and d.reason == reason


def test_stops_level_respected():
    specs = {**SPECS, "XAUUSD": gold_spec(stops_level=1000)}  # 10.00 minimum distance
    d = engine().evaluate(proposal(), account(), [], specs, state())
    assert d.reason == "STOP_LOSS_TOO_CLOSE"


def test_daily_loss_and_drawdown_locks():
    st = state()
    st.daily_loss_locked = True
    assert engine().evaluate(proposal(), account(), [], SPECS, st).reason == "MAX_DAILY_LOSS_REACHED"
    st = state()
    st.drawdown_locked = True
    assert engine().evaluate(proposal(), account(), [], SPECS, st).reason == "MAX_DRAWDOWN_REACHED"
    # computed (not yet locked) breach
    st = state(10_000)
    assert engine().evaluate(proposal(), account(9_700), [], SPECS, st).reason == "MAX_DAILY_LOSS_REACHED"


def test_position_count_is_secondary_limit():
    positions = [pos(ticket=str(i), symbol="OIL", sl=0.5 * 2000, current=2000, entry=2000) for i in range(2)]
    d = engine(max_positions=2).evaluate(proposal(), account(), positions, SPECS, state())
    assert d.reason == "MAX_POSITIONS_REACHED"


def test_portfolio_risk_budget_rejects_below_position_count():
    # Two OIL positions risking $30 each = 0.6% of equity, above a 0.5% portfolio cap:
    # the trade is refused although only 2 of 8 position slots are used.
    p1 = pos(ticket="1", symbol="OIL", volume=0.006, entry=2000, sl=1995, current=2000)
    positions = [p1, replace(p1, ticket="2")]
    eng = engine(max_portfolio_risk=0.005, max_symbol_risk=0.005, risk_per_trade=0.005)
    d = eng.evaluate(proposal(), account(), positions, SPECS, state())
    assert d.reason == "MAX_PORTFOLIO_RISK_REACHED"


def test_symbol_risk_limit():
    existing = pos(volume=0.29, sl=1995.0)  # $145 risk on XAUUSD = 1.45% of 10k
    d = engine().evaluate(proposal(), account(), [existing], SPECS, state())
    # Only $5 of the 1.5% symbol budget remains -> 0.01 lot max
    assert d.approved and d.volume == pytest.approx(0.01)
    existing = pos(volume=0.30, sl=1995.0)
    d = engine().evaluate(proposal(), account(), [existing], SPECS, state())
    assert d.reason == "MAX_SYMBOL_RISK_REACHED"


def test_strategy_risk_limit():
    existing = pos(symbol="OIL", volume=0.04, sl=1995.0, strategy="TEST")  # 5*1000*0.04 = $200 = 2%
    d = engine(max_symbol_risk=0.015).evaluate(proposal(), account(), [existing], SPECS, state())
    assert d.reason == "MAX_STRATEGY_RISK_REACHED"


def test_correlation_reduces_or_rejects():
    existing = pos(symbol="US30", volume=0.15, sl=1995.0, strategy="OTHER")  # $75 risk on correlated US30
    uncorrelated = engine(corr=0.0).evaluate(proposal(), account(), [existing], SPECS, state())
    correlated = engine(corr=0.9, max_correlated_exposure=0.01).evaluate(proposal(), account(), [existing], SPECS,
                                                                          state())
    assert uncorrelated.volume == pytest.approx(0.10)
    assert correlated.approved and correlated.volume == pytest.approx(0.05)  # $100 cap - $75 = $25 -> 0.05 lot
    blocked = engine(corr=0.9, max_correlated_exposure=0.0075).evaluate(proposal(), account(), [existing], SPECS,
                                                                        state())
    assert blocked.reason == "MAX_CORRELATION_RISK_REACHED"


def test_opposite_direction_correlation_offsets():
    existing = pos(symbol="US30", direction=Direction.SELL, volume=0.15, entry=2000, sl=2005.0, strategy="OTHER")
    d = engine(corr=0.9, max_correlated_exposure=0.0075).evaluate(proposal(), account(), [existing], SPECS, state())
    assert d.approved


def test_hedge_requires_purpose():
    short = pos(direction=Direction.SELL, entry=2000, sl=2005.0)
    d = engine().evaluate(proposal(), account(), [short], SPECS, state())
    assert d.reason == "HEDGE_WITHOUT_PURPOSE"
    d = engine().evaluate(proposal(hedge_purpose=HedgePurpose.EVENT_RISK), account(), [short], SPECS, state())
    assert d.approved


def test_margin_usage_limits_volume():
    d = engine().evaluate(proposal(), account(equity=10_000, margin=3_960), [], SPECS, state())
    # 40% of 10k = 4000; 40 left / 400 per lot = 0.1 -> fits exactly
    assert d.approved and d.volume <= 0.1 + 1e-9
    d = engine().evaluate(proposal(), account(equity=10_000, margin=3_999), [], SPECS, state())
    assert d.reason == "INSUFFICIENT_MARGIN"


def test_small_account_min_volume_rejection():
    d = engine().evaluate(proposal(), account(equity=100), [], SPECS, state(100))
    assert not d.approved
    assert "Minimum broker volume exceeds allowed account risk." in d.reason


def test_position_without_stop_blocks_new_risk():
    naked = pos(sl=0.0)
    d = engine().evaluate(proposal(symbol="OIL"), account(), [naked], SPECS, state())
    assert d.reason == "OPEN_POSITION_WITHOUT_STOP_OR_SPEC"


def test_risk_multiplier_bounds_and_drawdown():
    L = RiskLimits()
    steps = [(1.0, 1.0), (1.25, 1.05), (1.5, 1.1), (2.0, 1.2), (4.0, 3.0)]
    assert risk_multiplier(100, 100, steps, L) == 1.0
    assert risk_multiplier(125, 100, steps, L) == pytest.approx(1.05)
    assert risk_multiplier(160, 100, steps, L) == pytest.approx(1.1)
    assert risk_multiplier(1000, 100, steps, L) == pytest.approx(1.25)  # hard cap
    assert risk_multiplier(90, 100, steps, L) <= 1.0  # below starting capital: never scale up
    assert risk_multiplier(160, 100, steps, L, drawdown_fraction=0.05) == pytest.approx(0.75)
    assert risk_multiplier(10_000, 10_000, steps, L) == 1.0  # scaling is relative, not absolute dollars
