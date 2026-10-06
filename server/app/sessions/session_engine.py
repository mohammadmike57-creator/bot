"""Trading-session engine with DST-aware local session windows.

Each session is defined in its *own* local timezone, so daylight-saving shifts
in Sydney, London and New York move the UTC window automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from app.domain import SessionName
from app.sessions.timezone import ensure_utc


class ActivityLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True)
class SessionWindow:
    name: SessionName
    tz: str
    open: time
    close: time

    def bounds_for(self, ts: datetime) -> tuple[datetime, datetime]:
        """Window (UTC) of the session occurrence that contains or precedes ``ts``."""
        zone = ZoneInfo(self.tz)
        local = ensure_utc(ts).astimezone(zone)
        start = datetime.combine(local.date(), self.open, tzinfo=zone)
        if start > local:
            start -= timedelta(days=1)
            start = datetime.combine(start.date(), self.open, tzinfo=zone)
        end_date = start.date() if self.close > self.open else start.date() + timedelta(days=1)
        end = datetime.combine(end_date, self.close, tzinfo=zone)
        return ensure_utc(start), ensure_utc(end)

    def contains(self, ts: datetime) -> bool:
        start, end = self.bounds_for(ts)
        return start <= ensure_utc(ts) < end


DEFAULT_WINDOWS: tuple[SessionWindow, ...] = (
    SessionWindow(SessionName.SYDNEY, "Australia/Sydney", time(7, 0), time(16, 0)),
    SessionWindow(SessionName.TOKYO, "Asia/Tokyo", time(9, 0), time(18, 0)),
    SessionWindow(SessionName.LONDON, "Europe/London", time(8, 0), time(17, 0)),
    SessionWindow(SessionName.NEW_YORK, "America/New_York", time(8, 0), time(17, 0)),
)

_ACTIVITY = {
    SessionName.OVERLAP: ActivityLevel.HIGH,
    SessionName.NEW_YORK: ActivityLevel.NORMAL,
    SessionName.LONDON: ActivityLevel.NORMAL,
    SessionName.TOKYO: ActivityLevel.NORMAL,
    SessionName.SYDNEY: ActivityLevel.LOW,
    SessionName.OFF_HOURS: ActivityLevel.NONE,
}


@dataclass(frozen=True)
class SessionInfo:
    ts: datetime
    primary: SessionName
    active: frozenset[SessionName]
    activity: ActivityLevel
    minutes_into_primary: float
    minutes_to_primary_close: float

    def is_active(self, name: SessionName) -> bool:
        return name in self.active


class SessionEngine:
    def __init__(self, windows: tuple[SessionWindow, ...] = DEFAULT_WINDOWS):
        self.windows = {w.name: w for w in windows}

    def window(self, name: SessionName) -> SessionWindow:
        return self.windows[name]

    def classify(self, ts: datetime) -> SessionInfo:
        ts = ensure_utc(ts)
        active = {name for name, w in self.windows.items() if w.contains(ts)}
        if SessionName.LONDON in active and SessionName.NEW_YORK in active:
            active.add(SessionName.OVERLAP)
        primary = SessionName.OFF_HOURS
        for candidate in (SessionName.OVERLAP, SessionName.NEW_YORK, SessionName.LONDON, SessionName.TOKYO,
                          SessionName.SYDNEY):
            if candidate in active:
                primary = candidate
                break
        into = to_close = 0.0
        if primary is not SessionName.OFF_HOURS:
            if primary is SessionName.OVERLAP:
                start = self.windows[SessionName.NEW_YORK].bounds_for(ts)[0]
                end = self.windows[SessionName.LONDON].bounds_for(ts)[1]
            else:
                start, end = self.windows[primary].bounds_for(ts)
            into = (ts - start).total_seconds() / 60
            to_close = (end - ts).total_seconds() / 60
        return SessionInfo(ts, primary, frozenset(active), _ACTIVITY[primary], into, to_close)

    def asian_range_window(self, ts: datetime) -> tuple[datetime, datetime]:
        """The most recent Tokyo session window (UTC) at or before ``ts``."""
        return self.windows[SessionName.TOKYO].bounds_for(ts)
