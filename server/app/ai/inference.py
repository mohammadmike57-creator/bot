"""Model inference with health monitoring and automatic fallback.

Modes:
  MODEL       - active model loaded and healthy; it confirms strategy signals.
  RULE_BASED  - no healthy model; approved rule-based strategies may trade
                (only if ALLOW_RULE_BASED_FALLBACK=true).
  SAFE        - no healthy model and fallback disabled -> NO TRADE.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from app.ai.model_registry import ModelMetadata, ModelRegistry
from app.ai.probability import Probabilities
from app.monitoring.logger import get_logger

log = get_logger(__name__)


class ModelMode(str, Enum):
    MODEL = "MODEL"
    RULE_BASED = "RULE_BASED"
    SAFE = "SAFE"


def population_stability_index(expected_quantiles: list[float], actual: np.ndarray) -> float:
    """PSI of ``actual`` against decile edges learned at training time."""
    actual = actual[np.isfinite(actual)]
    if len(actual) < 20 or len(expected_quantiles) < 3:
        return 0.0
    edges = np.unique(np.asarray(expected_quantiles, dtype=float))
    if len(edges) < 3:
        return 0.0
    inner = edges[1:-1]
    counts = np.bincount(np.searchsorted(inner, actual, side="right"), minlength=len(inner) + 1)
    actual_pct = counts / counts.sum()
    expected_pct = np.full(len(actual_pct), 1.0 / len(actual_pct))
    actual_pct = np.clip(actual_pct, 1e-4, None)
    return float(np.sum((actual_pct - expected_pct) * np.log(actual_pct / expected_pct)))


@dataclass
class HealthThresholds:
    max_drift_psi: float = 0.25
    min_recent_trades: int = 20
    min_recent_profit_factor: float = 0.9
    max_error_rate: float = 0.05
    min_mean_confidence: float = 0.0


class ModelHealth:
    def __init__(self, thresholds: HealthThresholds):
        self.t = thresholds
        self.predictions = 0
        self.errors = 0
        self.recent_r: deque[float] = deque(maxlen=50)
        self.confidences: deque[float] = deque(maxlen=200)
        self.last_drift = 0.0
        self.drift_by_feature: dict[str, float] = {}

    def record_prediction(self, confidence: float) -> None:
        self.predictions += 1
        self.confidences.append(confidence)

    def record_error(self) -> None:
        self.predictions += 1
        self.errors += 1

    def record_trade_result(self, r_multiple: float) -> None:
        self.recent_r.append(r_multiple)

    def update_drift(self, meta_quantiles: dict[str, list[float]], recent: pd.DataFrame) -> None:
        scores = {}
        for col, qs in meta_quantiles.items():
            if col in recent:
                scores[col] = population_stability_index(qs, recent[col].to_numpy(dtype=float))
        self.drift_by_feature = scores
        self.last_drift = float(np.median(list(scores.values()))) if scores else 0.0

    def recent_profit_factor(self) -> float:
        wins = sum(r for r in self.recent_r if r > 0)
        losses = -sum(r for r in self.recent_r if r < 0)
        return wins / losses if losses > 0 else float("inf") if wins > 0 else 0.0

    def problems(self) -> list[str]:
        out = []
        if self.predictions >= 20 and self.errors / self.predictions > self.t.max_error_rate:
            out.append("ERROR_RATE")
        if self.last_drift > self.t.max_drift_psi:
            out.append(f"DRIFT_PSI={self.last_drift:.3f}")
        if len(self.recent_r) >= self.t.min_recent_trades and self.recent_profit_factor() < self.t.min_recent_profit_factor:
            out.append(f"RECENT_PF={self.recent_profit_factor():.2f}")
        if self.confidences and self.t.min_mean_confidence and np.mean(self.confidences) < self.t.min_mean_confidence:
            out.append("LOW_CONFIDENCE")
        return out

    def snapshot(self) -> dict[str, Any]:
        pf = self.recent_profit_factor()
        return {
            "predictions": self.predictions,
            "errors": self.errors,
            "drift_psi": round(self.last_drift, 4),
            "recent_trades": len(self.recent_r),
            "recent_profit_factor": None if pf == float("inf") else round(pf, 3),
            "mean_confidence": round(float(np.mean(self.confidences)), 4) if self.confidences else None,
            "problems": self.problems(),
        }


class InferenceService:
    def __init__(self, registry: ModelRegistry | None, thresholds: HealthThresholds, allow_rule_based: bool,
                 pinned_version: str = ""):
        self.registry = registry
        self.allow_rule_based = allow_rule_based
        self.pinned_version = pinned_version
        self.health = ModelHealth(thresholds)
        self._lock = threading.Lock()
        self._model: Any = None
        self.meta: ModelMetadata | None = None
        self.load_error = ""

    def reload(self) -> None:
        with self._lock:
            self._model, self.meta, self.load_error = None, None, ""
            if self.registry is None:
                self.load_error = "NO_REGISTRY"
                return
            try:
                meta = self.registry.active(version=self.pinned_version)
                if meta is None:
                    self.load_error = "NO_ACTIVE_MODEL"
                    return
                self._model = self.registry.load(meta)
                self.meta = meta
                self.health = ModelHealth(self.health.t)
                log.info("model loaded", extra={"model_id": meta.model_id, "version": meta.version})
            except Exception as exc:
                self.load_error = f"LOAD_FAILED: {exc}"
                log.error("model load failed", extra={"error": str(exc)})

    @property
    def mode(self) -> ModelMode:
        if self._model is not None and not self.health.problems():
            return ModelMode.MODEL
        return ModelMode.RULE_BASED if self.allow_rule_based else ModelMode.SAFE

    @property
    def version(self) -> str:
        return self.meta.version if self.meta else ("rule-based" if self.mode is ModelMode.RULE_BASED else "none")

    def predict(self, features: pd.DataFrame) -> Probabilities | None:
        """Probabilities for the latest row, or None when the model can't be used."""
        if self.mode is not ModelMode.MODEL:
            return None
        try:
            row = features.tail(1)
            if row[self._model.features].isna().any(axis=1).iloc[0]:
                return None
            probs = self._model.probabilities(row)
            self.health.record_prediction(max(probs.p_buy, probs.p_sell))
            if len(features) >= 100:
                self.health.update_drift(self._model.feature_quantiles, features.tail(100))
            return probs
        except Exception as exc:
            self.health.record_error()
            log.error("inference failed", extra={"error": str(exc)})
            return None

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "version": self.version,
            "model": self.meta.to_dict() if self.meta else None,
            "load_error": self.load_error,
            "health": self.health.snapshot(),
        }
