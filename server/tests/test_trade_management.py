import pytest

from app.domain import Direction
from app.execution.simulator import CostModel, SimPosition, TradeSimulator
from app.execution.trade_management import ManagedState, ManagementParams, manage, partial_volume
from tests.conftest import FIXED_NOW, gold_spec

SPEC = gold_spec()
P = ManagementParams(tighten_trigger_r=0.8, tighten_to_r=-0.5, be_trigger_r=1.0, be_offset_r=0.05,
                     trail_start_r=1.0, trail_atr_mult=1.0, tp1_r=1.0, tp1_close_fraction=0.5)


def st(direction=Direction.BUY, volume=0.10):
    entry, sl = (2000.0, 1990.0) if direction is Direction.BUY else (2000.0, 2010.0)
    return ManagedState(direction, entry, sl, sl, volume, volume)


def test_no_action_below_triggers():
    s = st()
    assert manage(s, 2005.0, 2.0, P, SPEC) == []
    assert s.sl == 1990.0


def test_tighten_at_08r():
    s = st()
    acts = manage(s, 2008.0, 20.0, P, SPEC)  # +0.8R, trailing not active
    assert acts[-1].reason == "TIGHTEN" and s.sl == pytest.approx(1995.0)


def test_break_even_at_1r_with_offset():
    s = st()
    acts = manage(s, 2010.0, 50.0, P, SPEC)  # huge ATR -> trailing far, BE wins
    assert any(a.reason == "BREAK_EVEN" for a in acts)
    assert s.sl == pytest.approx(2000.5) and s.be_done


def test_trailing_above_1r_and_never_loosens():
    s = st()
    manage(s, 2020.0, 3.0, P, SPEC)  # +2R -> trail to 2017
    assert s.sl == pytest.approx(2017.0)
    manage(s, 2015.0, 3.0, P, SPEC)  # price falls back: stop must not move down
    assert s.sl == pytest.approx(2017.0)
    manage(s, 2030.0, 3.0, P, SPEC)
    assert s.sl == pytest.approx(2027.0)


def test_sell_side_symmetry():
    s = st(Direction.SELL)
    manage(s, 1980.0, 3.0, P, SPEC)
    assert s.sl == pytest.approx(1983.0)
    manage(s, 1985.0, 3.0, P, SPEC)
    assert s.sl == pytest.approx(1983.0)


def test_partial_once_and_respects_min_volume():
    s = st(volume=0.10)
    acts = manage(s, 2010.0, 50.0, P, SPEC)
    assert any(a.close_volume == pytest.approx(0.05) for a in acts)
    assert not any(a.close_volume for a in manage(s, 2011.0, 50.0, P, SPEC))
    tiny = st(volume=0.01)
    assert partial_volume(tiny, P, SPEC) == 0.0  # cannot split 0.01 lot


def test_stops_level_blocks_too_close_stop():
    spec = gold_spec(stops_level=500)  # 5.00 min distance
    s = st()
    manage(s, 2020.0, 3.0, P, spec)  # trail to 2017 is only 3.00 away -> rejected; BE candidate is allowed
    assert s.sl == pytest.approx(2000.5)


def _sim():
    return TradeSimulator(CostModel(), P)


def _pos(**kw):
    base = dict(ticket="T1", symbol="XAUUSD", direction=Direction.BUY, volume=0.10, entry=2000.0, sl=1990.0,
                tp=2020.0, tp1=2010.0, strategy="S", opened_at=FIXED_NOW, spec=SPEC, risk_money=100.0)
    base.update(kw)
    return SimPosition(**base)


def test_stop_assumed_first_when_both_touched():
    sim = _sim()
    sim.open(_pos())
    events = sim.on_bar("XAUUSD", FIXED_NOW, 2000, 2025, 1985, 2010, 0.2, 3.0)
    assert events[-1].closed.reason == "STOP_LOSS"
    assert events[-1].closed.profit == pytest.approx(-100.0)


def test_gap_through_stop_fills_at_gap():
    sim = _sim()
    sim.open(_pos())
    events = sim.on_bar("XAUUSD", FIXED_NOW, 1980, 1982, 1975, 1978, 0.2, 3.0)
    assert events[-1].closed.exit == 1980
    assert events[-1].closed.profit == pytest.approx(-200.0)


def test_partial_then_take_profit():
    sim = _sim()
    sim.open(_pos())
    ev1 = sim.on_bar("XAUUSD", FIXED_NOW, 2005, 2012, 2004, 2011, 0.2, 3.0)
    partial = [e for e in ev1 if e.kind == "PARTIAL"]
    assert partial and partial[0].closed.volume == pytest.approx(0.05)
    assert sim.positions["T1"].sl > 2000  # protected after +1R
    ev2 = sim.on_bar("XAUUSD", FIXED_NOW, 2011, 2021, 2010.5, 2020.5, 0.2, 1.0)
    assert ev2[-1].closed.reason == "TAKE_PROFIT" and "T1" not in sim.positions


def test_sell_closes_at_ask():
    sim = _sim()
    sim.open(_pos(direction=Direction.SELL, sl=2010.0, tp=1980.0, tp1=1990.0))
    # bid high 2009.9 + spread 0.2 = ask 2010.1 -> stop hit
    events = sim.on_bar("XAUUSD", FIXED_NOW, 2000, 2009.9, 1999, 2005, 0.2, 3.0)
    assert events and events[-1].closed.reason == "STOP_LOSS"


def test_commission_and_slippage_costs():
    sim = TradeSimulator(CostModel(commission_per_lot=7.0, slippage_points=10), P)
    fill = sim.fill_price(Direction.BUY, 2000.0, 2000.2, SPEC)
    assert fill == pytest.approx(2000.3)
    sim.open(_pos(entry=fill))
    ev = sim.close_all({"XAUUSD": (2000.3, 2000.5)}, FIXED_NOW, "TEST")
    assert ev[0].closed.profit == pytest.approx(-0.7)
