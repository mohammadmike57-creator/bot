"""Walk-forward validation: train -> validate -> out-of-sample test, then roll.

Splits are strictly chronological (never shuffled) and an embargo of
``embargo_bars`` (>= the label horizon) separates consecutive segments so
training labels never peek into validation/test periods.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from app.ai.features import FEATURE_COLUMNS, compute_features
from app.ai.labeling import triple_barrier_labels
from app.ai.signal_model import SignalModel
from app.backtesting.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.backtesting.metrics import TradeResult, compute_metrics
from app.domain import SymbolSpec
from app.optimization.validation import robust_score


@dataclass(frozen=True)
class WalkForwardConfig:
    train_bars: int = 6000
    validate_bars: int = 1500
    test_bars: int = 1500
    embargo_bars: int = 24
    label_horizon: int = 24
    label_barrier_atr: float = 1.5
    confidence_grid: tuple[float, ...] = (0.55, 0.6, 0.65, 0.7)
    min_trades_validation: int = 10


@dataclass(frozen=True)
class Fold:
    index: int
    train: tuple[pd.Timestamp, pd.Timestamp]
    validate: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]


@dataclass
class FoldResult:
    fold: Fold
    chosen: dict[str, Any]
    validation_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    model_metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class WalkForwardResult:
    folds: list[FoldResult]
    oos_trades: list[TradeResult]
    oos_metrics: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "note": "drawdown/daily-loss locks reset at each fold start; live trading would require a manual reset",
            "folds": len(self.folds),
            "oos": {k: v for k, v in self.oos_metrics.items() if not isinstance(v, dict)},
            "per_fold": [{"fold": f.fold.index, "test_start": str(f.fold.test[0]), "test_end": str(f.fold.test[1]),
                          "chosen": f.chosen,
                          "test": {k: f.test_metrics.get(k) for k in ("trades", "profit_factor", "net_profit",
                                                                       "max_drawdown", "expectancy")}}
                         for f in self.folds],
        }


def make_folds(times: pd.Series, cfg: WalkForwardConfig) -> list[Fold]:
    t = pd.to_datetime(times, utc=True).reset_index(drop=True)
    n = len(t)
    folds = []
    start = 0
    k = 0
    while True:
        tr_end = start + cfg.train_bars
        va_start = tr_end + cfg.embargo_bars
        va_end = va_start + cfg.validate_bars
        te_start = va_end + cfg.embargo_bars
        te_end = te_start + cfg.test_bars
        if te_end > n:
            break
        folds.append(Fold(k, (t[start], t[tr_end - 1]), (t[va_start], t[va_end - 1]), (t[te_start], t[te_end - 1])))
        k += 1
        start += cfg.test_bars
    return folds


def _end(ts: pd.Timestamp) -> datetime:
    return (ts + pd.Timedelta(seconds=1)).to_pydatetime()


def _train_model(features: dict[str, pd.DataFrame], fold: Fold, wf: WalkForwardConfig) -> tuple[SignalModel, dict[str, Any]]:
    xs, ys = [], []
    for f in features.values():
        labels = triple_barrier_labels(f, wf.label_barrier_atr, wf.label_horizon)
        mask = (f["time"] >= fold.train[0]) & (f["time"] <= fold.train[1])
        # Drop the last `horizon` training rows: their labels depend on bars after train end.
        idx = np.flatnonzero(mask.to_numpy())
        idx = idx[: max(0, len(idx) - wf.label_horizon)]
        xs.append(f.iloc[idx][FEATURE_COLUMNS])
        ys.append(labels.iloc[idx])
    model = SignalModel().fit(pd.concat(xs), pd.concat(ys))
    return model, {"train_rows": int(sum(len(x) for x in xs))}


def run_walk_forward(
    data: dict[str, pd.DataFrame],
    specs: dict[str, SymbolSpec],
    base: BacktestConfig,
    wf: WalkForwardConfig,
    use_model: bool = True,
    optimize_rules: Callable[[BacktestConfig, datetime, datetime], BacktestConfig] | None = None,
) -> WalkForwardResult:
    """Model mode trains a fresh model per fold; rules mode can re-optimise strategy params per fold."""
    features = {s: compute_features(df) for s, df in data.items()}
    ref = max(features.values(), key=len)
    folds = make_folds(ref["time"], wf)
    if not folds:
        raise ValueError("not enough data for one walk-forward fold; reduce train/validate/test bars")
    results: list[FoldResult] = []
    oos_trades: list[TradeResult] = []
    eq_t: list[datetime] = []
    eq_v: list[float] = []
    equity = base.initial_equity
    for fold in folds:
        chosen: dict[str, Any] = {}
        model_metrics: dict[str, Any] = {}
        cfg = base
        probs = None
        if use_model:
            model, model_metrics = _train_model(features, fold, wf)
            probs = {s: model.predict_proba(f[FEATURE_COLUMNS].fillna(np.nan)) for s, f in features.items()}
            for s, f in features.items():
                nan_rows = f[FEATURE_COLUMNS].isna().any(axis=1).to_numpy()
                probs[s][nan_rows] = np.nan
            best: tuple[float, float, BacktestResult | None] = (-np.inf, wf.confidence_grid[0], None)
            for thr in wf.confidence_grid:
                trial = replace(base, confidence_threshold=thr)
                res = BacktestEngine(trial, specs, data, probs, features).run(fold.validate[0], _end(fold.validate[1]))
                score = robust_score(res.metrics, min_trades=wf.min_trades_validation)
                if score > best[0]:
                    best = (score, thr, res)
            cfg = replace(base, confidence_threshold=best[1])
            chosen["confidence_threshold"] = best[1]
            val_metrics = best[2].metrics if best[2] else {}
        elif optimize_rules is not None:
            cfg = optimize_rules(base, fold.train[0].to_pydatetime(), _end(fold.validate[1]))
            chosen["strategy_params"] = cfg.strategy_params.__dict__
            val_metrics = BacktestEngine(cfg, specs, data, None, features).run(fold.validate[0],
                                                                              _end(fold.validate[1])).metrics
        else:
            val_metrics = BacktestEngine(cfg, specs, data, None, features).run(fold.validate[0],
                                                                              _end(fold.validate[1])).metrics
        test_cfg = replace(cfg, initial_equity=equity)
        test = BacktestEngine(test_cfg, specs, data, probs, features).run(fold.test[0], _end(fold.test[1]))
        oos_trades.extend(test.trades)
        eq_t.extend(test.equity_times)
        eq_v.extend(test.equity_values)
        equity = test.equity_values[-1] if test.equity_values else equity
        results.append(FoldResult(fold, chosen, val_metrics, test.metrics, model_metrics))
    oos = compute_metrics(oos_trades, eq_t, eq_v, base.initial_equity)
    return WalkForwardResult(results, oos_trades, oos)
