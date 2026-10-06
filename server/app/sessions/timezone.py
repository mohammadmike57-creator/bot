"""Timezone helpers. UTC internally; local zones only for session rules and display."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(ts: datetime) -> datetime:
    """Reject naive datetimes - comparing naive and aware timestamps is a bug."""
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise ValueError("naive datetime is not allowed; use timezone-aware UTC")
    return ts.astimezone(UTC)


def to_local(ts: datetime, tz_name: str) -> datetime:
    return ensure_utc(ts).astimezone(ZoneInfo(tz_name))


def from_epoch(seconds: float) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)
