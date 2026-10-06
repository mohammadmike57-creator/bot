"""Selects which strategies may run for a symbol given session and regime."""

from __future__ import annotations

from app.config.symbols import AssetClass, profile_for
from app.domain import Regime, SessionName
from app.strategies.base import Strategy, StrategyContext, StrategySignal
from app.strategies.gold_tokyo import GoldTokyoStrategy
from app.strategies.london_trend import LondonTrendStrategy
from app.strategies.newyork_trend import NewYorkTrendStrategy
from app.strategies.oil_strategy import OilStrategy
from app.strategies.overlap_aggressive import OverlapAggressiveStrategy
from app.strategies.us30_strategy import US30Strategy


class StrategyRouter:
    def __init__(self, allow_sydney: bool = False):
        self.allow_sydney = allow_sydney
        self.by_class: dict[AssetClass, list[Strategy]] = {
            AssetClass.METAL: [GoldTokyoStrategy(), LondonTrendStrategy(), NewYorkTrendStrategy(),
                               OverlapAggressiveStrategy()],
            AssetClass.ENERGY: [OilStrategy()],
            AssetClass.INDEX: [US30Strategy()],
            AssetClass.FX: [LondonTrendStrategy(), NewYorkTrendStrategy(), OverlapAggressiveStrategy()],
        }

    def candidates(self, ctx: StrategyContext) -> list[Strategy]:
        if ctx.regime is Regime.UNSTABLE:
            return []
        if ctx.session.primary is SessionName.OFF_HOURS:
            return []
        if ctx.session.primary is SessionName.SYDNEY and not self.allow_sydney:
            return []
        return [s for s in self.by_class.get(profile_for(ctx.symbol).asset_class, []) if s.applicable(ctx)]

    def evaluate(self, ctx: StrategyContext) -> tuple[StrategySignal | None, list[str]]:
        """First strategy (in priority order) producing a signal wins."""
        notes = []
        for strategy in self.candidates(ctx):
            sig = strategy.evaluate(ctx)
            notes.append(f"{strategy.name}:{'signal' if sig else 'flat'}")
            if sig:
                return sig, notes
        if not notes:
            notes.append(f"NO_STRATEGY_FOR session={ctx.session.primary.value} regime={ctx.regime.value}")
        return None, notes
