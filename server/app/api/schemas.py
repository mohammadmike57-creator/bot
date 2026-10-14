"""Request / response models. Strict validation on everything the EA sends."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SymbolStr = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._#\-+!]+$")
TICKET = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9\-]+$")
EVENT_ID = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
TIMEFRAMES = Literal["M1", "M5", "M15", "M30", "H1", "H4"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _finite(v: float) -> float:
    if not math.isfinite(v):
        raise ValueError("value must be finite")
    return v


# ------------------------------------------------------------------------- auth
class AuthRequest(StrictModel):
    api_key: str = Field(min_length=16, max_length=256)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth2 token type, not a secret
    expires_at: datetime


# -------------------------------------------------------------------- heartbeat
class Quote(StrictModel):
    symbol: str = SymbolStr
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_points: float = Field(ge=0)
    time: int = Field(ge=0, description="Last tick time, unix seconds")

    @model_validator(mode="after")
    def _ask_ge_bid(self) -> Quote:
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        return self


class HeartbeatIn(StrictModel):
    account_login: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    ea_version: str = Field(max_length=32)
    ea_mode: Literal["PAPER", "DEMO", "LIVE"]
    terminal_connected: bool
    trade_allowed: bool
    local_kill_switch: bool = False
    open_positions: int = Field(ge=0, le=1000)
    quotes: list[Quote] = Field(default_factory=list, max_length=50)
    latency_ms: float = Field(default=0.0, ge=0, le=600000)


class HeartbeatOut(BaseModel):
    server_time: datetime
    trading_mode: str
    live_permitted: bool
    kill_switch: bool
    close_positions: bool
    paused: bool
    safe_mode: bool
    reason: str


# ---------------------------------------------------------------------- account
class SymbolSpecIn(StrictModel):
    broker_symbol: str = SymbolStr
    digits: int = Field(ge=0, le=10)
    point: float = Field(gt=0)
    tick_size: float = Field(gt=0)
    tick_value: float = Field(gt=0)
    tick_value_loss: float = Field(default=0.0, ge=0)
    contract_size: float = Field(gt=0)
    volume_min: float = Field(gt=0)
    volume_max: float = Field(gt=0)
    volume_step: float = Field(gt=0)
    stops_level: int = Field(default=0, ge=0)
    freeze_level: int = Field(default=0, ge=0)
    margin_per_lot: float = Field(default=0.0, ge=0)
    currency_profit: str = Field(default="", max_length=8)
    currency_margin: str = Field(default="", max_length=8)
    trade_mode: Literal["FULL", "DISABLED", "LONGONLY", "SHORTONLY", "CLOSEONLY"] = "FULL"
    swap_long: float = 0.0
    swap_short: float = 0.0
    spread_points: float = Field(default=0.0, ge=0)
    bid: float = Field(default=0.0, ge=0)
    ask: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _volumes(self) -> SymbolSpecIn:
        if self.volume_min > self.volume_max:
            raise ValueError("volume_min > volume_max")
        return self


class AccountIn(StrictModel):
    login: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    server: str = Field(max_length=128)
    company: str = Field(default="", max_length=128)
    currency: str = Field(max_length=8)
    leverage: int = Field(ge=0)
    trade_mode: Literal["DEMO", "CONTEST", "REAL"]
    margin_mode: Literal["HEDGING", "NETTING", "EXCHANGE"]
    trade_allowed: bool
    balance: float
    equity: float
    margin: float = Field(ge=0)
    free_margin: float
    margin_level: float = Field(ge=0)
    symbols: list[SymbolSpecIn] = Field(default_factory=list, max_length=50)

    @field_validator("balance", "equity", "free_margin")
    @classmethod
    def _finite_values(cls, v: float) -> float:
        return _finite(v)


class AccountOut(BaseModel):
    accepted: bool
    warnings: list[str]
    daily_loss: float
    drawdown: float
    locks: dict[str, bool]


# -------------------------------------------------------------------- positions
class PositionIn(StrictModel):
    ticket: str = TICKET
    symbol: str = SymbolStr
    magic: int = Field(ge=0)
    direction: Literal["BUY", "SELL"]
    volume: float = Field(gt=0)
    entry_price: float = Field(gt=0)
    current_price: float = Field(gt=0)
    sl: float = Field(ge=0)
    tp: float = Field(ge=0)
    profit: float
    swap: float = 0.0
    commission: float = 0.0
    opened_at: int = Field(ge=0)
    comment: str = Field(default="", max_length=64)


class PositionsSyncIn(StrictModel):
    account_login: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    positions: list[PositionIn] = Field(max_length=500)


# ----------------------------------------------------------------- trade events
class TradeEventIn(StrictModel):
    event_id: str = EVENT_ID
    account_login: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    event: Literal["OPENED", "CLOSED", "PARTIAL", "MODIFIED", "REJECTED"]
    signal_id: str = Field(default="", max_length=64)
    ticket: str = Field(default="", max_length=32)
    symbol: str = SymbolStr
    magic: int = Field(default=0, ge=0)
    direction: Literal["BUY", "SELL"]
    volume: float = Field(ge=0)
    requested_price: float = Field(default=0.0, ge=0)
    executed_price: float = Field(default=0.0, ge=0)
    sl: float = Field(default=0.0, ge=0)
    tp: float = Field(default=0.0, ge=0)
    profit: float = 0.0
    retcode: int = 0
    reason: str = Field(default="", max_length=128)
    time: int = Field(ge=0)


# ---------------------------------------------------------------------- signals
class SignalRequest(StrictModel):
    account_login: str = Field(min_length=1, max_length=32, pattern=r"^[0-9A-Za-z]+$")
    symbol: str = SymbolStr
    timeframe: TIMEFRAMES
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_points: float = Field(ge=0)
    market_open: bool
    ea_mode: Literal["PAPER", "DEMO", "LIVE"]
    # closed bars, oldest first: [time, open, high, low, close, tick_volume, spread]
    bars: list[list[float]] = Field(min_length=60, max_length=2000)

    @field_validator("bars")
    @classmethod
    def _bars(cls, bars: list[list[float]]) -> list[list[float]]:
        last_t = -1.0
        for b in bars:
            if len(b) != 7:
                raise ValueError("each bar must have 7 fields")
            if not all(math.isfinite(x) for x in b):
                raise ValueError("bars must be finite")
            t, o, h, low, c = b[0], b[1], b[2], b[3], b[4]
            if t <= last_t:
                raise ValueError("bars must be strictly increasing in time")
            if min(o, h, low, c) <= 0 or h < low or not (low <= o <= h and low <= c <= h):
                raise ValueError("invalid OHLC bar")
            last_t = t
        return bars

    @model_validator(mode="after")
    def _quote(self) -> SignalRequest:
        if self.ask < self.bid:
            raise ValueError("ask must be >= bid")
        return self


class SignalResponse(BaseModel):
    signal_id: str
    created_at: datetime
    expires_at: datetime
    created_at_epoch: int
    expires_at_epoch: int
    symbol: str
    timeframe: str
    action: Literal["BUY", "SELL", "NO_TRADE"]
    confidence: float
    p_buy: float
    p_sell: float
    p_hold: float
    strategy: str
    model_version: str
    regime: str
    session: str
    risk_approved: bool
    risk_percent: float
    volume: float
    entry_price: float
    stop_loss: float
    take_profit: float
    take_profit_2: float
    trailing: bool
    atr: float
    be_trigger_r: float
    be_offset_r: float
    trail_start_r: float
    trail_atr_multiplier: float
    tp1_r: float
    tp1_close_fraction: float
    trading_mode: str
    paper: bool
    reason: str
    decision: str


# ----------------------------------------------------------------- admin control
class KillSwitchIn(StrictModel):
    reason: str = Field(min_length=3, max_length=255)
    close_positions: bool | None = None


class ResetRiskIn(StrictModel):
    items: list[Literal["kill_switch", "daily_loss", "drawdown", "safe_mode", "loss_streak"]] = Field(min_length=1)
    confirm: Literal["RESET"]
    note: str = Field(default="", max_length=255)


class ControlIn(StrictModel):
    action: Literal["START", "PAUSE", "RESUME"]


class RuntimeSettingsIn(StrictModel):
    """Dashboard-adjustable settings. They may only TIGHTEN env-configured limits."""

    risk_per_trade: float | None = Field(default=None, gt=0, lt=1)
    max_positions: int | None = Field(default=None, ge=1, le=100)
    max_portfolio_risk: float | None = Field(default=None, gt=0, lt=1)
    enabled_symbols: list[str] | None = Field(default=None, max_length=20)
    enabled_sessions: list[Literal["SYDNEY", "TOKYO", "LONDON", "NEW_YORK", "OVERLAP"]] | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.5, lt=1)
