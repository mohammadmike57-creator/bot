import pytest

from app.risk.margin import max_volume_for_margin, projected_margin_usage
from app.risk.position_sizing import MIN_VOLUME_MESSAGE, calculate_position_size, loss_per_lot, normalize_volume
from tests.conftest import gold_spec


def test_loss_per_lot_uses_tick_size_and_value():
    spec = gold_spec()  # 0.01 tick, $1 per tick per lot
    assert loss_per_lot(spec, 2000.0, 1995.0) == pytest.approx(500.0)
    assert loss_per_lot(spec, 2000.0, 1995.0, commission_per_lot=7) == pytest.approx(507.0)


def test_size_from_risk_and_stop_distance():
    spec = gold_spec()
    r = calculate_position_size(equity=10_000, risk_fraction=0.005, entry=2000, stop_loss=1995, spec=spec)
    # $50 risk / $500 per lot = 0.10 lot
    assert r.ok and r.volume == pytest.approx(0.10)
    assert r.risk_money == pytest.approx(50.0)


def test_volume_is_floored_to_step_never_rounded_up():
    spec = gold_spec()
    r = calculate_position_size(equity=10_000, risk_fraction=0.005, entry=2000, stop_loss=1993, spec=spec)
    # 50 / 700 = 0.0714 -> 0.07 (not 0.08)
    assert r.volume == pytest.approx(0.07)
    assert r.risk_money <= 50.0


def test_min_volume_exceeding_risk_is_rejected_not_increased():
    """$100 account: 0.5% = $0.50 risk, but 0.01 lot with a $5 stop risks $5."""
    spec = gold_spec()
    r = calculate_position_size(equity=100, risk_fraction=0.005, entry=2000, stop_loss=1995, spec=spec)
    assert not r.ok
    assert r.volume == 0
    assert MIN_VOLUME_MESSAGE in r.reason
    assert r.min_volume_risk == pytest.approx(5.0)


def test_volume_clamped_to_broker_max():
    spec = gold_spec(volume_max=0.5)
    r = calculate_position_size(equity=1_000_000, risk_fraction=0.01, entry=2000, stop_loss=1995, spec=spec)
    assert r.volume == pytest.approx(0.5)


@pytest.mark.parametrize("bad", [0, -0.01, 1.0, 2.0])
def test_invalid_risk_fraction_rejected(bad):
    r = calculate_position_size(equity=1000, risk_fraction=bad, entry=2000, stop_loss=1995, spec=gold_spec())
    assert not r.ok and r.reason == "INVALID_RISK_FRACTION"


def test_invalid_stop_rejected():
    r = calculate_position_size(equity=1000, risk_fraction=0.01, entry=2000, stop_loss=2000, spec=gold_spec())
    assert not r.ok and r.reason == "INVALID_STOP_LOSS"


def test_invalid_spec_rejected():
    r = calculate_position_size(equity=1000, risk_fraction=0.01, entry=2000, stop_loss=1990,
                                spec=gold_spec(volume_step=0))
    assert not r.ok and r.reason.startswith("INVALID_SYMBOL_SPEC")


@pytest.mark.parametrize("raw,expected", [(0.123, 0.12), (0.009, 0.0), (0.01, 0.01), (75.0, 50.0), (0.3, 0.3)])
def test_normalize_volume(raw, expected):
    assert normalize_volume(raw, gold_spec()) == pytest.approx(expected)


def test_normalize_volume_with_coarse_step():
    spec = gold_spec(volume_min=0.1, volume_step=0.1)
    assert normalize_volume(0.29, spec) == pytest.approx(0.2)
    assert normalize_volume(0.09, spec) == 0.0


def test_margin_helpers():
    spec = gold_spec(margin_per_lot=400.0)
    assert projected_margin_usage(0, 1000, spec, 0.5) == pytest.approx(0.2)
    assert max_volume_for_margin(0, 1000, spec, 0.4) == pytest.approx(1.0)
    assert max_volume_for_margin(0, 1000, gold_spec(margin_per_lot=0), 0.4) == float("inf")
