"""Risk-budget allocation across symbols and strategies.

Budgets are *caps*, never targets: an unused budget is not an instruction to
trade. Allocation weights can only reduce a trade's risk (scale <= 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AllocationPolicy:
    # Fraction of the per-trade risk allowed per logical symbol (<= 1.0).
    symbol_weights: dict[str, float] = field(default_factory=lambda: {"XAUUSD": 1.0, "OIL": 1.0, "US30": 1.0})
    # Strategies with weak recent performance can be throttled (<= 1.0).
    strategy_weights: dict[str, float] = field(default_factory=dict)
    min_weight: float = 0.0

    def scale_for(self, symbol: str, strategy: str) -> float:
        sw = self.symbol_weights.get(symbol, 1.0)
        base = strategy.split("_")[0] if strategy else ""
        tw = self.strategy_weights.get(strategy, self.strategy_weights.get(base, 1.0))
        return max(self.min_weight, min(1.0, sw) * min(1.0, tw))

    def throttle_strategy(self, strategy: str, recent_profit_factor: float, trades: int,
                          min_trades: int = 20, floor_pf: float = 0.8) -> None:
        """Halve a strategy's allocation when its recent live PF is poor."""
        if trades >= min_trades and recent_profit_factor < floor_pf:
            self.strategy_weights[strategy] = 0.5
        elif strategy in self.strategy_weights and recent_profit_factor >= 1.0:
            self.strategy_weights.pop(strategy)
