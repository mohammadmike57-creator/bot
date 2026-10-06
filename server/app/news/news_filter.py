"""Symbol-aware news blackout.

A USD release is relevant to gold, oil and US30; a crude-inventory release is
relevant to oil only. When the calendar is unavailable the default policy is
SAFE MODE (block everything) - "no data" never means "no news".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config.settings import NewsFailurePolicy
from app.config.symbols import ENERGY_ONLY_KEYWORDS, AssetClass, profile_for
from app.news.economic_calendar import CalendarStatus, EconomicCalendar
from app.news.news_provider import EconomicEvent
from app.sessions.timezone import ensure_utc


@dataclass(frozen=True)
class BlackoutConfig:
    high_before_min: int = 15
    high_after_min: int = 20
    medium_before_min: int = 5
    medium_after_min: int = 5
    block_medium: bool = False
    failure_policy: NewsFailurePolicy = NewsFailurePolicy.SAFE_MODE


@dataclass(frozen=True)
class NewsDecision:
    blocked: bool
    reason: str
    event: EconomicEvent | None = None
    minutes_to_next_relevant: float | None = None


def is_relevant(event: EconomicEvent, logical_symbol: str) -> bool:
    profile = profile_for(logical_symbol)
    name = event.event.lower()
    energy_event = any(k in name for k in ENERGY_ONLY_KEYWORDS)
    if energy_event:
        return profile.asset_class is AssetClass.ENERGY
    if event.currency and event.currency in profile.news_currencies:
        return True
    return any(k in name for k in profile.news_keywords)


class NewsFilter:
    def __init__(self, calendar: EconomicCalendar, config: BlackoutConfig):
        self.calendar = calendar
        self.config = config

    def _window(self, event: EconomicEvent) -> tuple[timedelta, timedelta] | None:
        c = self.config
        if event.importance == "HIGH":
            return timedelta(minutes=c.high_before_min), timedelta(minutes=c.high_after_min)
        if event.importance == "MEDIUM" and c.block_medium:
            return timedelta(minutes=c.medium_before_min), timedelta(minutes=c.medium_after_min)
        return None

    def check(self, logical_symbol: str, now: datetime) -> NewsDecision:
        now = ensure_utc(now)
        status = self.calendar.status(now)
        if status is not CalendarStatus.OK and self.config.failure_policy is NewsFailurePolicy.SAFE_MODE:
            return NewsDecision(True, f"NEWS_SERVICE_{status.value}")
        horizon = max(self.config.high_before_min, self.config.high_after_min, 60)
        events = self.calendar.events_between(now - timedelta(minutes=horizon), now + timedelta(days=1))
        next_minutes: float | None = None
        for ev in events:
            if not is_relevant(ev, logical_symbol):
                continue
            if ev.scheduled_at >= now and ev.importance == "HIGH":
                m = (ev.scheduled_at - now).total_seconds() / 60
                next_minutes = m if next_minutes is None else min(next_minutes, m)
            window = self._window(ev)
            if window and ev.scheduled_at - window[0] <= now <= ev.scheduled_at + window[1]:
                return NewsDecision(True, f"NEWS_BLACKOUT:{ev.importance}:{ev.currency}:{ev.event}", ev, next_minutes)
        return NewsDecision(False, "CLEAR", None, next_minutes)
