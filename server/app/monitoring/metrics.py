"""Minimal in-process metrics registry (counters + latency summaries).

Exposed through ``GET /v1/metrics`` for the dashboard. For multi-instance
deployments export to Prometheus or similar; the interface stays the same.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class _Summary:
    samples: deque[float] = field(default_factory=lambda: deque(maxlen=1000))

    def observe(self, value: float) -> None:
        self.samples.append(value)

    def snapshot(self) -> dict[str, float]:
        if not self.samples:
            return {"count": 0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
        ordered = sorted(self.samples)
        n = len(ordered)
        return {
            "count": n,
            "p50_ms": round(ordered[n // 2] * 1000, 2),
            "p95_ms": round(ordered[min(n - 1, int(n * 0.95))] * 1000, 2),
            "max_ms": round(ordered[-1] * 1000, 2),
        }


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._summaries: dict[str, _Summary] = defaultdict(_Summary)
        self._gauges: dict[str, float] = {}

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe(self, name: str, seconds: float) -> None:
        with self._lock:
            self._summaries[name].observe(seconds)

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - start)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            requests = self._counters.get("http_requests", 0)
            errors = self._counters.get("http_errors", 0)
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "latency": {k: v.snapshot() for k, v in self._summaries.items()},
                "error_rate": round(errors / requests, 4) if requests else 0.0,
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._summaries.clear()
            self._gauges.clear()


metrics = Metrics()
