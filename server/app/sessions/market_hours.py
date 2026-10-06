"""CFD market-hours rules (configurable, DST-aware via America/New_York).

These are *generic* defaults for metals, energy and US index CFDs (weekly open
Sunday 18:00 New York, weekly close Friday 17:00 New York, daily maintenance
break 17:00-18:00 New York). Broker schedules differ, so the EA additionally
checks the broker's real trading sessions (``SymbolInfoSessionTrade``) and the
backend requires BOTH to agree before a trade is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.sessions.timezone import ensure_utc


@dataclass(frozen=True)
class MarketHoursRule:
    tz: str = "America/New_York"
    weekly_open_weekday: int = 6  # Sunday
    weekly_open: time = time(18, 0)
    weekly_close_weekday: int = 4  # Friday
    weekly_close: time = time(17, 0)
    daily_break_start: time = time(17, 0)
    daily_break_end: time = time(18, 0)
    avoid_after_open_minutes: int = 15
    avoid_before_close_minutes: int = 15


@dataclass(frozen=True)
class MarketStatus:
    open: bool
    reason: str


def market_status(ts: datetime, rule: MarketHoursRule = MarketHoursRule()) -> MarketStatus:
    local = ensure_utc(ts).astimezone(ZoneInfo(rule.tz))
    wd = local.weekday()
    t = local.time()
    # Weekend closure.
    if wd == 5:
        return MarketStatus(False, "WEEKEND")
    if wd == rule.weekly_open_weekday and t < rule.weekly_open:
        return MarketStatus(False, "WEEKEND")
    if wd == rule.weekly_close_weekday and t >= rule.weekly_close:
        return MarketStatus(False, "WEEKEND")
    # Daily maintenance break (Mon-Thu).
    if rule.daily_break_start <= t < rule.daily_break_end:
        return MarketStatus(False, "DAILY_BREAK")
    # Thin liquidity right after the open / before the daily close.
    open_dt = datetime.combine(local.date(), rule.daily_break_end, tzinfo=local.tzinfo)
    if local < open_dt:
        open_dt -= timedelta(days=1)
    if (local - open_dt) < timedelta(minutes=rule.avoid_after_open_minutes):
        return MarketStatus(False, "JUST_OPENED")
    close_dt = datetime.combine(local.date(), rule.daily_break_start, tzinfo=local.tzinfo)
    if local >= close_dt:
        close_dt += timedelta(days=1)
    if (close_dt - local) < timedelta(minutes=rule.avoid_before_close_minutes):
        return MarketStatus(False, "NEAR_CLOSE")
    return MarketStatus(True, "OPEN")
