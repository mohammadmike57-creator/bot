import numpy as np
import pandas as pd
import pytest

from app.ai.features import FEATURE_COLUMNS, FeatureCache, compute_features
from app.ai.labeling import triple_barrier_labels
from app.ai.regime_model import RegimeConfig, classify_row
from app.domain import Regime
from tests.conftest import make_bars


def test_features_have_no_lookahead():
    """Features at bar t must be identical whether or not later bars exist."""
    bars = make_bars(900)
    full = compute_features(bars)
    for cut in (400, 600, len(bars) - 1):
        partial = compute_features(bars.iloc[: cut + 1])
        a = full.loc[cut, FEATURE_COLUMNS].astype(float).to_numpy()
        b = partial.loc[cut, FEATURE_COLUMNS].astype(float).to_numpy()
        np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True)


def test_feature_columns_present_and_finite_after_warmup():
    f = compute_features(make_bars(600))
    tail = f[FEATURE_COLUMNS].iloc[300:]
    assert not tail.isna().any().any()


def test_invalid_bars_rejected():
    bars = make_bars(100)
    bars.loc[5, "high"] = bars.loc[5, "low"] - 1
    with pytest.raises(ValueError):
        compute_features(bars)
    with pytest.raises(ValueError):
        compute_features(bars.drop(columns=["close"]))


def test_feature_cache_hits_on_same_candle():
    cache = FeatureCache()
    bars = make_bars(300)
    a = cache.get_or_compute("XAUUSD", "M15", bars)
    b = cache.get_or_compute("XAUUSD", "M15", bars)
    assert a is b and cache.hits == 1


def row(**kw):
    base = dict(adx=30.0, ema20=101.0, ema50=100.0, atr=1.0, atr_pctile=0.5, range_atr=1.0, close=102.0,
                ema20_slope_atr=0.3)
    base.update(kw)
    return pd.Series(base)


@pytest.mark.parametrize("kw,expected", [
    ({}, Regime.TREND_UP),
    (dict(ema20=99.0, ema50=100.0, close=98.0, ema20_slope_atr=-0.3), Regime.TREND_DOWN),
    (dict(adx=15.0), Regime.RANGE),
    (dict(adx=15.0, atr_pctile=0.1), Regime.LOW_VOLATILITY),
    (dict(atr_pctile=0.9), Regime.HIGH_VOLATILITY),
    (dict(atr_pctile=0.99), Regime.UNSTABLE),
    (dict(range_atr=5.0), Regime.UNSTABLE),
    (dict(adx=float("nan")), Regime.UNSTABLE),
])
def test_regime_classifier(kw, expected):
    assert classify_row(row(**kw), RegimeConfig()).regime is expected


def test_triple_barrier_labels_drop_unknown_future():
    f = compute_features(make_bars(500))
    labels = triple_barrier_labels(f, 1.5, 24)
    assert labels.iloc[-24:].isna().all()
    assert set(labels.dropna().unique()) <= {0.0, 1.0, 2.0}
