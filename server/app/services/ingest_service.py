"""Handles data the EA pushes: heartbeat, account, positions, trade events."""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.schemas import AccountIn, HeartbeatIn, HeartbeatOut, PositionsSyncIn, TradeEventIn
from app.config.settings import TradingMode
from app.database import repositories as repo
from app.database.models import Position
from app.domain import Direction
from app.execution.execution_state import SignalStatus, can_transition
from app.execution.slippage import slippage_points
from app.monitoring.alerts import Alert, AlertType, Severity
from app.portfolio.portfolio_manager import spec_from_record
from app.risk.drawdown import trading_day_for
from app.services.container import Container
from app.sessions.timezone import ensure_utc


def _ts(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


class IngestService:
    def __init__(self, c: Container):
        self.c = c

    # -------------------------------------------------------------- heartbeat
    def heartbeat(self, hb: HeartbeatIn, now: datetime) -> HeartbeatOut:
        c = self.c
        now = ensure_utc(now)
        with c.db.session() as s:
            st = repo.get_ea_status(s, hb.account_login)
            was_offline = st.last_heartbeat is None or \
                (now - st.last_heartbeat).total_seconds() > c.settings.heartbeat_timeout_seconds
            st.last_heartbeat = now
            st.ea_version, st.ea_mode = hb.ea_version, hb.ea_mode
            st.terminal_connected, st.trade_allowed = hb.terminal_connected, hb.trade_allowed
            st.local_kill_switch, st.latency_ms, st.open_positions = hb.local_kill_switch, hb.latency_ms, hb.open_positions
            st.quotes = {q.symbol: {"bid": q.bid, "ask": q.ask, "spread": q.spread_points, "time": q.time}
                         for q in hb.quotes}
            if hb.quotes:
                st.last_tick_at = _ts(max(q.time for q in hb.quotes)) if max(q.time for q in hb.quotes) else now
            if was_offline:
                repo.add_system_event(s, "ea", "EA_ONLINE", f"login={hb.account_login} v={hb.ea_version}")
        c.metrics.gauge("ea_latency_ms", hb.latency_ms)

        # Drive the paper broker with live quotes (no orders are sent anywhere).
        for q in hb.quotes:
            logical = c.resolver.to_logical(q.symbol)
            if logical and logical in {p.symbol for p in c.paper.sim.positions.values()}:
                c.paper.on_quote(logical, q.bid, q.ask, c.last_atr.get(logical, 0.0), now)
        if c.paper.sim.positions or hb.quotes:
            with c.db.session() as s:
                acc = repo.get_account(s, repo.PAPER_LOGIN)
                equity = acc.equity if acc else 0.0
            if equity > 0:
                c.risk_state.update_equity(repo.PAPER_LOGIN, equity, now)

        controls = c.risk_state.controls()
        if controls.close_positions and c.paper.sim.positions:
            c.paper.close_all("KILL_SWITCH_CLOSE", now)
        mode = c.settings.effective_trading_mode
        return HeartbeatOut(
            server_time=now, trading_mode=mode.value, live_permitted=c.settings.live_trading_permitted,
            kill_switch=controls.kill_switch, close_positions=controls.close_positions, paused=controls.paused,
            safe_mode=controls.safe_mode,
            reason=controls.kill_reason or controls.safe_mode_reason or ("PAPER MODE" if mode is TradingMode.PAPER else ""),
        )

    # ---------------------------------------------------------------- account
    def account(self, a: AccountIn, now: datetime) -> dict[str, object]:
        c = self.c
        warnings: list[str] = []
        if a.margin_mode != "HEDGING":
            warnings.append("ACCOUNT_NOT_HEDGING: trading will be refused")
        if c.settings.effective_trading_mode is TradingMode.DEMO and a.trade_mode != "DEMO":
            warnings.append("DEMO mode configured but the MT5 account is not a demo account")
        with c.db.session() as s:
            repo.upsert_account(s, {**a.model_dump(exclude={"symbols"}), "source": "MT5"})
            repo.add_equity_snapshot(s, a.login, a.balance, a.equity, a.margin, a.free_margin, "MT5")
            known = set(c.resolver.broker_symbols())
            for spec in a.symbols:
                logical = c.resolver.to_logical(spec.broker_symbol)
                if logical is None:
                    warnings.append(f"UNMAPPED_SYMBOL:{spec.broker_symbol}")
                    continue
                data = spec.model_dump()
                data["logical_symbol"] = logical
                data["trade_mode"] = spec.trade_mode
                repo.upsert_symbol_spec(s, data)
            reported = {sp.broker_symbol for sp in a.symbols}
            for missing in sorted(known - reported):
                if repo.get_symbol_spec(s, missing) is None:
                    warnings.append(f"SYMBOL_NOT_REPORTED:{missing}")
        if a.symbols and not c.paper.sim.positions:
            with c.db.session() as s:
                c.paper.bootstrap({sp.symbol: sp for sp in (spec_from_record(r) for r in repo.symbol_specs(s))})
        check = c.risk_state.update_equity(a.login, a.equity, now)
        st = c.risk_state.equity_state(a.login)
        return {
            "accepted": True,
            "warnings": warnings,
            "daily_loss": round(check.daily_loss_fraction, 5),
            "drawdown": round(check.drawdown_fraction, 5),
            "locks": {"daily_loss": st.daily_loss_locked, "drawdown": st.drawdown_locked},
        }

    # -------------------------------------------------------------- positions
    def positions(self, body: PositionsSyncIn, now: datetime) -> dict[str, int]:
        """Full sync of the EA's OWN open positions (by magic number)."""
        c = self.c
        seen: set[str] = set()
        created = updated = closed = 0
        with c.db.session() as s:
            for p in body.positions:
                seen.add(p.ticket)
                row = repo.get_position(s, "MT5", p.ticket)
                logical = c.resolver.to_logical(p.symbol) or ""
                if row is None:
                    sig = repo.get_signal(s, p.comment.split("|")[-1]) if "|" in p.comment else None
                    row = Position(
                        ticket=p.ticket, source="MT5", account_login=body.account_login, symbol=p.symbol,
                        logical_symbol=logical, magic=p.magic, direction=p.direction, volume=p.volume,
                        initial_volume=p.volume, entry_price=p.entry_price, initial_sl=p.sl, opened_at=_ts(p.opened_at),
                        strategy=sig.strategy if sig else "", signal_id=sig.signal_id if sig else "",
                        confidence=sig.confidence if sig else 0.0, session=sig.session if sig else "",
                        regime=sig.regime if sig else "",
                    )
                    s.add(row)
                    created += 1
                else:
                    updated += 1
                row.volume, row.current_price, row.sl, row.tp = p.volume, p.current_price, p.sl, p.tp
                row.profit, row.swap, row.commission = p.profit, p.swap, p.commission
                row.updated_at = now
                if row.initial_sl and row.risk_money == 0 and logical:
                    spec_rec = repo.get_symbol_spec(s, p.symbol)
                    if spec_rec:
                        spec = spec_from_record(spec_rec)
                        row.risk_money = abs(row.entry_price - row.initial_sl) * spec.money_per_price_unit(row.initial_volume)
                if row.sl and row.initial_sl and (row.sl - row.entry_price) * Direction(row.direction).sign >= 0:
                    row.be_applied = True
            for row in repo.open_positions(s, "MT5"):
                if row.account_login == body.account_login and row.ticket not in seen:
                    # Closed between syncs; the CLOSED trade event carries the final P/L.
                    row.status = "CLOSED"
                    row.closed_at = row.closed_at or now
                    row.close_reason = row.close_reason or "SYNC_NOT_OPEN"
                    closed += 1
        return {"created": created, "updated": updated, "closed": closed}

    # ----------------------------------------------------------------- trades
    def trade_event(self, ev: TradeEventIn, now: datetime) -> dict[str, object]:
        c = self.c
        with c.db.session() as s:
            if repo.get_order_by_event(s, ev.event_id) is not None:
                return {"accepted": True, "duplicate": True}
            spec_rec = repo.get_symbol_spec(s, ev.symbol)
            point = spec_rec.point if spec_rec else 0.0
            d = Direction(ev.direction)
            slip = slippage_points(d, ev.requested_price, ev.executed_price, point) \
                if ev.requested_price and ev.executed_price else 0.0
            repo.add_order(s, event_id=ev.event_id, signal_id=ev.signal_id, ticket=ev.ticket, source="MT5",
                           symbol=ev.symbol, direction=ev.direction, action=ev.event, volume=ev.volume,
                           requested_price=ev.requested_price, executed_price=ev.executed_price, slippage_points=slip,
                           status="REJECTED" if ev.event == "REJECTED" else "FILLED", retcode=ev.retcode,
                           comment=ev.reason)
            sig = repo.get_signal(s, ev.signal_id) if ev.signal_id else None
            if sig is not None:
                target = {"OPENED": SignalStatus.EXECUTED, "REJECTED": SignalStatus.REJECTED_BY_EA}.get(ev.event)
                if target and can_transition(sig.status, target.value):
                    sig.status = target.value
            logical = c.resolver.to_logical(ev.symbol) or ev.symbol
            row = repo.get_position(s, "MT5", ev.ticket) if ev.ticket else None
            if ev.event == "OPENED":
                repo.add_slippage(s, request_id=ev.event_id, ticket=ev.ticket, symbol=ev.symbol,
                                  direction=ev.direction, volume=ev.volume, requested_price=ev.requested_price,
                                  executed_price=ev.executed_price, slippage_points=slip)
                if row is None and ev.ticket:
                    row = Position(ticket=ev.ticket, source="MT5", account_login=ev.account_login, symbol=ev.symbol,
                                   logical_symbol=logical, magic=ev.magic, direction=ev.direction, volume=ev.volume,
                                   initial_volume=ev.volume, entry_price=ev.executed_price, current_price=ev.executed_price,
                                   sl=ev.sl, tp=ev.tp, initial_sl=ev.sl, opened_at=_ts(ev.time),
                                   strategy=sig.strategy if sig else "", signal_id=ev.signal_id,
                                   confidence=sig.confidence if sig else 0.0, session=sig.session if sig else "",
                                   regime=sig.regime if sig else "", tp2=sig.take_profit_2 if sig else 0.0)
                    if spec_rec and ev.sl:
                        row.risk_money = abs(ev.executed_price - ev.sl) * spec_from_record(spec_rec).money_per_price_unit(ev.volume)
                    s.add(row)
                st = repo.get_ea_status(s, ev.account_login)
                st.last_trade_at = now
            elif ev.event in ("CLOSED", "PARTIAL") and row is not None:
                row.realized_profit += ev.profit
                if ev.event == "CLOSED":
                    row.status, row.closed_at = "CLOSED", _ts(ev.time)
                    row.close_price, row.close_reason = ev.executed_price, ev.reason or "CLOSED"
                    row.profit = row.realized_profit
                    row.r_multiple = row.realized_profit / row.risk_money if row.risk_money > 0 else None
                    day = trading_day_for(_ts(ev.time), c.settings.trading_day_timezone,
                                          c.settings.trading_day_rollover_hour)
                    repo.upsert_performance_day(s, ev.account_login, "MT5", day, row.realized_profit)
                    if row.r_multiple is not None and sig is not None and sig.model_version not in ("", "rule-based"):
                        c.inference.health.record_trade_result(row.r_multiple)
                else:
                    row.partial_done = True
                    row.volume = max(0.0, row.volume - ev.volume)
            elif ev.event == "MODIFIED" and row is not None:
                row.sl, row.tp = ev.sl, ev.tp
            elif ev.event == "REJECTED":
                repo.add_risk_event(s, "EA_REJECTED_ORDER", ev.reason or f"retcode={ev.retcode}", "WARNING",
                                    ev.account_login, ev.symbol)
        if ev.event == "OPENED" and slip:
            status = c.slippage.record(ev.symbol, slip)
            if status.abnormal:
                c.risk_state.set_safe_mode(f"ABNORMAL_SLIPPAGE {ev.symbol} mean={status.mean_points:.1f}pts")
                c.notifier.notify(Alert(AlertType.SLIPPAGE_ABNORMAL, f"{ev.symbol} mean slippage "
                                                                     f"{status.mean_points:.1f} points", Severity.CRITICAL))
        if ev.event in ("OPENED", "CLOSED"):
            c.notifier.notify(Alert(AlertType.TRADE_OPENED if ev.event == "OPENED" else AlertType.TRADE_CLOSED,
                                    f"{ev.event} {ev.direction} {ev.volume} {ev.symbol} @ {ev.executed_price}"
                                    + (f" P/L={ev.profit:.2f}" if ev.event == "CLOSED" else "")))
        return {"accepted": True, "duplicate": False, "slippage_points": round(slip, 2)}
