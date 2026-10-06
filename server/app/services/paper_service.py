"""PAPER mode broker: simulated fills, P/L, slippage and fees.

No broker order is ever sent in PAPER mode. Positions, the simulated account
and every fill are persisted (source="PAPER") so the dashboard shows them and
risk limits apply exactly as in DEMO/LIVE.
"""

from __future__ import annotations

import threading
from datetime import datetime

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.database import repositories as repo
from app.database.database import Database
from app.database.models import Position
from app.domain import Direction, SymbolSpec
from app.execution.simulator import ClosedTrade, CostModel, SimEvent, SimPosition, TradeSimulator, pnl_money
from app.execution.slippage import slippage_points
from app.execution.trade_management import ManagementParams
from app.monitoring.alerts import Alert, AlertType, Notifier, Severity
from app.risk.drawdown import trading_day_for

PAPER = "PAPER"


def management_params(settings: Settings) -> ManagementParams:
    return ManagementParams(
        tighten_trigger_r=settings.tighten_trigger_r, tighten_to_r=settings.tighten_to_r,
        be_trigger_r=settings.be_trigger_r, be_offset_r=settings.be_offset_r, trail_start_r=settings.trail_start_r,
        trail_atr_mult=settings.trail_atr_multiplier, tp1_r=settings.tp1_r,
        tp1_close_fraction=settings.tp1_close_fraction,
    )


class PaperTradingService:
    def __init__(self, db: Database, settings: Settings, notifier: Notifier):
        self.db = db
        self.settings = settings
        self.notifier = notifier
        self.sim = TradeSimulator(
            CostModel(commission_per_lot=settings.paper_commission_per_lot,
                      slippage_points=settings.paper_slippage_points),
            management_params(settings),
        )
        self._lock = threading.Lock()
        self.last_prices: dict[str, tuple[float, float]] = {}

    # --------------------------------------------------------------- startup
    def bootstrap(self, specs: dict[str, SymbolSpec]) -> None:
        """Create the paper account if needed and reload open paper positions."""
        with self.db.session() as s:
            acc = repo.get_account(s, repo.PAPER_LOGIN)
            if acc is None:
                eq = self.settings.initial_equity
                repo.upsert_account(s, {"login": repo.PAPER_LOGIN, "server": "paper", "company": "simulation",
                                        "currency": "USD", "trade_mode": "PAPER", "margin_mode": "HEDGING",
                                        "trade_allowed": True, "balance": eq, "equity": eq, "free_margin": eq,
                                        "source": PAPER})
            rows = repo.open_positions(s, PAPER)
        with self._lock:
            for r in rows:
                spec = specs.get(r.logical_symbol)
                if spec is None or r.ticket in self.sim.positions:
                    continue
                self.sim.positions[r.ticket] = SimPosition(
                    ticket=r.ticket, symbol=r.logical_symbol, direction=Direction(r.direction), volume=r.volume,
                    entry=r.entry_price, sl=r.sl, tp=r.tp, tp1=self._tp1(r), strategy=r.strategy,
                    opened_at=r.opened_at, spec=spec, initial_sl=r.initial_sl, initial_volume=r.initial_volume,
                    commission=r.commission, risk_money=r.risk_money, signal_id=r.signal_id, session=r.session,
                    regime=r.regime, confidence=r.confidence, partial_done=r.partial_done, be_done=r.be_applied,
                )

    def _tp1(self, r: Position) -> float:
        risk = abs(r.entry_price - r.initial_sl)
        return r.entry_price + (1 if r.direction == "BUY" else -1) * risk * self.settings.tp1_r

    # ------------------------------------------------------------------ open
    def open_from_signal(self, *, signal_id: str, symbol: str, broker_symbol: str, direction: Direction,
                         volume: float, bid: float, ask: float, sl: float, tp1: float, tp2: float, strategy: str,
                         spec: SymbolSpec, risk_money: float, session: str, regime: str, confidence: float,
                         now: datetime) -> SimPosition:
        with self._lock:
            requested = ask if direction is Direction.BUY else bid
            fill = self.sim.fill_price(direction, bid, ask, spec)
            # Stops/targets are not re-anchored after slippage: slippage simply worsens the R.
            pos = SimPosition(
                ticket=f"P-{signal_id[:12]}", symbol=symbol, direction=direction, volume=volume, entry=fill,
                sl=sl, tp=tp2, tp1=tp1, strategy=strategy, opened_at=now, spec=spec, risk_money=risk_money,
                signal_id=signal_id, session=session, regime=regime, confidence=confidence, requested_price=requested,
            )
            self.sim.open(pos)
            with self.db.session() as s:
                s.add(Position(
                    ticket=pos.ticket, source=PAPER, account_login=repo.PAPER_LOGIN, symbol=broker_symbol,
                    logical_symbol=symbol, magic=0, strategy=strategy, direction=direction.value, volume=volume,
                    initial_volume=volume, entry_price=fill, current_price=fill, sl=sl, tp=tp2, initial_sl=sl,
                    tp2=tp2, risk_money=risk_money, commission=pos.commission, signal_id=signal_id,
                    confidence=confidence, session=session, regime=regime, opened_at=now,
                ))
                slip = slippage_points(direction, requested, fill, spec.point)
                repo.add_order(s, event_id=f"paper-open-{signal_id}", signal_id=signal_id, ticket=pos.ticket,
                               source=PAPER, symbol=broker_symbol, direction=direction.value, action="OPEN",
                               volume=volume, requested_price=requested, executed_price=fill, slippage_points=slip,
                               status="FILLED")
                repo.add_slippage(s, request_id=signal_id, ticket=pos.ticket, symbol=broker_symbol,
                                  direction=direction.value, volume=volume, requested_price=requested,
                                  executed_price=fill, slippage_points=slip)
            self.last_prices[symbol] = (bid, ask)
        self.notifier.notify(Alert(AlertType.TRADE_OPENED, f"[PAPER] {direction.value} {volume} {symbol} @ {fill}",
                                   data={"signal_id": signal_id, "strategy": strategy}))
        return pos

    # ---------------------------------------------------------------- prices
    def on_quote(self, symbol: str, bid: float, ask: float, atr: float, now: datetime) -> list[SimEvent]:
        with self._lock:
            self.last_prices[symbol] = (bid, ask)
            events = self.sim.on_bar(symbol, now, bid, bid, bid, bid, ask - bid, atr)
            self._persist(events, now)
        return events

    def close_all(self, reason: str, now: datetime) -> list[SimEvent]:
        with self._lock:
            events = self.sim.close_all(self.last_prices, now, reason)
            self._persist(events, now)
        return events

    def _persist(self, events: list[SimEvent], now: datetime) -> None:
        with self.db.session() as s:
            for ev in events:
                row = repo.get_position(s, PAPER, ev.ticket)
                if row is None:
                    continue
                pos = self.sim.positions.get(ev.ticket)
                if ev.kind == "MODIFY" and pos:
                    row.sl, row.be_applied = pos.sl, pos.be_done
                elif ev.closed is not None:
                    self._record_close(s, row, ev.closed, now)
                    if pos:
                        row.volume, row.partial_done = pos.volume, pos.partial_done
                row.updated_at = now
            # Flush first: the session does not autoflush, and the closes above must be
            # visible to the open-positions query or stale floating P/L would leak into equity.
            s.flush()
            # Refresh floating P/L and the paper account.
            for row in repo.open_positions(s, PAPER):
                pos = self.sim.positions.get(row.ticket)
                if pos is None:
                    continue
                bid, ask = self.last_prices.get(pos.symbol, (0.0, 0.0))
                if bid > 0:
                    price = bid if pos.direction is Direction.BUY else ask
                    row.current_price = price
                    row.profit = pnl_money(pos.spec, pos.direction, pos.entry, price, pos.volume) - pos.commission
            acc = repo.get_account(s, repo.PAPER_LOGIN)
            if acc is not None:
                acc.equity = acc.balance + sum(r.profit for r in repo.open_positions(s, PAPER))
                acc.free_margin = acc.equity - acc.margin
                acc.updated_at = now
                repo.add_equity_snapshot(s, acc.login, acc.balance, acc.equity, acc.margin, acc.free_margin, PAPER)

    def _record_close(self, s: Session, row: Position, closed: ClosedTrade, now: datetime) -> None:
        acc = repo.get_account(s, repo.PAPER_LOGIN)
        if acc is not None:
            acc.balance += closed.profit
        row.realized_profit += closed.profit
        repo.add_order(s, event_id=f"paper-{closed.reason.lower()}-{row.ticket}-{now.timestamp():.0f}-{closed.volume}",
                       signal_id=row.signal_id, ticket=row.ticket, source=PAPER, symbol=row.symbol,
                       direction=row.direction, action="CLOSE" if closed.final else "PARTIAL", volume=closed.volume,
                       requested_price=closed.exit, executed_price=closed.exit, status="FILLED",
                       comment=closed.reason)
        if closed.final:
            row.status = "CLOSED"
            row.closed_at = now
            row.close_price = closed.exit
            row.current_price = closed.exit
            row.close_reason = closed.reason
            row.profit = row.realized_profit
            row.volume = 0.0
            row.r_multiple = row.realized_profit / row.risk_money if row.risk_money > 0 else None
            day = trading_day_for(now, self.settings.trading_day_timezone, self.settings.trading_day_rollover_hour)
            repo.upsert_performance_day(s, repo.PAPER_LOGIN, PAPER, day, row.realized_profit)
            r = row.r_multiple or 0.0
            alert = AlertType.TRADE_CLOSED
            severity = Severity.INFO
            if r >= self.settings.large_profit_r:
                alert = AlertType.LARGE_PROFIT
            elif r <= -self.settings.large_loss_r:
                alert, severity = AlertType.LARGE_LOSS, Severity.WARNING
            self.notifier.notify(Alert(alert, f"[PAPER] closed {row.logical_symbol} {row.direction} "
                                              f"P/L={row.realized_profit:.2f} ({r:.2f}R) {closed.reason}", severity))
