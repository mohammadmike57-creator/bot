"""Data-access functions. Keep SQL here so services stay testable."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import (
    Account,
    ConfigurationVersion,
    EaStatus,
    EquitySnapshot,
    NewsEventRecord,
    Order,
    PerformanceRecord,
    Position,
    ProcessedRequest,
    RiskEvent,
    RiskState,
    Signal,
    SlippageRecord,
    SymbolSpecRecord,
    SystemEvent,
    TradeLog,
    utcnow,
)
from app.domain import AccountState

PAPER_LOGIN = "PAPER"

# ------------------------------------------------------------- replay protection


def record_request_id(s: Session, request_id: str, path: str) -> bool:
    """Store a request id once. Returns False if it was already seen (replay)."""
    if s.get(ProcessedRequest, request_id) is not None:
        return False
    try:
        with s.begin_nested():
            s.add(ProcessedRequest(request_id=request_id, path=path[:128]))
        return True
    except IntegrityError:
        return False


def purge_processed_requests(s: Session, older_than: timedelta) -> int:
    cutoff = utcnow() - older_than
    result = s.execute(delete(ProcessedRequest).where(ProcessedRequest.received_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------- account


def upsert_account(s: Session, data: dict[str, Any]) -> Account:
    acc = s.get(Account, data["login"])
    if acc is None:
        acc = Account(login=data["login"])
        s.add(acc)
    for k, v in data.items():
        if hasattr(acc, k):
            setattr(acc, k, v)
    acc.updated_at = utcnow()
    return acc


def get_account(s: Session, login: str) -> Account | None:
    return s.get(Account, login)


def latest_mt5_account(s: Session) -> Account | None:
    return s.scalars(select(Account).where(Account.source == "MT5").order_by(Account.updated_at.desc())).first()


def get_account_state(s: Session, source: str) -> AccountState:
    acc = s.get(Account, PAPER_LOGIN) if source == "PAPER" else latest_mt5_account(s)
    if acc is None:
        return AccountState(login="", balance=0.0, equity=0.0, trade_allowed=False)
    return AccountState(
        login=acc.login, balance=acc.balance, equity=acc.equity, margin=acc.margin, free_margin=acc.free_margin,
        currency=acc.currency, leverage=acc.leverage, margin_mode=acc.margin_mode, trade_allowed=acc.trade_allowed,
    )


def add_equity_snapshot(s: Session, login: str, balance: float, equity: float, margin: float, free_margin: float,
                        source: str, min_interval: timedelta = timedelta(seconds=55)) -> None:
    last = s.scalars(
        select(EquitySnapshot).where(EquitySnapshot.account_login == login).order_by(EquitySnapshot.ts.desc())
    ).first()
    if last and utcnow() - last.ts < min_interval:
        return
    s.add(EquitySnapshot(account_login=login, balance=balance, equity=equity, margin=margin,
                         free_margin=free_margin, source=source))


def equity_curve(s: Session, login: str, since: datetime | None = None, limit: int = 5000) -> list[EquitySnapshot]:
    q = select(EquitySnapshot).where(EquitySnapshot.account_login == login)
    if since:
        q = q.where(EquitySnapshot.ts >= since)
    rows = s.scalars(q.order_by(EquitySnapshot.ts.desc()).limit(limit)).all()
    return list(reversed(rows))


# ---------------------------------------------------------------------- symbols


def upsert_symbol_spec(s: Session, data: dict[str, Any]) -> SymbolSpecRecord:
    rec = s.get(SymbolSpecRecord, data["broker_symbol"])
    if rec is None:
        rec = SymbolSpecRecord(broker_symbol=data["broker_symbol"])
        s.add(rec)
    for k, v in data.items():
        if hasattr(rec, k):
            setattr(rec, k, v)
    rec.updated_at = utcnow()
    return rec


def symbol_specs(s: Session) -> list[SymbolSpecRecord]:
    return list(s.scalars(select(SymbolSpecRecord)).all())


def get_symbol_spec(s: Session, broker_symbol: str) -> SymbolSpecRecord | None:
    return s.get(SymbolSpecRecord, broker_symbol)


# -------------------------------------------------------------------- positions


def open_positions(s: Session, source: str) -> list[Position]:
    return list(s.scalars(select(Position).where(Position.source == source, Position.status == "OPEN")).all())


def get_position(s: Session, source: str, ticket: str) -> Position | None:
    return s.scalars(select(Position).where(Position.source == source, Position.ticket == ticket)).first()


def closed_positions(s: Session, source: str | None = None, limit: int = 1000, since: datetime | None = None) -> list[Position]:
    q = select(Position).where(Position.status == "CLOSED")
    if source:
        q = q.where(Position.source == source)
    if since:
        q = q.where(Position.closed_at >= since)
    return list(s.scalars(q.order_by(Position.closed_at.desc()).limit(limit)).all())


def positions(s: Session, status: str | None, source: str | None, limit: int) -> list[Position]:
    q = select(Position)
    if status:
        q = q.where(Position.status == status.upper())
    if source:
        q = q.where(Position.source == source.upper())
    return list(s.scalars(q.order_by(Position.opened_at.desc()).limit(limit)).all())


# ---------------------------------------------------------------------- signals


def add_signal(s: Session, **fields: Any) -> Signal:
    sig = Signal(**fields)
    s.add(sig)
    return sig


def get_signal(s: Session, signal_id: str) -> Signal | None:
    return s.scalars(select(Signal).where(Signal.signal_id == signal_id)).first()


def signal_for_bar(s: Session, symbol: str, timeframe: str, bar_time: datetime) -> Signal | None:
    return s.scalars(
        select(Signal).where(Signal.symbol == symbol, Signal.timeframe == timeframe, Signal.bar_time == bar_time)
        .order_by(Signal.created_at.desc())
    ).first()


def recent_signals(s: Session, limit: int = 100) -> list[Signal]:
    return list(s.scalars(select(Signal).order_by(Signal.created_at.desc()).limit(limit)).all())


# ----------------------------------------------------------------------- orders


def get_order_by_event(s: Session, event_id: str) -> Order | None:
    return s.scalars(select(Order).where(Order.event_id == event_id)).first()


def add_order(s: Session, **fields: Any) -> Order:
    order = Order(**fields)
    s.add(order)
    return order


def add_slippage(s: Session, **fields: Any) -> None:
    s.add(SlippageRecord(**fields))


def recent_slippage(s: Session, limit: int = 100) -> list[SlippageRecord]:
    return list(s.scalars(select(SlippageRecord).order_by(SlippageRecord.ts.desc()).limit(limit)).all())


# ---------------------------------------------------------------------- logging


def add_trade_log(s: Session, symbol: str, decision: str, reason: str, line: str, signal_id: str = "",
                  details: dict[str, Any] | None = None) -> None:
    s.add(TradeLog(symbol=symbol, decision=decision, reason=reason[:255], line=line, signal_id=signal_id,
                   details=details or {}))


def trade_logs(s: Session, limit: int = 200, decision: str | None = None) -> list[TradeLog]:
    q = select(TradeLog)
    if decision:
        q = q.where(TradeLog.decision == decision)
    return list(s.scalars(q.order_by(TradeLog.ts.desc()).limit(limit)).all())


def add_risk_event(s: Session, type_: str, reason: str, severity: str = "WARNING", account_login: str = "",
                   symbol: str = "", details: dict[str, Any] | None = None) -> None:
    s.add(RiskEvent(type=type_, reason=reason[:255], severity=severity, account_login=account_login, symbol=symbol,
                    details=details or {}))


def risk_events(s: Session, limit: int = 100) -> list[RiskEvent]:
    return list(s.scalars(select(RiskEvent).order_by(RiskEvent.ts.desc()).limit(limit)).all())


def add_system_event(s: Session, component: str, event: str, message: str = "", level: str = "INFO",
                     details: dict[str, Any] | None = None) -> None:
    s.add(SystemEvent(component=component, event=event, message=message[:512], level=level, details=details or {}))


def system_events(s: Session, limit: int = 100) -> list[SystemEvent]:
    return list(s.scalars(select(SystemEvent).order_by(SystemEvent.ts.desc()).limit(limit)).all())


# ------------------------------------------------------------------- risk state


def get_risk_state(s: Session, login: str) -> RiskState:
    state = s.get(RiskState, login)
    if state is None:
        state = RiskState(account_login=login)
        s.add(state)
        s.flush()
    return state


# -------------------------------------------------------------------- EA status


def get_ea_status(s: Session, login: str) -> EaStatus:
    st = s.get(EaStatus, login)
    if st is None:
        st = EaStatus(account_login=login)
        s.add(st)
    return st


def latest_ea_status(s: Session) -> EaStatus | None:
    return s.scalars(select(EaStatus).order_by(EaStatus.last_heartbeat.desc())).first()


# ------------------------------------------------------------------------- news


def upsert_news_events(s: Session, events: list[dict[str, Any]]) -> int:
    added = 0
    for ev in events:
        exists = s.scalars(select(NewsEventRecord).where(
            NewsEventRecord.provider == ev["provider"], NewsEventRecord.event == ev["event"],
            NewsEventRecord.country == ev["country"], NewsEventRecord.scheduled_at == ev["scheduled_at"],
        )).first()
        if exists:
            exists.actual, exists.forecast, exists.previous = ev["actual"], ev["forecast"], ev["previous"]
            exists.importance = ev["importance"]
        else:
            s.add(NewsEventRecord(**ev))
            added += 1
    return added


# ------------------------------------------------------------------ performance


def upsert_performance_day(s: Session, login: str, source: str, day: Any, profit: float) -> None:
    rec = s.scalars(select(PerformanceRecord).where(
        PerformanceRecord.account_login == login, PerformanceRecord.day == day, PerformanceRecord.source == source
    )).first()
    if rec is None:
        rec = PerformanceRecord(account_login=login, day=day, source=source, trades=0, wins=0, losses=0,
                                gross_profit=0.0, gross_loss=0.0, net=0.0)
        s.add(rec)
    rec.trades += 1
    if profit > 0:
        rec.wins += 1
        rec.gross_profit += profit
    else:
        rec.losses += 1
        rec.gross_loss += -profit
    rec.net += profit
    rec.updated_at = utcnow()


# ----------------------------------------------------------------- configuration


def config_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def record_config_version(s: Session, data: dict[str, Any], actor: str, note: str = "") -> ConfigurationVersion:
    h = config_hash(data)
    existing = s.scalars(select(ConfigurationVersion).where(ConfigurationVersion.config_hash == h)).first()
    if existing:
        existing.ts = utcnow()
        existing.actor, existing.note = actor, note
        return existing
    rec = ConfigurationVersion(config_hash=h, settings=data, actor=actor, note=note)
    s.add(rec)
    return rec


def latest_config_version(s: Session, note_prefix: str = "") -> ConfigurationVersion | None:
    q = select(ConfigurationVersion)
    if note_prefix:
        q = q.where(ConfigurationVersion.note.startswith(note_prefix))
    return s.scalars(q.order_by(ConfigurationVersion.ts.desc())).first()
