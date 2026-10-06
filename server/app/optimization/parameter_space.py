"""Small, bounded parameter spaces (keep the model simple enough to generalise)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Dimension:
    name: str
    values: tuple[Any, ...]
    target: str  # "strategy" | "management" | "risk" | "config" | "blackout"


@dataclass
class ParameterSpace:
    dims: list[Dimension] = field(default_factory=list)

    def sample(self, rng: random.Random) -> dict[str, Any]:
        return {d.name: rng.choice(d.values) for d in self.dims}

    def grid_size(self) -> int:
        n = 1
        for d in self.dims:
            n *= len(d.values)
        return n

    def neighbours(self, point: dict[str, Any]) -> list[dict[str, Any]]:
        """Points one step away in each dimension (for sensitivity analysis)."""
        out = []
        for d in self.dims:
            i = d.values.index(point[d.name])
            for j in (i - 1, i + 1):
                if 0 <= j < len(d.values):
                    out.append({**point, d.name: d.values[j]})
        return out

    def target_of(self, name: str) -> str:
        return next(d.target for d in self.dims if d.name == name)


def default_space() -> ParameterSpace:
    return ParameterSpace([
        Dimension("sl_atr_mult", (1.0, 1.5, 2.0, 2.5), "strategy"),
        Dimension("tp2_r", (1.5, 2.0, 2.5, 3.0), "strategy"),
        Dimension("adx_threshold", (18.0, 22.0, 26.0, 30.0), "strategy"),
        Dimension("rsi_overbought", (0.65, 0.70, 0.75), "strategy"),
        Dimension("trail_atr_mult", (0.8, 1.0, 1.5, 2.0), "management"),
        Dimension("be_trigger_r", (0.8, 1.0, 1.2), "management"),
    ])


def extended_space() -> ParameterSpace:
    """Adds model, session, risk and news parameters. Use with more data only."""
    space = default_space()
    space.dims += [
        Dimension("confidence_threshold", (0.55, 0.6, 0.65, 0.7), "config"),
        Dimension("tp1_close_fraction", (0.0, 0.33, 0.5), "management"),
        Dimension("high_before_min", (10, 15, 30), "blackout"),
        Dimension("high_after_min", (15, 20, 30), "blackout"),
        Dimension("risk_per_trade", (0.0025, 0.005), "risk"),
    ]
    return space
