from datetime import UTC, datetime

import pytest

from app.domain import SessionName
from app.sessions.market_hours import market_status
from app.sessions.session_engine import ActivityLevel, SessionEngine
from app.sessions.timezone import ensure_utc

E = SessionEngine()


def utc(*a):
    return datetime(*a, tzinfo=UTC)


def test_london_open_moves_with_dst():
    # Summer (BST, UTC+1): London opens 07:00 UTC
    assert E.classify(utc(2026, 7, 7, 7, 0)).is_active(SessionName.LONDON)
    assert not E.classify(utc(2026, 7, 7, 6, 59)).is_active(SessionName.LONDON)
    # Winter (GMT): London opens 08:00 UTC
    assert not E.classify(utc(2026, 1, 13, 7, 30)).is_active(SessionName.LONDON)
    assert E.classify(utc(2026, 1, 13, 8, 0)).is_active(SessionName.LONDON)


def test_new_york_dst_and_us_eu_mismatch_week():
    # 2026-03-10: US already on EDT (since Mar 8), UK still on GMT -> NY opens 12:00 UTC
    assert E.classify(utc(2026, 3, 10, 12, 0)).is_active(SessionName.NEW_YORK)
    assert not E.classify(utc(2026, 3, 10, 11, 59)).is_active(SessionName.NEW_YORK)
    # Winter: NY opens 13:00 UTC
    assert not E.classify(utc(2026, 1, 13, 12, 30)).is_active(SessionName.NEW_YORK)


def test_overlap_is_primary_and_high_activity():
    info = E.classify(utc(2026, 10, 6, 14, 0))
    assert info.primary is SessionName.OVERLAP
    assert info.activity is ActivityLevel.HIGH


def test_tokyo_session_no_dst():
    assert E.classify(utc(2026, 1, 13, 1, 0)).primary is SessionName.TOKYO
    assert E.classify(utc(2026, 7, 7, 1, 0)).primary is SessionName.TOKYO


def test_sydney_low_activity():
    # 22:00 UTC in January = 09:00 AEDT; Tokyo closed -> Sydney is primary
    info = E.classify(utc(2026, 1, 13, 22, 0))
    assert info.primary is SessionName.SYDNEY and info.activity is ActivityLevel.LOW


def test_off_hours():
    # 2026-10-06 21:30 UTC: NY closed (17:30 EDT), Sydney opens 07:00 AEDT = 20:00 UTC -> Sydney
    assert E.classify(utc(2026, 10, 6, 21, 30)).primary is SessionName.SYDNEY


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        E.classify(datetime(2026, 1, 1, 10))


@pytest.mark.parametrize("ts,open_,reason", [
    (utc(2026, 10, 10, 12, 0), False, "WEEKEND"),  # Saturday
    (utc(2026, 10, 11, 21, 0), False, "WEEKEND"),  # Sunday 17:00 NY
    (utc(2026, 10, 11, 22, 30), True, "OPEN"),  # Sunday 18:30 NY
    (utc(2026, 10, 9, 21, 0), False, "WEEKEND"),  # Friday 17:00 NY
    (utc(2026, 10, 6, 21, 30), False, "DAILY_BREAK"),  # Tue 17:30 NY
    (utc(2026, 10, 6, 22, 5), False, "JUST_OPENED"),
    (utc(2026, 10, 6, 20, 50), False, "NEAR_CLOSE"),
    (utc(2026, 10, 6, 14, 0), True, "OPEN"),
])
def test_market_hours(ts, open_, reason):
    st = market_status(ts)
    assert st.open is open_ and st.reason == reason


def test_asian_range_window():
    start, end = E.asian_range_window(utc(2026, 10, 6, 10, 0))
    assert start == utc(2026, 10, 6, 0, 0) and end == utc(2026, 10, 6, 9, 0)
