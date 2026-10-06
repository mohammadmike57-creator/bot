"""Random-search optimiser with robustness selection.

1. Sample N points from a small space, score each with ``robust_score`` on
   the in-sample window.
2. For the top-k, evaluate every one-step neighbour; subtract a stability
   penalty (sharp optima are fragile).
3. Return the most *robust* point, not the most profitable one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import pandas as pd

from app.backtesting.engine import BacktestConfig, BacktestEngine
from app.domain import SymbolSpec
from app.optimization.parameter_space import ParameterSpace
from app.optimization.validation import robust_score, stability_penalty


def apply_params(base: BacktestConfig, point: dict[str, Any], space: ParameterSpace) -> BacktestConfig:
    strategy, management, risk, blackout, config = {}, {}, {}, {}, {}
    for name, value in point.items():
        target = space.target_of(name)
        if target == "strategy":
            strategy[name] = value
        elif target == "management":
            management["trail_atr_mult" if name == "trail_atr_mult" else name] = value
        elif target == "risk":
            risk[name] = value
        elif target == "blackout":
            blackout[name] = value
        else:
            config[name] = value
    cfg = replace(base, **config)
    if strategy:
        cfg = replace(cfg, strategy_params=cfg.strategy_params.with_overrides(**strategy))
    if management:
        cfg = replace(cfg, management=replace(cfg.management, **management))
    if risk:
        cfg = replace(cfg, limits=replace(cfg.limits, **risk))
    if blackout:
        cfg = replace(cfg, blackout=replace(cfg.blackout, **blackout))
    return cfg


@dataclass
class Trial:
    params: dict[str, Any]
    score: float
    metrics: dict[str, Any]
    robust: float | None = None


class Optimizer:
    def __init__(self, data: dict[str, pd.DataFrame], specs: dict[str, SymbolSpec], base: BacktestConfig,
                 space: ParameterSpace, min_trades: int = 30, seed: int = 42):
        self.data, self.specs, self.base, self.space = data, specs, base, space
        self.min_trades = min_trades
        self.rng = random.Random(seed)
        self._features: dict[str, pd.DataFrame] | None = None
        self._cache: dict[tuple[Any, ...], Trial] = {}

    def _evaluate(self, point: dict[str, Any], start: datetime, end: datetime) -> Trial:
        key = tuple(sorted(point.items())) + (start, end)
        if key in self._cache:
            return self._cache[key]
        cfg = apply_params(self.base, point, self.space)
        engine = BacktestEngine(cfg, self.specs, self.data, None, self._features)
        if self._features is None:
            self._features = {k: v.drop(columns=["regime"]) for k, v in engine.features.items()}
        res = engine.run(start, end)
        trial = Trial(point, robust_score(res.metrics, self.min_trades), res.metrics)
        self._cache[key] = trial
        return trial

    def optimize(self, start: datetime, end: datetime, n_trials: int = 30, top_k: int = 3) -> list[Trial]:
        seen: set[tuple[Any, ...]] = set()
        trials: list[Trial] = []
        attempts = 0
        while len(trials) < min(n_trials, self.space.grid_size()) and attempts < n_trials * 10:
            attempts += 1
            point = self.space.sample(self.rng)
            k = tuple(sorted(point.items()))
            if k in seen:
                continue
            seen.add(k)
            trials.append(self._evaluate(point, start, end))
        trials.sort(key=lambda t: t.score, reverse=True)
        for t in trials[:top_k]:
            neighbours = [self._evaluate(n, start, end).score for n in self.space.neighbours(t.params)]
            t.robust = t.score - stability_penalty(neighbours, t.score)
        ranked = sorted(trials[:top_k], key=lambda t: t.robust if t.robust is not None else -1e9, reverse=True)
        return ranked + trials[top_k:]

    def best_config(self, start: datetime, end: datetime, n_trials: int = 30) -> BacktestConfig:
        best = self.optimize(start, end, n_trials)[0]
        return apply_params(self.base, best.params, self.space)
