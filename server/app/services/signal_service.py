"""The decision pipeline behind ``POST /v1/signal``.

Every candidate trade passes, in order:
  1 market hours   2 session        3 spread          4 news
  5 account risk   6 portfolio      7 strategy signal 8 model confidence
  9 stop loss     10 position size 11 margin         12 execution validation
(9-11 are performed by the risk engine, which has final authority.)

The first failing gate produces ``NO_TRADE`` with an explicit reason, and the
full key=value decision line is stored in ``trade_logs``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from app.ai.features import FeatureConfig
from app.ai.inference import ModelMode
from app.ai.probability import Probabilities, confirms
from app.ai.regime_model import classify_row
from app.api.schemas import SignalRequest, SignalResponse
from app.config.settings import TradingMode
from app.database import repositories as repo
from app.database.models import ModelPrediction, Signal
from app.domain import AccountState, TradeProposal
from app.execution.execution_state import SignalStatus
from app.execution.order_validation import validate_order
from app.monitoring.logger import decision_line, get_logger
from app.portfolio.portfolio_manager import position_from_record, spec_from_record
from app.risk.drawdown import in_cooldown
from app.services.container import Container
from app.sessions.market_hours import market_status
from app.sessions.timezone import ensure_utc
from app.strategies.base import StrategyContext, StrategyParams

log = get_logger(__name__)

_MODE_RANK = {TradingMode.PAPER: 0, TradingMode.DEMO: 1, TradingMode.LIVE: 2}


def new_signal_id() -> str:
    """24 hex chars (96 random bits): fits in an MT5 order comment as ``PMX|<id>`` (31-char limit),
    which lets the EA detect already-executed signals even after a restart."""
    return uuid.uuid4().hex[:24]


class UnknownSymbolError(ValueError):
    pass


@dataclass
class Decision:
    fields: dict[str, object] = field(default_factory=dict)

    def add(self, **kw: object) -> None:
        self.fields.update(kw)


def bars_frame(raw: list[list[float]]) -> pd.DataFrame:
    arr = np.asarray(raw, dtype=float)
    df = pd.DataFrame(arr, columns=["epoch", "open", "high", "low", "close", "tick_volume", "spread"])
    df["time"] = pd.to_datetime(df["epoch"].astype("int64"), unit="s", utc=True)
    return df.drop(columns=["epoch"])


def effective_mode(backend: TradingMode, ea_mode: str) -> TradingMode:
    """The most conservative of the backend and EA modes wins."""
    ea = TradingMode(ea_mode)
    return backend if _MODE_RANK[backend] <= _MODE_RANK[ea] else ea


class SignalService:
    def __init__(self, c: Container):
        self.c = c

    def process(self, req: SignalRequest, now: datetime, request_id: str = "") -> SignalResponse:
        c, st = self.c, self.c.settings
        now = ensure_utc(now)
        logical = c.resolver.to_logical(req.symbol)
        if logical is None:
            raise UnknownSymbolError(f"INVALID_SYMBOL: {req.symbol} is not configured")
        mode = effective_mode(st.effective_trading_mode, req.ea_mode)
        paper = mode is TradingMode.PAPER
        d = Decision()
        d.add(symbol=logical, broker_symbol=req.symbol, tf=req.timeframe, mode=mode.value)

        bars = bars_frame(req.bars)
        bar_time = bars["time"].iloc[-1].to_pydatetime()

        # Duplicate protection: one decision per (symbol, timeframe, closed bar).
        with c.db.session() as s:
            existing = repo.signal_for_bar(s, req.symbol, req.timeframe, bar_time)
            if existing is not None:
                if existing.expires_at > now:
                    c.metrics.inc("signal_duplicate_requests")
                    return self._response(existing, existing.decision_log)
                return self._no_trade_response(req, logical, now, mode, "DUPLICATE_BAR_REQUEST", "", d)
            spec_rec = repo.get_symbol_spec(s, req.symbol)
            spec = spec_from_record(spec_rec) if spec_rec else None

        if spec is None:
            return self._finish_no_trade(req, logical, now, mode, "SYMBOL_SPEC_UNKNOWN", d, bar_time)
        features = c.feature_cache.get_or_compute(logical, req.timeframe, bars, config=FeatureConfig(point=spec.point),
                                                  session_engine=c.session_engine)
        c.correlation.update(logical, pd.Series(bars["close"].to_numpy(), index=pd.DatetimeIndex(bars["time"])))
        last = features.iloc[-1]
        atr = float(last["atr"]) if pd.notna(last["atr"]) else 0.0
        c.last_atr[logical] = atr
        regime = classify_row(last)
        c.last_regime[logical] = regime.regime.value
        session = c.session_engine.classify(now)
        d.add(session=session.primary.value, regime=regime.regime.value, atr=round(atr, 5))

        def no(reason: str) -> SignalResponse:
            return self._finish_no_trade(req, logical, now, mode, reason, d, bar_time, session.primary.value,
                                         regime.regime.value)

        # 1. market hours -------------------------------------------------------
        mkt = market_status(now)
        if not mkt.open:
            return no(f"MARKET_CLOSED:{mkt.reason}")
        if not req.market_open:
            return no("MARKET_CLOSED:BROKER_SESSION")
        if not spec.trade_allowed:
            return no("SYMBOL_NOT_TRADABLE")
        if not c.symbol_enabled(logical):
            return no("SYMBOL_DISABLED")
        d.add(market="open")

        # 2. session ----------------------------------------------------------
        if not c.session_enabled(session.primary.value):
            return no(f"SESSION_DISABLED:{session.primary.value}")
        d.add(activity=session.activity.value)

        # 3. spread -----------------------------------------------------------
        spread_price = req.ask - req.bid
        if st.max_spread_points > 0 and req.spread_points > st.max_spread_points:
            d.add(spread=f"{req.spread_points:.0f}pts")
            return no("SPREAD_TOO_HIGH")
        if atr <= 0:
            return no("ATR_UNAVAILABLE")
        spread_ratio = spread_price / atr
        d.add(spread=f"{spread_ratio:.3f}xATR")
        if spread_ratio > st.max_spread_atr_ratio:
            return no("SPREAD_TOO_HIGH")

        # 4. news -------------------------------------------------------------
        c.calendar.refresh_if_due(now)
        news = c.news_filter.check(logical, now)
        d.add(news="clear" if not news.blocked else news.reason)
        if news.blocked:
            return no(news.reason)

        # 5. account risk -----------------------------------------------------
        login = repo.PAPER_LOGIN if paper else req.account_login
        controls = c.risk_state.controls()
        if controls.kill_switch:
            return no("KILL_SWITCH_ACTIVE")
        if controls.paused:
            return no("NEW_TRADES_PAUSED")
        if controls.safe_mode:
            return no(f"SAFE_MODE:{controls.safe_mode_reason}")
        with c.db.session() as s:
            ea = repo.get_ea_status(s, req.account_login)
            hb = ea.last_heartbeat
            acc = repo.get_account(s, login)
            account = AccountState(
                login=login, balance=acc.balance, equity=acc.equity, margin=acc.margin, free_margin=acc.free_margin,
                currency=acc.currency, leverage=acc.leverage, margin_mode=acc.margin_mode,
                trade_allowed=acc.trade_allowed,
            ) if acc else None
            source = "PAPER" if paper else "MT5"
            positions = [position_from_record(p) for p in repo.open_positions(s, source)]
            specs = {sp.symbol: sp for sp in (spec_from_record(r) for r in repo.symbol_specs(s))}
        if hb is None or (now - hb).total_seconds() > st.heartbeat_timeout_seconds:
            return no("EA_HEARTBEAT_STALE")
        if account is None or account.equity <= 0:
            return no("ACCOUNT_STATE_UNKNOWN")
        if not paper and account.margin_mode != "HEDGING":
            return no("ACCOUNT_NOT_HEDGING")
        eq_check = c.risk_state.update_equity(login, account.equity, now)
        equity_state = c.risk_state.equity_state(login)
        d.add(daily_loss=f"{eq_check.daily_loss_fraction:.2%}", drawdown=f"{eq_check.drawdown_fraction:.2%}")
        if equity_state.drawdown_locked:
            return no("MAX_DRAWDOWN_REACHED")
        if equity_state.daily_loss_locked:
            return no("MAX_DAILY_LOSS_REACHED")
        d.add(loss_streak=equity_state.consecutive_losses)
        if in_cooldown(equity_state, now):
            return no(f"LOSS_STREAK_COOLDOWN until {equity_state.cooldown_until:%H:%M} UTC")

        # 6. portfolio pre-check ------------------------------------------------
        own = [p for p in positions if p.own]
        d.add(open_positions=len(own))
        if len(own) >= c.limits.max_positions:
            return no("MAX_POSITIONS_REACHED")

        # 7. strategy signal ----------------------------------------------------
        params = StrategyParams(sl_atr_mult=st.sl_atr_multiplier, tp1_r=st.tp1_r, tp2_r=st.tp2_r,
                                max_spread_atr=st.max_spread_atr_ratio)
        ctx = StrategyContext(symbol=logical, timeframe=req.timeframe, features=features, regime=regime.regime,
                              session=session, bid=req.bid, ask=req.ask, spec=spec, params=params,
                              session_engine=c.session_engine)
        sig, notes = c.router.evaluate(ctx)
        d.add(strategies="|".join(notes))
        if sig is None:
            return no("NO_SETUP")
        d.add(strategy=sig.strategy, setup=sig.reason)

        # 8. model confidence ---------------------------------------------------
        probs: Probabilities | None = None
        confidence = 0.0
        model_mode = c.inference.mode
        if model_mode is ModelMode.MODEL:
            probs = c.inference.predict(features)
            if probs is None:
                return no("MODEL_PREDICTION_UNAVAILABLE")
            decision = confirms(probs, sig.direction, c.confidence_threshold(), st.min_probability_edge,
                                st.max_hold_probability)
            d.add(ai_buy=f"{probs.p_buy:.2f}", ai_sell=f"{probs.p_sell:.2f}", ai_hold=f"{probs.p_hold:.2f}")
            if decision.direction is None:
                return no(decision.reason)
            confidence = decision.confidence
        elif model_mode is ModelMode.RULE_BASED:
            if mode is TradingMode.LIVE and not st.allow_rule_based_in_live:
                return no("MODEL_REQUIRED_FOR_LIVE")
            d.add(ai="unavailable->RULE_BASED")
        else:
            return no("MODEL_SAFE_MODE")
        c.last_confidence[logical] = confidence

        # Snap stop and targets to the broker's tick grid (as the order will be placed).
        def snap(price: float) -> float:
            return round(round(price / spec.tick_size) * spec.tick_size, spec.digits)

        sig.stop_loss, sig.take_profit, sig.take_profit_2 = snap(sig.stop_loss), snap(sig.take_profit), \
            snap(sig.take_profit_2)

        # 9-11. risk engine -----------------------------------------------------
        proposal = TradeProposal(
            symbol=logical, direction=sig.direction, entry_price=sig.entry, stop_loss=sig.stop_loss,
            take_profit=sig.take_profit_2 or sig.take_profit, take_profit_2=sig.take_profit_2,
            strategy=sig.strategy, confidence=confidence,
            risk_scale=sig.risk_scale * c.allocation.scale_for(logical, sig.strategy),
        )
        rd = c.risk_engine.evaluate(proposal, account, positions, specs, equity_state, controls.flags(), now)
        d.add(**rd.log_fields())
        if not rd.approved:
            return no(rd.reason)
        d.add(trade_risk=f"{rd.risk_fraction:.3%}", position_size=rd.volume)

        # 12. execution validation ---------------------------------------------
        check = validate_order(sig.direction, rd.volume, sig.entry, sig.stop_loss, proposal.take_profit, spec,
                               req.bid, req.ask)
        if not check.ok:
            return no(check.reason)
        d.add(decision=sig.direction.value)
        return self._issue(req, logical, now, mode, d, bar_time, session.primary.value, regime.regime.value, sig,
                           proposal, rd.volume, rd.risk_money, rd.risk_fraction, confidence, probs, atr, spec,
                           request_id)

    # ------------------------------------------------------------------ issue
    def _issue(self, req, logical, now, mode, d, bar_time, session, regime, sig, proposal, volume, risk_money,  # type: ignore[no-untyped-def]
               risk_fraction, confidence, probs, atr, spec, request_id) -> SignalResponse:
        c, st = self.c, self.c.settings
        paper = mode is TradingMode.PAPER
        signal_id = new_signal_id()
        line = decision_line(d.fields)
        tp1 = sig.entry + sig.direction.sign * abs(sig.entry - sig.stop_loss) * st.tp1_r
        with c.db.session() as s:
            row = repo.add_signal(
                s, signal_id=signal_id, request_id=request_id, created_at=now,
                expires_at=now + timedelta(seconds=st.signal_ttl_seconds), symbol=req.symbol, logical_symbol=logical,
                timeframe=req.timeframe, bar_time=bar_time, account_login=req.account_login,
                action=sig.direction.value, strategy=sig.strategy, model_version=c.inference.version,
                confidence=confidence, p_buy=probs.p_buy if probs else 0.0, p_sell=probs.p_sell if probs else 0.0,
                p_hold=probs.p_hold if probs else 0.0, regime=regime, session=session, entry_price=sig.entry,
                stop_loss=sig.stop_loss, take_profit=proposal.take_profit, take_profit_2=sig.take_profit_2,
                volume=volume, risk_percent=risk_fraction * 100, risk_approved=True, trading_mode=mode.value,
                reason=sig.reason, decision_log=line, status=SignalStatus.ISSUED.value,
            )
            if probs is not None:
                s.add(ModelPrediction(signal_id=signal_id, model_id=c.inference.meta.model_id if c.inference.meta else "",
                                      model_version=c.inference.version, symbol=logical, p_buy=probs.p_buy,
                                      p_sell=probs.p_sell, p_hold=probs.p_hold, regime=regime))
            repo.add_trade_log(s, logical, sig.direction.value, "APPROVED", line, signal_id, d.fields)
        c.metrics.inc("signals_issued")
        log.info(line, extra={"signal_id": signal_id})
        if paper:
            c.paper.open_from_signal(
                signal_id=signal_id, symbol=logical, broker_symbol=req.symbol, direction=sig.direction,
                volume=volume, bid=req.bid, ask=req.ask, sl=sig.stop_loss, tp1=tp1, tp2=proposal.take_profit,
                strategy=sig.strategy, spec=spec, risk_money=risk_money, session=session, regime=regime,
                confidence=confidence, now=now,
            )
            with c.db.session() as s:
                stored = repo.get_signal(s, signal_id)
                if stored:
                    stored.status = SignalStatus.PAPER_FILLED.value
                    row = stored
        return self._response(row, line, atr=atr)

    def _finish_no_trade(self, req: SignalRequest, logical: str, now: datetime, mode: TradingMode, reason: str,
                         d: Decision, bar_time: datetime, session: str = "", regime: str = "") -> SignalResponse:
        d.add(decision="NO_TRADE", reason=reason)
        line = decision_line(d.fields)
        signal_id = new_signal_id()
        with self.c.db.session() as s:
            row = repo.add_signal(
                s, signal_id=signal_id, created_at=now, expires_at=now, symbol=req.symbol, logical_symbol=logical,
                timeframe=req.timeframe, bar_time=bar_time, account_login=req.account_login, action="NO_TRADE",
                model_version=self.c.inference.version, regime=regime, session=session, trading_mode=mode.value,
                reason=reason[:255], decision_log=line, status=SignalStatus.NO_TRADE.value,
            )
            repo.add_trade_log(s, logical, "REJECT", reason, line, signal_id, d.fields)
        self.c.metrics.inc("signals_rejected")
        log.info(line)
        return self._response(row, line)

    def _no_trade_response(self, req: SignalRequest, logical: str, now: datetime, mode: TradingMode, reason: str,
                           strategy: str, d: Decision) -> SignalResponse:
        """NO_TRADE answer that is not persisted as a new bar decision (duplicates)."""
        d.add(decision="NO_TRADE", reason=reason)
        row = Signal(signal_id=new_signal_id(), created_at=now, expires_at=now, symbol=req.symbol,
                     logical_symbol=logical, timeframe=req.timeframe, action="NO_TRADE", strategy=strategy,
                     model_version=self.c.inference.version, trading_mode=mode.value, reason=reason)
        return self._response(row, decision_line(d.fields))

    def _response(self, row: Signal, line: str, atr: float | None = None) -> SignalResponse:
        st = self.c.settings
        logical = row.logical_symbol or row.symbol
        return SignalResponse(
            signal_id=row.signal_id, created_at=row.created_at, expires_at=row.expires_at,
            created_at_epoch=int(row.created_at.timestamp()), expires_at_epoch=int(row.expires_at.timestamp()),
            symbol=row.symbol,
            timeframe=row.timeframe, action=row.action, confidence=row.confidence or 0.0, p_buy=row.p_buy or 0.0,
            p_sell=row.p_sell or 0.0, p_hold=row.p_hold or 0.0, strategy=row.strategy or "",
            model_version=row.model_version or "", regime=row.regime or "", session=row.session or "",
            risk_approved=bool(row.risk_approved), risk_percent=row.risk_percent or 0.0, volume=row.volume or 0.0,
            entry_price=row.entry_price or 0.0, stop_loss=row.stop_loss or 0.0, take_profit=row.take_profit or 0.0,
            take_profit_2=row.take_profit_2 or 0.0, trailing=row.action in ("BUY", "SELL"),
            atr=atr if atr is not None else self.c.last_atr.get(logical, 0.0),
            be_trigger_r=st.be_trigger_r, be_offset_r=st.be_offset_r, trail_start_r=st.trail_start_r,
            trail_atr_multiplier=st.trail_atr_multiplier, tp1_r=st.tp1_r, tp1_close_fraction=st.tp1_close_fraction,
            trading_mode=row.trading_mode or "PAPER", paper=(row.trading_mode or "PAPER") == "PAPER",
            reason=row.reason or "", decision=line,
        )
