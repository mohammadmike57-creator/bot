"""Daily loss and drawdown tracking.

State is persisted by the caller (database ``risk_state``); this module is pure
logic so it can be shared by the live service and the backtester.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass
class EquityRiskState:
    trading_day: date | None = None
    day_start_equity: float = 0.0
    initial_equity: float = 0.0
    peak_equity: float = 0.0
    last_equity: float = 0.0
    daily_loss_locked: bool = False
    drawdown_locked: bool = False
    consecutive_losses: int = 0
    cooldown_until: datetime | None = None


@dataclass(frozen=True)
class LossStreakPolicy:
    """How a professional desk reacts to a losing streak: trade smaller, then step away.

    Risk is only ever REDUCED after losses (never increased to "win it back").
    """

    reduce_after: int = 2  # consecutive losing trades before risk is reduced
    reduced_scale: float = 0.5  # risk multiplier while the streak lasts (until the next winner)
    cooldown_after: int = 3  # consecutive losses that pause new trades
    cooldown_minutes: int = 120


def record_trade_result(state: EquityRiskState, profit: float, now: datetime, policy: LossStreakPolicy) -> bool:
    """Update the streak with one closed trade. Returns True when a cooldown starts.

    A winner resets the streak; a scratch (exactly 0) leaves it unchanged.
    """
    if profit > 0:
        state.consecutive_losses = 0
        return False
    if profit == 0:
        return False
    state.consecutive_losses += 1
    n, every = state.consecutive_losses, policy.cooldown_after
    if every > 0 and n >= every and (n - every) % every == 0:
        state.cooldown_until = now + timedelta(minutes=policy.cooldown_minutes)
        return True
    return False


def loss_streak_scale(state: EquityRiskState, policy: LossStreakPolicy) -> float:
    if policy.reduce_after > 0 and state.consecutive_losses >= policy.reduce_after:
        return policy.reduced_scale
    return 1.0


def in_cooldown(state: EquityRiskState, now: datetime | None) -> bool:
    return bool(now is not None and state.cooldown_until is not None and now < state.cooldown_until)


@dataclass(frozen=True)
class EquityCheck:
    daily_loss_fraction: float
    drawdown_fraction: float
    daily_loss_breached: bool
    drawdown_breached: bool
    new_day: bool


def trading_day_for(ts: datetime, tz_name: str = "America/New_York", rollover_hour: int = 17) -> date:
    """Broker-style trading day: rolls over at ``rollover_hour`` in ``tz_name`` (default 17:00 New York)."""
    if ts.tzinfo is None:
        raise ValueError("timezone-aware datetime required")
    local = ts.astimezone(ZoneInfo(tz_name))
    if local.hour >= rollover_hour:
        local = local + timedelta(days=1)
    return local.date()


def update_equity_state(
    state: EquityRiskState,
    equity: float,
    now: datetime,
    *,
    max_daily_loss: float,
    max_drawdown: float,
    tz_name: str = "America/New_York",
    rollover_hour: int = 17,
) -> EquityCheck:
    """Mutates ``state`` with the latest equity and reports breaches.

    - A new trading day resets the daily baseline and the daily lock - this
      happens ONLY on a calendar rollover, never because a process restarted.
    - The drawdown lock is sticky; it is cleared only by an explicit reset.
    """
    day = trading_day_for(now, tz_name, rollover_hour)
    new_day = state.trading_day != day
    if new_day:
        state.trading_day = day
        state.day_start_equity = equity
        state.daily_loss_locked = False
    if state.initial_equity <= 0:
        state.initial_equity = equity
    state.peak_equity = max(state.peak_equity, equity)
    state.last_equity = equity

    daily = 0.0
    if state.day_start_equity > 0:
        daily = max(0.0, (state.day_start_equity - equity) / state.day_start_equity)
    dd = 0.0
    if state.peak_equity > 0:
        dd = max(0.0, (state.peak_equity - equity) / state.peak_equity)

    daily_breach = daily >= max_daily_loss
    dd_breach = dd >= max_drawdown
    if daily_breach:
        state.daily_loss_locked = True
    if dd_breach:
        state.drawdown_locked = True
    return EquityCheck(daily, dd, daily_breach, dd_breach, new_day)


def reset_drawdown(state: EquityRiskState, equity: float) -> None:
    """Manual reset: re-base the peak to current equity and unlock."""
    state.peak_equity = equity
    state.drawdown_locked = False


def reset_daily(state: EquityRiskState, equity: float) -> None:
    state.day_start_equity = equity
    state.daily_loss_locked = False


def reset_loss_streak(state: EquityRiskState) -> None:
    state.consecutive_losses = 0
    state.cooldown_until = None
