"""The risk engine: final authority over every new trade.

It cannot be bypassed by strategies, the ML model or any RL research agent.
Each check appends a ``CheckResult`` so every decision (approve or reject) is
explainable. The first failing hard check rejects the trade.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain import AccountState, HedgePurpose, OpenPosition, SymbolSpec, TradeProposal
from app.risk.correlation import correlated_risk
from app.risk.drawdown import EquityRiskState
from app.risk.exposure import position_risk_money
from app.risk.limits import RiskLimits
from app.risk.margin import max_volume_for_margin, projected_margin_usage
from app.risk.position_sizing import calculate_position_size


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    volume: float = 0.0
    risk_money: float = 0.0
    risk_fraction: float = 0.0
    risk_multiplier: float = 1.0
    checks: list[CheckResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    def log_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {f"chk_{c.name}": "ok" if c.passed else f"FAIL({c.detail})" for c in self.checks}
        fields.update({k: round(v, 5) for k, v in self.metrics.items()})
        return fields


@dataclass(frozen=True)
class ControlFlags:
    kill_switch: bool = False
    paused: bool = False
    safe_mode: bool = False
    safe_mode_reason: str = ""


def risk_multiplier(equity: float, initial_equity: float, steps: list[tuple[float, float]], limits: RiskLimits,
                    drawdown_fraction: float = 0.0) -> float:
    """Equity-growth scaling with hard bounds.

    ``steps`` maps growth (equity / initial equity) to a multiplier, e.g.
    1.25 -> 1.05. Scaling is driven by growth only; it never increases risk
    to recover losses: below the starting capital the multiplier is at most
    1.0, and in a drawdown deeper than half the limit it is forced to the
    lower bound.
    """
    growth = equity / initial_equity if initial_equity > 0 else 1.0
    multiplier = 1.0
    for threshold, mult in sorted(steps):
        if growth >= threshold:
            multiplier = mult
    if growth < 1.0:
        multiplier = min(multiplier, 1.0)
    if drawdown_fraction >= limits.max_drawdown / 2:
        multiplier = limits.risk_multiplier_min
    return max(limits.risk_multiplier_min, min(limits.risk_multiplier_max, multiplier))


class RiskEngine:
    def __init__(
        self,
        limits: RiskLimits,
        correlation: Callable[[str, str], float],
        scaling_steps: list[tuple[float, float]] | None = None,
    ):
        self.limits = limits
        self.correlation = correlation
        self.scaling_steps = scaling_steps or [(1.0, 1.0)]

    # ------------------------------------------------------------------ helpers
    def open_risk(self, positions: list[OpenPosition], specs: dict[str, SymbolSpec]) -> list[tuple[OpenPosition, float]]:
        out = []
        for p in positions:
            spec = specs.get(p.symbol)
            risk = position_risk_money(p, spec) if spec else math.inf
            out.append((p, risk))
        return out

    # --------------------------------------------------------------- evaluation
    def evaluate(
        self,
        proposal: TradeProposal,
        account: AccountState,
        positions: list[OpenPosition],
        specs: dict[str, SymbolSpec],
        equity_state: EquityRiskState,
        flags: ControlFlags = ControlFlags(),
    ) -> RiskDecision:
        L = self.limits
        checks: list[CheckResult] = []
        metrics: dict[str, float] = {}

        def reject(name: str, detail: str) -> RiskDecision:
            checks.append(CheckResult(name, False, detail))
            return RiskDecision(False, detail, checks=checks, metrics=metrics)

        def ok(name: str, detail: str = "") -> None:
            checks.append(CheckResult(name, True, detail))

        # 1. global controls -------------------------------------------------
        if flags.kill_switch:
            return reject("kill_switch", "KILL_SWITCH_ACTIVE")
        if flags.paused:
            return reject("paused", "NEW_TRADES_PAUSED")
        if flags.safe_mode:
            return reject("safe_mode", f"SAFE_MODE: {flags.safe_mode_reason}")
        ok("controls")

        # 2. prohibited position-management patterns ------------------------
        if proposal.is_martingale or proposal.volume_multiplier_after_loss > 1.0:
            return reject("no_martingale", "MARTINGALE_PROHIBITED")
        if proposal.is_grid:
            return reject("no_grid", "GRID_PROHIBITED")
        if proposal.averaging_down or self._is_averaging_down(proposal, positions):
            return reject("no_averaging_down", "AVERAGING_DOWN_PROHIBITED")
        if proposal.risk_scale > 1.0 or proposal.risk_scale <= 0:
            return reject("risk_scale", "INVALID_RISK_SCALE")
        ok("prohibited_patterns")

        # 3. account state ----------------------------------------------------
        if not account.trade_allowed:
            return reject("account", "ACCOUNT_TRADING_NOT_ALLOWED")
        if account.equity <= 0:
            return reject("account", "NON_POSITIVE_EQUITY")
        spec = specs.get(proposal.symbol)
        if spec is None:
            return reject("symbol_spec", "SYMBOL_SPEC_UNKNOWN")
        if not spec.trade_allowed:
            return reject("symbol_spec", "SYMBOL_NOT_TRADABLE")
        ok("account")

        # 4. drawdown / daily loss (sticky locks) ----------------------------
        dd = 0.0
        if equity_state.peak_equity > 0:
            dd = max(0.0, (equity_state.peak_equity - account.equity) / equity_state.peak_equity)
        daily = 0.0
        if equity_state.day_start_equity > 0:
            daily = max(0.0, (equity_state.day_start_equity - account.equity) / equity_state.day_start_equity)
        metrics.update(drawdown=dd, daily_loss=daily)
        if equity_state.drawdown_locked or dd >= L.max_drawdown:
            return reject("drawdown", "MAX_DRAWDOWN_REACHED")
        if equity_state.daily_loss_locked or daily >= L.max_daily_loss:
            return reject("daily_loss", "MAX_DAILY_LOSS_REACHED")
        ok("drawdown_daily")

        # 5. stop loss / take profit validity --------------------------------
        sl_problem = self._validate_stops(proposal, spec)
        if sl_problem:
            return reject("stops", sl_problem)
        ok("stops", f"rr={proposal.reward_risk:.2f}")

        # 6. position counts (a secondary limit, not the primary one) --------
        own = [p for p in positions if p.own]
        if len(own) >= L.max_positions:
            return reject("max_positions", "MAX_POSITIONS_REACHED")
        same_symbol = [p for p in own if p.symbol == proposal.symbol]
        if len(same_symbol) >= L.max_positions_per_symbol:
            return reject("max_positions_symbol", "MAX_SYMBOL_POSITIONS_REACHED")
        ok("position_count", f"{len(own)}/{L.max_positions}")

        # 7. hedging must have a purpose -------------------------------------
        opposite = [p for p in same_symbol if p.direction != proposal.direction]
        if opposite:
            if proposal.hedge_purpose == HedgePurpose.NONE:
                return reject("hedging", "HEDGE_WITHOUT_PURPOSE")
            ok("hedging", proposal.hedge_purpose.value)

        # 8. risk budget (equity scaling, bounded) ---------------------------
        mult = risk_multiplier(account.equity, equity_state.initial_equity, self.scaling_steps, L, dd)
        risk_fraction = L.risk_per_trade * mult * proposal.risk_scale
        risk_fraction = min(risk_fraction, L.max_symbol_risk)
        metrics.update(risk_multiplier=mult, risk_fraction=risk_fraction)

        risks = self.open_risk(own, specs)
        if any(math.isinf(r) for _, r in risks):
            return reject("open_risk", "OPEN_POSITION_WITHOUT_STOP_OR_SPEC")
        equity = account.equity
        portfolio_risk = sum(r for _, r in risks)
        symbol_risk = sum(r for p, r in risks if p.symbol == proposal.symbol)
        strategy_risk = sum(r for p, r in risks if p.strategy == proposal.strategy)
        metrics.update(
            portfolio_risk=portfolio_risk / equity,
            symbol_risk=symbol_risk / equity,
            strategy_risk=strategy_risk / equity,
        )

        day_base = equity_state.day_start_equity if equity_state.day_start_equity > 0 else equity
        # Remaining budgets bound the new trade's risk money.
        budgets = {
            "trade": risk_fraction * equity,
            "symbol": L.max_symbol_risk * equity - symbol_risk,
            "strategy": L.max_strategy_risk * equity - strategy_risk,
            "portfolio": L.max_portfolio_risk * equity - portfolio_risk,
            # Never put on more open risk than the daily loss allowance left.
            "daily": L.max_daily_loss * day_base - max(0.0, day_base - equity) - portfolio_risk,
        }
        corr_risk = correlated_risk(
            proposal.symbol,
            proposal.direction,
            [(p.symbol, p.direction, r) for p, r in risks],
            self.correlation,
            L.correlation_threshold,
        )
        if corr_risk > 0:
            budgets["correlation"] = L.max_correlated_exposure * equity - corr_risk
        metrics["correlated_risk"] = corr_risk / equity
        binding = min(budgets, key=lambda k: budgets[k])
        allowed_money = budgets[binding]
        if allowed_money <= 0:
            return reject(f"{binding}_risk", f"MAX_{binding.upper()}_RISK_REACHED")
        ok("risk_budget", f"binding={binding} allowed={allowed_money:.2f}")

        # 9. position size from the binding budget ---------------------------
        max_vol_margin = max_volume_for_margin(account.margin, equity, spec, L.max_margin_usage)
        sizing = calculate_position_size(
            equity=equity,
            risk_fraction=min(allowed_money / equity, 0.999),
            entry=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            spec=spec,
            commission_per_lot=L.commission_per_lot,
            max_volume=max_vol_margin,
        )
        if not sizing.ok:
            if max_vol_margin < spec.volume_min:
                return reject("margin", "INSUFFICIENT_MARGIN")
            return reject("position_size", sizing.reason)
        ok("position_size", f"vol={sizing.volume}")

        # 10. margin -----------------------------------------------------------
        usage = projected_margin_usage(account.margin, equity, spec, sizing.volume)
        metrics["margin_usage"] = usage
        if spec.margin_per_lot > 0 and usage > L.max_margin_usage:
            return reject("margin", "MAX_MARGIN_USAGE")
        if spec.margin_per_lot > 0 and account.free_margin and spec.margin_per_lot * sizing.volume > account.free_margin:
            return reject("margin", "INSUFFICIENT_FREE_MARGIN")
        ok("margin", f"usage={usage:.3f}")

        metrics["trade_risk"] = sizing.risk_money / equity
        return RiskDecision(
            True,
            "APPROVED",
            volume=sizing.volume,
            risk_money=sizing.risk_money,
            risk_fraction=sizing.risk_money / equity,
            risk_multiplier=mult,
            checks=checks,
            metrics=metrics,
        )

    # ------------------------------------------------------------------ checks
    @staticmethod
    def _is_averaging_down(proposal: TradeProposal, positions: list[OpenPosition]) -> bool:
        """Adding to a losing position at a worse price is averaging down."""
        for p in positions:
            if not p.own or p.symbol != proposal.symbol or p.direction != proposal.direction:
                continue
            losing = (p.current_price - p.entry_price) * p.direction.sign < 0
            worse_entry = (proposal.entry_price - p.entry_price) * p.direction.sign < 0
            if losing and worse_entry:
                return True
        return False

    def _validate_stops(self, proposal: TradeProposal, spec: SymbolSpec) -> str:
        d = proposal.direction
        if proposal.stop_loss <= 0:
            return "STOP_LOSS_REQUIRED"
        if (proposal.entry_price - proposal.stop_loss) * d.sign <= 0:
            return "STOP_LOSS_WRONG_SIDE"
        if proposal.take_profit <= 0:
            return "TAKE_PROFIT_REQUIRED"
        if (proposal.take_profit - proposal.entry_price) * d.sign <= 0:
            return "TAKE_PROFIT_WRONG_SIDE"
        min_distance = max(spec.stops_level, 0) * spec.point
        if proposal.stop_distance < max(min_distance, spec.tick_size):
            return "STOP_LOSS_TOO_CLOSE"
        if abs(proposal.take_profit - proposal.entry_price) < max(min_distance, spec.tick_size):
            return "TAKE_PROFIT_TOO_CLOSE"
        if proposal.reward_risk < self.limits.min_reward_risk:
            return "REWARD_RISK_TOO_LOW"
        if proposal.stop_distance / proposal.entry_price > 0.2:
            return "STOP_LOSS_TOO_WIDE"
        return ""
