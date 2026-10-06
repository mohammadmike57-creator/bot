"""Tabular signal model: gradient-boosted trees with probability calibration.

Default estimator is scikit-learn's HistGradientBoostingClassifier (native on
Apple Silicon, no OpenMP setup). LightGBM is used when installed and
requested. Calibration uses forward-chaining time-series splits only - the
data is never shuffled.

A sequence model (LSTM/temporal CNN) is NOT included; the ``SignalModel``
interface (fit / predict_proba / metadata) is where one would plug in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score, log_loss
from sklearn.model_selection import TimeSeriesSplit

from app.ai.features import FEATURE_COLUMNS
from app.ai.labeling import BUY, HOLD, SELL
from app.ai.probability import Probabilities

DEFAULT_PARAMS: dict[str, Any] = {
    "max_depth": 3,
    "learning_rate": 0.05,
    "max_iter": 300,
    "l2_regularization": 1.0,
    "min_samples_leaf": 50,
}


def _make_estimator(model_type: str, params: dict[str, Any]) -> Any:
    if model_type == "lightgbm":
        try:
            import lightgbm as lgb  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("lightgbm not installed; pip install lightgbm or use model_type='hgb'") from exc
        return lgb.LGBMClassifier(
            max_depth=params.get("max_depth", 3),
            learning_rate=params.get("learning_rate", 0.05),
            n_estimators=params.get("max_iter", 300),
            reg_lambda=params.get("l2_regularization", 1.0),
            min_child_samples=params.get("min_samples_leaf", 50),
            verbose=-1,
        )
    return HistGradientBoostingClassifier(random_state=7, **params)


@dataclass
class SignalModel:
    model_type: str = "hgb"
    params: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    features: list[str] = field(default_factory=lambda: list(FEATURE_COLUMNS))
    calibrated: Any = None
    feature_quantiles: dict[str, list[float]] = field(default_factory=dict)
    classes_: list[int] = field(default_factory=list)

    def fit(self, X: pd.DataFrame, y: pd.Series, calibration_splits: int = 3) -> SignalModel:
        X = X[self.features]
        mask = X.notna().all(axis=1) & y.notna()
        X, y = X[mask], y[mask].astype(int)
        if len(X) < 500:
            raise ValueError(f"not enough clean training rows ({len(X)}); need >= 500")
        if y.nunique() < 3:
            raise ValueError("training labels must contain BUY, SELL and HOLD classes")
        base = _make_estimator(self.model_type, self.params)
        self.calibrated = CalibratedClassifierCV(base, method="sigmoid", cv=TimeSeriesSplit(n_splits=calibration_splits))
        self.calibrated.fit(X.to_numpy(), y.to_numpy())
        self.classes_ = [int(c) for c in self.calibrated.classes_]
        qs = np.linspace(0, 1, 11)
        self.feature_quantiles = {c: [float(v) for v in X[c].quantile(qs).to_numpy()] for c in self.features}
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        if self.calibrated is None:
            raise RuntimeError("model not fitted")
        raw = self.calibrated.predict_proba(X[self.features].to_numpy())
        # Re-order columns to HOLD, BUY, SELL regardless of training order.
        out = np.zeros((len(X), 3))
        for idx, cls in enumerate(self.classes_):
            out[:, cls] = raw[:, idx]
        return out

    def probabilities(self, row: pd.DataFrame) -> Probabilities:
        p = self.predict_proba(row.tail(1))[0]
        total = float(p.sum()) or 1.0
        return Probabilities(p_buy=float(p[BUY] / total), p_sell=float(p[SELL] / total), p_hold=float(p[HOLD] / total))

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> dict[str, float]:
        X = X[self.features]
        mask = X.notna().all(axis=1) & y.notna()
        X, y = X[mask], y[mask].astype(int)
        if len(X) == 0:
            return {"rows": 0}
        proba = self.predict_proba(X)
        pred = proba.argmax(axis=1)
        directional = pred != HOLD
        dir_precision = float((pred[directional] == y.to_numpy()[directional]).mean()) if directional.any() else 0.0
        return {
            "rows": float(len(X)),
            "accuracy": float(accuracy_score(y, pred)),
            "log_loss": float(log_loss(y, proba, labels=[HOLD, BUY, SELL])),
            "directional_precision": dir_precision,
            "directional_rate": float(directional.mean()),
            "base_rate_hold": float((y == HOLD).mean()),
        }
