"""Event-driven, bar-by-bar multi-symbol backtester.

Uses the SAME components as live trading: features, regime classifier,
session engine, market hours, news filter, strategy router, model
confirmation, risk engine (all portfolio/correlation/drawdown limits),
trade management and the paper-trading simulator.

No look-ahead:
* features are causal (verified by tests);
* a decision is made at the CLOSE of bar t and filled at the OPEN of bar
  t + latency_bars, including spread and slippage;
* exits use each bar's high/low with the conservative stop-first rule;
* stop adjustments made at bar t only apply from bar t+1.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.ai.features import compute_features
from app.ai.labeling import BUY, HOLD, SELL
from app.ai.probability import Probabilities, confirms
from app.ai.regime_model import RegimeConfig, classify_series
from app.backtesting.metrics import TradeResult, compute_metrics
from app.backtesting.transaction_costs import CostAssumptions, cost_model, spread_price
from app.domain import AccountState, Direction, OpenPosition, Regime, SymbolSpec, TradeProposal
from app.execution.simulator import ClosedTrade, SimPosition, TradeSimulator
from app.execution.trade_management import ManagementParams
from app.news.economic_calendar import EconomicCalendar
from app.news.news_filter import BlackoutConfig, NewsFilter
from app.news.news_provider import EconomicEvent
from app.portfolio.correlation import CorrelationMatrix
from app.risk.drawdown import EquityRiskState, record_trade_result, update_equity_state
from app.risk.limits import RiskLimits
from app.risk.risk_engine import RiskEngine
from app.sessions.market_hours import market_status
from app.sessions.session_engine import SessionEngine
from app.strategies.base import StrategyContext, StrategyParams
from app.strategies.router import StrategyRouter


@dataclass
class BacktestConfig:
    initial_equity: float = 100.0
    timeframe_minutes: int = 15
    warmup_bars: int = 220
    limits: RiskLimits = field(default_factory=RiskLimits)
    management: ManagementParams = field(default_factory=ManagementParams)
    costs: CostAssumptions = field(default_factory=CostAssumptions)
    strategy_params: StrategyParams = field(default_factory=StrategyParams)
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    max_spread_atr_ratio: float = 0.15
    news_events: list[EconomicEvent] | None = None  # None = no news filter (state it in reports!)
    blackout: BlackoutConfig = field(default_factory=BlackoutConfig)
    apply_market_hours: bool = True
    confidence_threshold: float = 0.6
    min_probability_edge: float = 0.15
    max_hold_probability: float = 0.5
    scaling_steps: list[tuple[float, float]] = field(default_factory=lambda: [(1.0, 1.0)])
    allow_sydney: bool = False
    correlation_update_bars: int = 16
    trading_day_tz: str = "America/New_York"
    rollover_hour: int = 17


@dataclass
class BacktestResult:
    trades: list[TradeResult]
    equity_times: list[datetime]
    equity_values: list[float]
    metrics: dict[str, Any]
    decisions: Counter[str]
    events: list[ClosedTrade]
    assumptions: dict[str, Any]


@dataclass
class _Pending:
    proposal: TradeProposal
    volume: float
    risk_money: float
    session: str
    regime: str
    confidence: float
    decided_at: datetime


class BacktestEngine:
    def __init__(self, config: BacktestConfig, specs: dict[str, SymbolSpec], data: dict[str, pd.DataFrame],
                 probabilities: dict[str, np.ndarray] | None = None,
                 features: dict[str, pd.DataFrame] | None = None):
        """``data``: logical symbol -> BID bars (time, open, high, low, close[, tick_volume, spread]).

        ``probabilities``: optional symbol -> (n_bars, 3) array of [HOLD, BUY, SELL]
        model probabilities aligned to the bars (produced by a model trained only
        on data before the backtest window).
        """
        self.cfg = config
        self.specs = specs
        self.sessions = SessionEngine()
        self.router = StrategyRouter(allow_sydney=config.allow_sydney)
        self.correlation = CorrelationMatrix()
        self.features: dict[str, pd.DataFrame] = {}
        for sym, df in data.items():
            f = features[sym] if features and sym in features else compute_features(
                df, session_engine=self.sessions)
            f = f.copy()
            f["regime"] = classify_series(f, config.regime)
            self.features[sym] = f.reset_index(drop=True)
        self.probabilities = probabilities or {}
        self.news: NewsFilter | None = None
        if config.news_events is not None:
            cal = EconomicCalendar(provider=None, historical=True)
            cal.load_events(config.news_events, datetime(1970, 1, 1, tzinfo=UTC))
            self.news = NewsFilter(cal, config.blackout)

    # ---------------------------------------------------------------------------------
    def run(self, start: datetime | None = None, end: datetime | None = None) -> BacktestResult:
        cfg = self.cfg
        tf = timedelta(minutes=cfg.timeframe_minutes)
        sims: TradeSimulator = TradeSimulator(cost_model(cfg.costs, next(iter(self.specs.values()))), cfg.management)
        risk_engine = RiskEngine(cfg.limits, self.correlation.get, cfg.scaling_steps)
        balance = cfg.initial_equity
        eq_state = EquityRiskState()
        decisions: Counter[str] = Counter()
        closed_events: list[ClosedTrade] = []
        pending: dict[str, _Pending] = {}
        last_close: dict[str, tuple[float, float]] = {}
        eq_t: list[datetime] = []
        eq_v: list[float] = []

        index: dict[str, dict[pd.Timestamp, int]] = {
            s: {t: i for i, t in enumerate(f["time"])} for s, f in self.features.items()
        }
        timeline = sorted(set().union(*[set(f["time"]) for f in self.features.values()]))
        if start is not None:
            timeline = [t for t in timeline if t >= pd.Timestamp(start)]
        if end is not None:
            timeline = [t for t in timeline if t < pd.Timestamp(end)]
        sym_costs = {s: cost_model(cfg.costs, spec) for s, spec in self.specs.items()}

        for ts in timeline:
            now_open = ts.to_pydatetime()
            close_time = now_open + tf
            for sym, f in self.features.items():
                i = index[sym].get(ts)
                if i is None:
                    continue
                row = f.iloc[i]
                spec = self.specs[sym]
                spread = spread_price(float(row.get("spread", 0) or 0), spec, cfg.costs)
                # 1. fill pending order at this bar's open
                p = pending.pop(sym, None)
                if p is not None:
                    self._fill(sims, sym_costs[sym], p, row, spread, spec, now_open, decisions)
                # 2. exits / management through this bar
                sims.costs = sym_costs[sym]
                atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
                for ev in sims.on_bar(sym, close_time, float(row["open"]), float(row["high"]), float(row["low"]),
                                      float(row["close"]), spread, atr):
                    if ev.closed is not None:
                        balance += ev.closed.profit
                        closed_events.append(ev.closed)
                        if ev.closed.final:
                            total = sum(c.profit for c in closed_events if c.ticket == ev.closed.ticket)
                            record_trade_result(eq_state, total, close_time, cfg.limits.loss_streak)
                last_close[sym] = (float(row["close"]), float(row["close"]) + spread)
                if i % cfg.correlation_update_bars == 0:  # refresh the rolling matrix periodically (causal)
                    lo = max(0, i - 250)
                    self.correlation.update(sym, pd.Series(f["close"].to_numpy()[lo: i + 1],
                                                           index=pd.DatetimeIndex(f["time"].iloc[lo: i + 1])))

            day = close_time.date()
            sims.apply_swap(close_time, day)
            equity = balance + sims.floating(last_close)
            update_equity_state(eq_state, equity, close_time, max_daily_loss=cfg.limits.max_daily_loss,
                                max_drawdown=cfg.limits.max_drawdown, tz_name=cfg.trading_day_tz,
                                rollover_hour=cfg.rollover_hour)
            eq_t.append(close_time)
            eq_v.append(equity)

            # 3. decisions at bar close
            for sym, f in self.features.items():
                i = index[sym].get(ts)
                if i is None or i < cfg.warmup_bars or i >= len(f) - 1:
                    continue
                reason = self._decide(sym, f, i, close_time, sims, risk_engine, balance, equity, eq_state, pending,
                                      last_close)
                decisions[reason] += 1

        # Close anything left at the final prices (marked as END_OF_TEST).
        if timeline:
            final_time = timeline[-1].to_pydatetime() + tf
            for ev in sims.close_all(last_close, final_time, "END_OF_TEST"):
                if ev.closed is not None:
                    balance += ev.closed.profit
                    closed_events.append(ev.closed)
            if eq_v:
                eq_v[-1] = balance
        trades = _aggregate(closed_events)
        metrics = compute_metrics(trades, eq_t, eq_v, cfg.initial_equity)
        metrics["decisions"] = dict(decisions)
        return BacktestResult(trades, eq_t, eq_v, metrics, decisions, closed_events, self.assumptions())

    def assumptions(self) -> dict[str, Any]:
        c = self.cfg
        return {
            "fill": f"next bar open after {c.costs.latency_bars} bar latency, spread + {c.costs.slippage_points} pts slippage",
            "intrabar": "stop assumed hit before target when both touched",
            "commission_per_lot": c.costs.commission_per_lot,
            "spread": "per-bar spread from data, else fallback",
            "spread_multiplier": c.costs.spread_multiplier,
            "news_filter": "historical calendar" if c.news_events is not None else "NOT APPLIED (no calendar supplied)",
            "market_hours": c.apply_market_hours,
        }

    # ---------------------------------------------------------------------------------
    def _fill(self, sim: TradeSimulator, costs: Any, p: _Pending, row: pd.Series, spread: float, spec: SymbolSpec,
              ts: datetime, decisions: Counter[str]) -> None:
        d = p.proposal.direction
        bid = float(row["open"])
        ask = bid + spread
        sim.costs = costs
        fill = sim.fill_price(d, bid, ask, spec)
        if (fill - p.proposal.stop_loss) * d.sign <= 0 or (p.proposal.take_profit - fill) * d.sign <= 0:
            decisions["FILL_CANCELLED_GAP"] += 1
            return
        risk = abs(p.proposal.entry_price - p.proposal.stop_loss)
        tp1 = fill + d.sign * risk * sim.management.tp1_r
        sim.open(SimPosition(
            ticket=sim.next_ticket("BT"), symbol=p.proposal.symbol, direction=d, volume=p.volume, entry=fill,
            sl=p.proposal.stop_loss, tp=p.proposal.take_profit, tp1=tp1, strategy=p.proposal.strategy, opened_at=ts,
            spec=spec, risk_money=p.risk_money, session=p.session, regime=p.regime, confidence=p.confidence,
            requested_price=ask if d is Direction.BUY else bid,
        ))
        decisions["FILLED"] += 1

    def _decide(self, sym: str, f: pd.DataFrame, i: int, now: datetime, sim: TradeSimulator, engine: RiskEngine,
                balance: float, equity: float, eq_state: EquityRiskState, pending: dict[str, _Pending],
                last_close: dict[str, tuple[float, float]]) -> str:
        cfg = self.cfg
        row = f.iloc[i]
        if sym in pending:
            return "PENDING_EXISTS"
        if cfg.apply_market_hours and not market_status(now).open:
            return "MARKET_CLOSED"
        session = self.sessions.classify(now)
        regime = Regime(row["regime"])
        atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
        if atr <= 0:
            return "ATR_UNAVAILABLE"
        spec = self.specs[sym]
        spread = spread_price(float(row.get("spread", 0) or 0), spec, cfg.costs)
        if spread / atr > cfg.max_spread_atr_ratio:
            return "SPREAD_TOO_HIGH"
        if self.news is not None:
            nd = self.news.check(sym, now)
            if nd.blocked:
                return "NEWS_BLACKOUT"
        if eq_state.drawdown_locked:
            return "MAX_DRAWDOWN_REACHED"
        if eq_state.daily_loss_locked:
            return "MAX_DAILY_LOSS_REACHED"
        window = f.iloc[max(0, i - 300): i + 1]
        bid = float(row["close"])
        ctx = StrategyContext(symbol=sym, timeframe=f"M{cfg.timeframe_minutes}", features=window, regime=regime,
                              session=session, bid=bid, ask=bid + spread, spec=spec, params=cfg.strategy_params,
                              session_engine=self.sessions)
        sig, _ = self.router.evaluate(ctx)
        if sig is None:
            return "NO_SETUP"
        confidence = 0.0
        probs_arr = self.probabilities.get(sym)
        if probs_arr is not None:
            p = probs_arr[i]
            if not np.all(np.isfinite(p)):
                return "MODEL_PREDICTION_UNAVAILABLE"
            total = float(p.sum()) or 1.0
            probs = Probabilities(p_buy=float(p[BUY] / total), p_sell=float(p[SELL] / total),
                                  p_hold=float(p[HOLD] / total))
            dec = confirms(probs, sig.direction, cfg.confidence_threshold, cfg.min_probability_edge,
                           cfg.max_hold_probability)
            if dec.direction is None:
                return dec.reason
            confidence = dec.confidence
        margin = sum(pos.spec.margin_per_lot * pos.volume for pos in sim.positions.values())
        account = AccountState(login="BT", balance=balance, equity=equity, margin=margin,
                               free_margin=equity - margin)
        positions = [
            OpenPosition(ticket=pos.ticket, symbol=pos.symbol, direction=pos.direction, volume=pos.volume,
                         entry_price=pos.entry, sl=pos.sl, tp=pos.tp,
                         current_price=last_close.get(pos.symbol, (pos.entry, pos.entry))[0],
                         strategy=pos.strategy)
            for pos in sim.positions.values()
        ]
        proposal = TradeProposal(symbol=sym, direction=sig.direction, entry_price=sig.entry, stop_loss=sig.stop_loss,
                                 take_profit=sig.take_profit_2 or sig.take_profit, take_profit_2=sig.take_profit_2,
                                 strategy=sig.strategy, confidence=confidence, risk_scale=sig.risk_scale)
        decision = engine.evaluate(proposal, account, positions, self.specs, eq_state, now=now)
        if not decision.approved:
            return decision.reason.split(":")[0].split(" until")[0]
        pending[sym] = _Pending(proposal, decision.volume, decision.risk_money, session.primary.value, regime.value,
                                confidence, now)
        return "APPROVED"


def _aggregate(events: list[ClosedTrade]) -> list[TradeResult]:
    """Combine partial closes into one trade result per ticket."""
    by_ticket: dict[str, list[ClosedTrade]] = {}
    for e in events:
        by_ticket.setdefault(e.ticket, []).append(e)
    out = []
    for ticket, parts in by_ticket.items():
        first, last = parts[0], parts[-1]
        out.append(TradeResult(
            ticket=ticket, symbol=first.symbol, strategy=first.strategy, direction=first.direction.value,
            session=first.session, regime=first.regime, opened_at=first.opened_at, closed_at=last.closed_at,
            profit=sum(p.profit for p in parts), risk_money=sum(p.risk_money for p in parts),
        ))
    out.sort(key=lambda t: t.closed_at)
    return out
