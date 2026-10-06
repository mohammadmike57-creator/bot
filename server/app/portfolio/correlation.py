"""Rolling correlation matrix between instruments, from closes the EA reports.

When there is not enough overlapping data the configured *conservative*
fallback is used (by default above the correlation threshold, i.e. unknown
pairs are treated as correlated).
"""

from __future__ import annotations

import threading

import numpy as np
import pandas as pd


class CorrelationMatrix:
    def __init__(self, window: int = 200, min_overlap: int = 50, fallback: float = 0.7):
        self.window = window
        self.min_overlap = min_overlap
        self.fallback = fallback
        self._closes: dict[str, pd.Series] = {}
        self._cache: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def update(self, symbol: str, closes: pd.Series) -> None:
        """``closes`` indexed by UTC timestamp."""
        if closes.empty:
            return
        closes = closes[~closes.index.duplicated(keep="last")].sort_index()
        with self._lock:
            prev = self._closes.get(symbol)
            merged = closes if prev is None else pd.concat([prev, closes])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
            self._closes[symbol] = merged.tail(self.window * 3)
            self._cache = {k: v for k, v in self._cache.items() if symbol not in k}

    def get(self, a: str, b: str) -> float:
        if a == b:
            return 1.0
        key = tuple(sorted((a, b)))
        with self._lock:
            if key in self._cache:
                return self._cache[key]  # type: ignore[index]
            sa, sb = self._closes.get(a), self._closes.get(b)
        value = self.fallback
        if sa is not None and sb is not None:
            joined = pd.concat([np.log(sa).diff(), np.log(sb).diff()], axis=1, join="inner").dropna().tail(self.window)
            if len(joined) >= self.min_overlap:
                rho = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
                if np.isfinite(rho):
                    value = rho
        with self._lock:
            self._cache[key] = value  # type: ignore[index]
        return value

    def matrix(self, symbols: list[str]) -> dict[str, dict[str, float]]:
        return {a: {b: round(self.get(a, b), 3) for b in symbols} for a in symbols}
