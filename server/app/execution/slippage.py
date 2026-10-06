"""Slippage monitoring. Abnormal slippage reduces activity (SAFE MODE)."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from app.domain import Direction


def slippage_points(direction: Direction, requested: float, executed: float, point: float) -> float:
    """Positive = adverse (paid more on a buy / received less on a sell)."""
    if point <= 0:
        return 0.0
    return (executed - requested) * direction.sign / point


@dataclass(frozen=True)
class SlippageStatus:
    abnormal: bool
    mean_points: float
    samples: int
    reason: str = ""


class SlippageMonitor:
    def __init__(self, window: int = 20, max_mean_points: float = 50.0, min_samples: int = 5):
        self.window = window
        self.max_mean_points = max_mean_points
        self.min_samples = min_samples
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=self.window))

    def record(self, symbol: str, points: float) -> SlippageStatus:
        self._samples[symbol].append(points)
        return self.status(symbol)

    def status(self, symbol: str) -> SlippageStatus:
        s = self._samples.get(symbol)
        if not s:
            return SlippageStatus(False, 0.0, 0)
        mean = sum(s) / len(s)
        abnormal = len(s) >= self.min_samples and mean > self.max_mean_points
        return SlippageStatus(abnormal, mean, len(s), "ABNORMAL_SLIPPAGE" if abnormal else "")
