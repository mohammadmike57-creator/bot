"""Backtest / walk-forward / optimisation / model plumbing on SYNTHETIC data.

These tests prove the machinery runs and respects its invariants. They make
no claim about profitability - synthetic random walks have no edge.
"""

from datetime import UTC, datetime

import numpy as np
import pytest

from app.ai.features import FEATURE_COLUMNS, compute_features
from app.ai.inference import HealthThresholds, InferenceService, ModelMode, population_stability_index
from app.ai.labeling import triple_barrier_labels
from app.ai.model_registry import ModelRegistry
from app.ai.probability import Probabilities, decide
from app.ai.signal_model import SignalModel
from app.backtesting.engine import BacktestConfig, BacktestEngine
from app.backtesting.metrics import max_drawdown
from app.backtesting.transaction_costs import CostAssumptions
from app.backtesting.walk_forward import WalkForwardConfig, make_folds, run_walk_forward
from app.database.database import Database
from app.optimization.optimizer import Optimizer, apply_params
from app.optimization.parameter_space import default_space
from app.optimization.validation import robust_score, stability_penalty
from app.risk.limits import RiskLimits
from tests.conftest import gold_spec, make_bars

SPECS = {"XAUUSD": gold_spec(), "US30": gold_spec(symbol="US30", tick_value=0.01, contract_size=1.0)}


@pytest.fixture(scope="module")
def data():
    return {"XAUUSD": make_bars(2500, seed=1), "US30": make_bars(2500, seed=2, price=42000, vol=0.001)}


def cfg(**kw):
    base = dict(initial_equity=10_000.0, costs=CostAssumptions(commission_per_lot=7.0, slippage_points=2),
                limits=RiskLimits(), apply_market_hours=True, news_events=[])
    base.update(kw)
    return BacktestConfig(**base)


def test_backtest_runs_and_respects_invariants(data):
    res = BacktestEngine(cfg(), SPECS, data).run()
    m = res.metrics
    assert len(res.equity_values) > 1000
    assert sum(res.decisions.values()) > 0
    assert m["trades"] > 0, res.decisions  # the machinery must actually trade, not reject everything
    assert res.decisions["NEWS_BLACKOUT"] == 0  # empty historical calendar = no events, not "stale"
    assert m["trades"] == len(res.trades)
    # every trade risked at most the per-trade budget (plus scaling cap)
    for t in res.trades:
        assert t.risk_money <= 10_000 * 0.005 * 1.25 * 1.5 + 1e-6
    # equity curve consistent with reported drawdown
    assert m["max_drawdown"] == pytest.approx(max_drawdown(res.equity_values), abs=1e-4)
    assert res.assumptions["news_filter"] == "historical calendar"


def test_drawdown_limit_stops_trading(data):
    res = BacktestEngine(cfg(limits=RiskLimits(max_drawdown=0.001, max_daily_loss=0.5)), SPECS, data).run()
    assert res.decisions["MAX_DRAWDOWN_REACHED"] > 0  # lock engaged and blocked further entries
    assert res.metrics["max_drawdown"] < 0.05


def test_backtest_without_calendar_is_labelled(data):
    res = BacktestEngine(cfg(news_events=None), SPECS, {"XAUUSD": data["XAUUSD"]}).run()
    assert "NOT APPLIED" in res.assumptions["news_filter"]


def test_small_account_rejected_for_min_volume(data):
    """$100 with 0.5% risk = $0.50 budget; 0.01 lot of gold with an ATR stop risks several dollars."""
    res = BacktestEngine(cfg(initial_equity=100.0), SPECS, {"XAUUSD": data["XAUUSD"]}).run()
    assert res.decisions.get("MIN_VOLUME_EXCEEDS_RISK", 0) > 0
    assert res.metrics["trades"] == 0


def test_walk_forward_folds_are_chronological_with_embargo(data):
    f = compute_features(data["XAUUSD"])
    folds = make_folds(f["time"], WalkForwardConfig(train_bars=800, validate_bars=300, test_bars=300,
                                                    embargo_bars=24))
    assert len(folds) >= 2
    for fold in folds:
        assert fold.train[1] < fold.validate[0] < fold.validate[1] < fold.test[0] < fold.test[1]
    assert folds[1].test[0] > folds[0].test[0]


def test_walk_forward_with_model_runs(data):
    wf = WalkForwardConfig(train_bars=1200, validate_bars=300, test_bars=300, embargo_bars=24,
                           confidence_grid=(0.55, 0.65))
    res = run_walk_forward({"XAUUSD": data["XAUUSD"]}, SPECS, cfg(), wf, use_model=True)
    assert len(res.folds) >= 1
    assert "trades" in res.oos_metrics
    assert all("confidence_threshold" in f.chosen for f in res.folds)


def test_optimizer_prefers_robust_points(data):
    opt = Optimizer({"XAUUSD": data["XAUUSD"]}, SPECS, cfg(), default_space(), min_trades=5, seed=1)
    start = datetime(2026, 1, 10, tzinfo=UTC)
    end = datetime(2026, 1, 24, tzinfo=UTC)
    trials = opt.optimize(start, end, n_trials=4, top_k=1)
    assert len(trials) == 4
    assert trials[0].robust is not None
    applied = apply_params(cfg(), trials[0].params, default_space())
    assert applied.strategy_params.sl_atr_mult == trials[0].params["sl_atr_mult"]


def test_robust_score_penalises_low_trades_and_drawdown():
    good = {"trades": 100, "profit_factor": 1.5, "avg_r": 0.2, "sharpe": 1.0, "sortino": 1.5,
            "return_over_drawdown": 2.0, "max_drawdown": 0.05}
    few = {**good, "trades": 5}
    deep = {**good, "max_drawdown": 0.30}
    assert robust_score(good) > robust_score(few)
    assert robust_score(good) > robust_score(deep)
    assert robust_score({"trades": 0}) == -10.0
    assert stability_penalty([1.0, 0.2], 1.0) > stability_penalty([0.95, 0.9], 1.0)


def _trained_model(data):
    f = compute_features(data["XAUUSD"])
    y = triple_barrier_labels(f, 1.5, 24)
    return SignalModel().fit(f[FEATURE_COLUMNS], y), f, y


def test_signal_model_probabilities_are_valid(data):
    model, f, y = _trained_model(data)
    p = model.predict_proba(f[FEATURE_COLUMNS].dropna().tail(50))
    assert p.shape == (50, 3)
    np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-6)
    metrics = model.evaluate(f[FEATURE_COLUMNS], y)
    assert 0 <= metrics["accuracy"] <= 1


def test_registry_versioning_and_integrity(tmp_path, data):
    db = Database("sqlite://")
    db.create_all()
    reg = ModelRegistry(db, tmp_path)
    model, _, _ = _trained_model(data)
    meta = reg.register(model, version="v-test", features=model.features, hyperparameters=model.params,
                        validation_metrics={"accuracy": 0.4}, oos_metrics={})
    with pytest.raises(ValueError):  # no OOS results -> cannot activate
        reg.set_status(meta.model_id, "ACTIVE")
    meta2 = reg.register(model, version="v-test2", oos_metrics={"profit_factor": 1.1})
    reg.set_status(meta2.model_id, "ACTIVE")
    svc = InferenceService(reg, HealthThresholds(), allow_rule_based=False)
    svc.reload()
    assert svc.mode is ModelMode.MODEL and svc.version == "v-test2"
    # Tampered artifact -> refuses to load -> SAFE (fallback disabled)
    (tmp_path / meta2.artifact_path).write_bytes(b"tampered")
    svc.reload()
    assert svc.mode is ModelMode.SAFE and "checksum" in svc.load_error


def test_inference_falls_back_without_model():
    assert InferenceService(None, HealthThresholds(), allow_rule_based=True).mode is ModelMode.RULE_BASED
    assert InferenceService(None, HealthThresholds(), allow_rule_based=False).mode is ModelMode.SAFE


def test_model_health_degrades_on_poor_performance():
    svc = InferenceService(None, HealthThresholds(min_recent_trades=5, min_recent_profit_factor=1.0),
                           allow_rule_based=True)
    for r in (-1, -1, -1, 0.5, -1):
        svc.health.record_trade_result(r)
    assert any(p.startswith("RECENT_PF") for p in svc.health.problems())


def test_psi_detects_shift():
    qs = list(np.quantile(np.random.default_rng(0).normal(0, 1, 5000), np.linspace(0, 1, 11)))
    same = population_stability_index(qs, np.random.default_rng(1).normal(0, 1, 500))
    shifted = population_stability_index(qs, np.random.default_rng(1).normal(2, 1, 500))
    assert same < 0.1 < shifted


@pytest.mark.parametrize("p,expected", [
    ((0.82, 0.12, 0.06), "MODEL_CONFIRMS"),
    ((0.55, 0.40, 0.05), "CONFIDENCE_BELOW_THRESHOLD"),
    ((0.62, 0.36, 0.02), "MODEL_CONFIRMS"),
    ((0.61, 0.50, -0.11), None),
    ((0.30, 0.10, 0.60), "MODEL_HOLD_DOMINANT"),
])
def test_probability_decision(p, expected):
    if expected is None:
        with pytest.raises(ValueError):
            Probabilities(*p)
        return
    assert decide(Probabilities(*p), threshold=0.6, min_edge=0.15, max_hold=0.5).reason == expected
