"""Cached economic calendar with explicit health status."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from app.monitoring.logger import get_logger
from app.news.news_provider import EconomicEvent, NewsProvider, NewsProviderError, default_window
from app.sessions.timezone import ensure_utc, utcnow

log = get_logger(__name__)


class CalendarStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass
class CalendarSnapshot:
    status: CalendarStatus
    events: list[EconomicEvent]
    last_success: datetime | None
    last_error: str


class EconomicCalendar:
    def __init__(self, provider: NewsProvider | None, refresh_seconds: int = 900, stale_after_seconds: int = 3600,
                 historical: bool = False):
        """``historical=True``: the loaded events are a complete historical record (backtests),
        so the calendar never goes stale."""
        self.provider = provider
        self.historical = historical
        self.refresh = timedelta(seconds=refresh_seconds)
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self._events: list[EconomicEvent] = []
        self._last_success: datetime | None = None
        self._last_attempt: datetime | None = None
        self._last_error = ""
        self._lock = threading.Lock()

    def load_events(self, events: list[EconomicEvent], now: datetime | None = None) -> None:
        """Inject events directly (backtests / tests)."""
        with self._lock:
            self._events = sorted(events, key=lambda e: e.scheduled_at)
            self._last_success = ensure_utc(now) if now else utcnow()
            self._last_error = ""

    def refresh_if_due(self, now: datetime | None = None, force: bool = False) -> None:
        now = ensure_utc(now) if now else utcnow()
        if self.provider is None:
            return
        if not force and self._last_attempt and now - self._last_attempt < self.refresh:
            return
        self._last_attempt = now
        start, end = default_window(now)
        try:
            events = self.provider.fetch(start, end)
            self.load_events(events, now)
            log.info("news calendar refreshed", extra={"events": len(events), "provider": self.provider.name})
        except NewsProviderError as exc:
            with self._lock:
                self._last_error = str(exc)
            log.warning("news calendar refresh failed", extra={"error": str(exc)})

    def status(self, now: datetime | None = None) -> CalendarStatus:
        now = ensure_utc(now) if now else utcnow()
        if self.provider is None and self._last_success is None:
            return CalendarStatus.NOT_CONFIGURED
        if self._last_success is None:
            return CalendarStatus.FAILED
        if self.historical:
            return CalendarStatus.OK
        if now - self._last_success > self.stale_after:
            return CalendarStatus.STALE
        return CalendarStatus.OK

    def snapshot(self, now: datetime | None = None) -> CalendarSnapshot:
        with self._lock:
            return CalendarSnapshot(self.status(now), list(self._events), self._last_success, self._last_error)

    def events_between(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        with self._lock:
            return [e for e in self._events if start <= e.scheduled_at <= end]
