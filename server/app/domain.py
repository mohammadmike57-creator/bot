"""Shared domain types used across risk, strategies, execution and backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.BUY else -1

    def opposite(self) -> Direction:
        return Direction.SELL if self is Direction.BUY else Direction.BUY


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NO_TRADE = "NO_TRADE"


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNSTABLE = "UNSTABLE"

    @property
    def is_trend(self) -> bool:
        return self in (Regime.TREND_UP, Regime.TREND_DOWN)


class SessionName(str, Enum):
    SYDNEY = "SYDNEY"
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    OVERLAP = "OVERLAP"  # London / New York overlap
    OFF_HOURS = "OFF_HOURS"


class HedgePurpose(str, Enum):
    NONE = ""
    EXPOSURE_REDUCTION = "EXPOSURE_REDUCTION"
    EVENT_RISK = "EVENT_RISK"
    STRATEGY_TRANSITION = "STRATEGY_TRANSITION"
    PORTFOLIO_HEDGE = "PORTFOLIO_HEDGE"


@dataclass(frozen=True)
class SymbolSpec:
    """Broker contract specification - always sourced from MT5."""

    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level: int = 0
    freeze_level: int = 0
    margin_per_lot: float = 0.0
    tick_value_loss: float = 0.0
    trade_allowed: bool = True

    def loss_tick_value(self) -> float:
        """Tick value used for loss calculations (MT5 SYMBOL_TRADE_TICK_VALUE_LOSS when given)."""
        return self.tick_value_loss if self.tick_value_loss > 0 else self.tick_value

    def money_per_price_unit(self, volume: float) -> float:
        """Account-currency P/L for a 1.0 price move on ``volume`` lots."""
        return volume * self.loss_tick_value() / self.tick_size

    def validate(self) -> list[str]:
        problems = []
        for name in ("point", "tick_size", "tick_value", "volume_min", "volume_max", "volume_step"):
            if getattr(self, name) <= 0:
                problems.append(f"{name} must be > 0")
        if self.volume_min > self.volume_max:
            problems.append("volume_min > volume_max")
        return problems


@dataclass
class OpenPosition:
    ticket: str
    symbol: str  # logical symbol
    direction: Direction
    volume: float
    entry_price: float
    sl: float
    tp: float
    current_price: float
    strategy: str = ""
    magic: int = 0
    profit: float = 0.0
    opened_at: datetime | None = None
    hedge_purpose: str = ""
    own: bool = True

    def unrealized_pnl_ok(self) -> bool:
        return (self.current_price - self.entry_price) * self.direction.sign >= 0


@dataclass
class AccountState:
    login: str
    balance: float
    equity: float
    margin: float = 0.0
    free_margin: float = 0.0
    currency: str = "USD"
    leverage: int = 0
    margin_mode: str = "HEDGING"
    trade_allowed: bool = True


@dataclass
class TradeProposal:
    """What a strategy (optionally confirmed by the model) wants to do."""

    symbol: str  # logical
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    strategy: str
    confidence: float = 0.0
    take_profit_2: float = 0.0
    risk_scale: float = 1.0  # strategies may only REDUCE risk (<= 1)
    hedge_purpose: HedgePurpose = HedgePurpose.NONE
    # Flags a strategy must never set; the risk engine rejects them.
    is_martingale: bool = False
    is_grid: bool = False
    averaging_down: bool = False
    volume_multiplier_after_loss: float = 1.0
    notes: dict[str, object] = field(default_factory=dict)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def reward_risk(self) -> float:
        if self.stop_distance <= 0:
            return 0.0
        return abs(self.take_profit - self.entry_price) / self.stop_distance
