"""Turning model probabilities into a (conservative) directional opinion.

BUY merely being more likely than SELL is NOT enough. A direction is only
endorsed when its probability clears an absolute threshold, beats the
opposite side by a margin, and HOLD is not dominant.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain import Direction


@dataclass(frozen=True)
class Probabilities:
    p_buy: float
    p_sell: float
    p_hold: float

    def __post_init__(self) -> None:
        for v in (self.p_buy, self.p_sell, self.p_hold):
            if not 0.0 <= v <= 1.0:
                raise ValueError("probabilities must lie in [0, 1]")
        total = self.p_buy + self.p_sell + self.p_hold
        if abs(total - 1.0) > 1e-3:
            raise ValueError(f"probabilities must sum to 1 (got {total:.4f})")

    def for_direction(self, d: Direction) -> float:
        return self.p_buy if d is Direction.BUY else self.p_sell


@dataclass(frozen=True)
class ProbabilityDecision:
    direction: Direction | None
    confidence: float
    reason: str


def decide(probs: Probabilities, threshold: float, min_edge: float, max_hold: float) -> ProbabilityDecision:
    if probs.p_hold >= max_hold:
        return ProbabilityDecision(None, probs.p_hold, "MODEL_HOLD_DOMINANT")
    direction = Direction.BUY if probs.p_buy >= probs.p_sell else Direction.SELL
    best = probs.for_direction(direction)
    other = probs.for_direction(direction.opposite())
    if best < threshold:
        return ProbabilityDecision(None, best, "CONFIDENCE_BELOW_THRESHOLD")
    if best - other < min_edge:
        return ProbabilityDecision(None, best, "INSUFFICIENT_PROBABILITY_EDGE")
    return ProbabilityDecision(direction, best, "MODEL_CONFIRMS")


def confirms(probs: Probabilities, direction: Direction, threshold: float, min_edge: float, max_hold: float) -> ProbabilityDecision:
    """Does the model endorse a *strategy-proposed* direction?"""
    d = decide(probs, threshold, min_edge, max_hold)
    if d.direction is None:
        return d
    if d.direction is not direction:
        return ProbabilityDecision(None, probs.for_direction(direction), "MODEL_DISAGREES_WITH_STRATEGY")
    return d
