"""SQLAlchemy ORM models. PostgreSQL in production, SQLite for local tests.

All timestamps are stored and returned as timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Rejects naive datetimes on write; always returns aware UTC on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to UTCDateTime column")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, dict[str, Any]: JSON, list[Any]: JSON}


class Account(Base):
    __tablename__ = "accounts"

    login: Mapped[str] = mapped_column(String(32), primary_key=True)
    server: Mapped[str] = mapped_column(String(128), default="")
    company: Mapped[str] = mapped_column(String(128), default="")
    currency: Mapped[str] = mapped_column(String(8), default="")
    leverage: Mapped[int] = mapped_column(Integer, default=0)
    trade_mode: Mapped[str] = mapped_column(String(16), default="")  # DEMO / CONTEST / REAL
    margin_mode: Mapped[str] = mapped_column(String(16), default="")  # HEDGING / NETTING
    trade_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    equity: Mapped[float] = mapped_column(Float, default=0.0)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    free_margin: Mapped[float] = mapped_column(Float, default=0.0)
    margin_level: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(8), default="MT5")  # MT5 / PAPER
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    __table_args__ = (Index("ix_equity_account_ts", "account_login", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_login: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    balance: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float, default=0.0)
    free_margin: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(8), default="MT5")


class SymbolSpecRecord(Base):
    """Contract specification as reported by MT5 (never assumed)."""

    __tablename__ = "symbol_specs"

    broker_symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    logical_symbol: Mapped[str] = mapped_column(String(32), default="")
    digits: Mapped[int] = mapped_column(Integer)
    point: Mapped[float] = mapped_column(Float)
    tick_size: Mapped[float] = mapped_column(Float)
    tick_value: Mapped[float] = mapped_column(Float)
    tick_value_loss: Mapped[float] = mapped_column(Float, default=0.0)
    contract_size: Mapped[float] = mapped_column(Float)
    volume_min: Mapped[float] = mapped_column(Float)
    volume_max: Mapped[float] = mapped_column(Float)
    volume_step: Mapped[float] = mapped_column(Float)
    stops_level: Mapped[int] = mapped_column(Integer, default=0)
    freeze_level: Mapped[int] = mapped_column(Integer, default=0)
    margin_per_lot: Mapped[float] = mapped_column(Float, default=0.0)
    currency_profit: Mapped[str] = mapped_column(String(8), default="")
    currency_margin: Mapped[str] = mapped_column(String(8), default="")
    trade_mode: Mapped[str] = mapped_column(String(16), default="")
    swap_long: Mapped[float] = mapped_column(Float, default=0.0)
    swap_short: Mapped[float] = mapped_column(Float, default=0.0)
    spread_points: Mapped[float] = mapped_column(Float, default=0.0)
    bid: Mapped[float] = mapped_column(Float, default=0.0)
    ask: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("source", "ticket", name="uq_position_source_ticket"),
        Index("ix_positions_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(8), default="MT5")  # MT5 / PAPER
    account_login: Mapped[str] = mapped_column(String(32), default="")
    symbol: Mapped[str] = mapped_column(String(64))
    logical_symbol: Mapped[str] = mapped_column(String(32), default="")
    magic: Mapped[int] = mapped_column(Integer, default=0)
    strategy: Mapped[str] = mapped_column(String(64), default="")
    direction: Mapped[str] = mapped_column(String(4))  # BUY / SELL
    volume: Mapped[float] = mapped_column(Float)
    initial_volume: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    sl: Mapped[float] = mapped_column(Float, default=0.0)
    tp: Mapped[float] = mapped_column(Float, default=0.0)
    initial_sl: Mapped[float] = mapped_column(Float, default=0.0)
    tp2: Mapped[float] = mapped_column(Float, default=0.0)
    risk_money: Mapped[float] = mapped_column(Float, default=0.0)  # money at risk at open
    profit: Mapped[float] = mapped_column(Float, default=0.0)  # floating or final
    realized_profit: Mapped[float] = mapped_column(Float, default=0.0)  # partial closes
    swap: Mapped[float] = mapped_column(Float, default=0.0)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")  # OPEN / CLOSED
    close_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_reason: Mapped[str] = mapped_column(String(32), default="")
    signal_id: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    session: Mapped[str] = mapped_column(String(16), default="")
    regime: Mapped[str] = mapped_column(String(24), default="")
    be_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    partial_done: Mapped[bool] = mapped_column(Boolean, default=False)
    hedge_purpose: Mapped[str] = mapped_column(String(32), default="")
    opened_at: Mapped[datetime] = mapped_column(default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Order(Base):
    """Order/trade events reported by the EA (or the paper broker).

    ``event_id`` is the idempotency key: the EA re-sends the same event id
    when it retries after a timeout, so duplicates are ignored.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    signal_id: Mapped[str] = mapped_column(String(64), default="")
    ticket: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(8), default="MT5")
    symbol: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(4))
    action: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN / CLOSE / PARTIAL / MODIFY
    volume: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float] = mapped_column(Float, default=0.0)
    executed_price: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_points: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="REQUESTED")
    retcode: Mapped[int] = mapped_column(Integer, default=0)
    comment: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_created", "created_at"),
        Index("ix_signals_bar", "symbol", "timeframe", "bar_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), unique=True)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(default=utcnow)
    symbol: Mapped[str] = mapped_column(String(64))
    logical_symbol: Mapped[str] = mapped_column(String(32), default="")
    timeframe: Mapped[str] = mapped_column(String(8), default="")
    bar_time: Mapped[datetime | None] = mapped_column(nullable=True)
    account_login: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(10))  # BUY / SELL / NO_TRADE
    strategy: Mapped[str] = mapped_column(String(64), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    p_buy: Mapped[float] = mapped_column(Float, default=0.0)
    p_sell: Mapped[float] = mapped_column(Float, default=0.0)
    p_hold: Mapped[float] = mapped_column(Float, default=0.0)
    regime: Mapped[str] = mapped_column(String(24), default="")
    session: Mapped[str] = mapped_column(String(16), default="")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_loss: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit: Mapped[float] = mapped_column(Float, default=0.0)
    take_profit_2: Mapped[float] = mapped_column(Float, default=0.0)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    risk_percent: Mapped[float] = mapped_column(Float, default=0.0)
    risk_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    trading_mode: Mapped[str] = mapped_column(String(8), default="PAPER")
    reason: Mapped[str] = mapped_column(String(255), default="")
    decision_log: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="ISSUED")


class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    signal_id: Mapped[str] = mapped_column(String(64), default="")
    model_id: Mapped[str] = mapped_column(String(64), default="")
    model_version: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(32))
    p_buy: Mapped[float] = mapped_column(Float)
    p_sell: Mapped[float] = mapped_column(Float)
    p_hold: Mapped[float] = mapped_column(Float)
    regime: Mapped[str] = mapped_column(String(24), default="")
    features: Mapped[dict[str, Any]] = mapped_column(default=dict)


class NewsEventRecord(Base):
    __tablename__ = "news_events"
    __table_args__ = (
        UniqueConstraint("provider", "event", "country", "scheduled_at", name="uq_news_event"),
        Index("ix_news_scheduled", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(8), default="")
    country: Mapped[str] = mapped_column(String(64), default="")
    importance: Mapped[str] = mapped_column(String(8))  # LOW / MEDIUM / HIGH
    scheduled_at: Mapped[datetime] = mapped_column()
    previous: Mapped[str] = mapped_column(String(64), default="")
    forecast: Mapped[str] = mapped_column(String(64), default="")
    actual: Mapped[str] = mapped_column(String(64), default="")
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    account_login: Mapped[str] = mapped_column(String(32), default="")
    type: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(10), default="WARNING")
    symbol: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(String(255), default="")
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class PerformanceRecord(Base):
    """Daily roll-up, recomputed whenever a trade closes."""

    __tablename__ = "performance"
    __table_args__ = (UniqueConstraint("account_login", "day", "source", name="uq_perf_day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_login: Mapped[str] = mapped_column(String(32), default="")
    source: Mapped[str] = mapped_column(String(8), default="MT5")
    day: Mapped[date] = mapped_column(Date)
    trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    gross_profit: Mapped[float] = mapped_column(Float, default=0.0)
    gross_loss: Mapped[float] = mapped_column(Float, default=0.0)
    net: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class TradeLog(Base):
    """Explainable record of every decision (accepted or rejected)."""

    __tablename__ = "trade_logs"
    __table_args__ = (Index("ix_trade_logs_ts", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    symbol: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(255), default="")
    signal_id: Mapped[str] = mapped_column(String(64), default="")
    line: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    component: Mapped[str] = mapped_column(String(32))
    event: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(10), default="INFO")
    message: Mapped[str] = mapped_column(String(512), default="")
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ConfigurationVersion(Base):
    __tablename__ = "configuration_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    config_hash: Mapped[str] = mapped_column(String(64), unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)  # non-secret values only
    actor: Mapped[str] = mapped_column(String(64), default="system")
    note: Mapped[str] = mapped_column(String(255), default="")


class RiskState(Base):
    """Persisted risk state: survives EA and backend restarts."""

    __tablename__ = "risk_state"

    account_login: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    day_start_equity: Mapped[float] = mapped_column(Float, default=0.0)
    initial_equity: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    last_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_loss_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    drawdown_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_losses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cooldown_until: Mapped[datetime | None] = mapped_column(nullable=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    kill_reason: Mapped[str] = mapped_column(String(255), default="")
    kill_at: Mapped[datetime | None] = mapped_column(nullable=True)
    close_positions_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_mode_reason: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class EaStatus(Base):
    __tablename__ = "ea_status"

    account_login: Mapped[str] = mapped_column(String(32), primary_key=True)
    ea_version: Mapped[str] = mapped_column(String(32), default="")
    ea_mode: Mapped[str] = mapped_column(String(8), default="")
    terminal_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    trade_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    local_kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(nullable=True)
    last_tick_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_signal_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_trade_at: Mapped[datetime | None] = mapped_column(nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    quotes: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ProcessedRequest(Base):
    """Replay protection / idempotency: every signed request id is stored once."""

    __tablename__ = "processed_requests"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    path: Mapped[str] = mapped_column(String(128), default="")


class SlippageRecord(Base):
    __tablename__ = "slippage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow)
    request_id: Mapped[str] = mapped_column(String(64), default="")
    ticket: Mapped[str] = mapped_column(String(32), default="")
    symbol: Mapped[str] = mapped_column(String(64))
    direction: Mapped[str] = mapped_column(String(4))
    volume: Mapped[float] = mapped_column(Float)
    requested_price: Mapped[float] = mapped_column(Float)
    executed_price: Mapped[float] = mapped_column(Float)
    slippage_points: Mapped[float] = mapped_column(Float)  # positive = adverse


class ModelRecord(Base):
    __tablename__ = "model_registry"

    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32), default="signal")
    symbol: Mapped[str] = mapped_column(String(32), default="ALL")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    dataset_start: Mapped[datetime | None] = mapped_column(nullable=True)
    dataset_end: Mapped[datetime | None] = mapped_column(nullable=True)
    features: Mapped[list[Any]] = mapped_column(default=list)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(default=dict)
    validation_metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    oos_metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="CANDIDATE")  # CANDIDATE/ACTIVE/RETIRED/REJECTED
    artifact_path: Mapped[str] = mapped_column(String(255), default="")
    artifact_sha256: Mapped[str] = mapped_column(String(64), default="")
