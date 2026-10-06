"""Signal / order lifecycle and idempotency rules.

A signal moves ISSUED -> ACKED -> EXECUTED | REJECTED_BY_EA | EXPIRED. Any
transition not listed is refused, which makes duplicate EA reports (network
retries, restarts) harmless.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from app.sessions.timezone import ensure_utc


class SignalStatus(str, Enum):
    ISSUED = "ISSUED"
    ACKED = "ACKED"
    EXECUTED = "EXECUTED"
    REJECTED_BY_EA = "REJECTED_BY_EA"
    EXPIRED = "EXPIRED"
    PAPER_FILLED = "PAPER_FILLED"
    NO_TRADE = "NO_TRADE"


ALLOWED_TRANSITIONS: dict[SignalStatus, set[SignalStatus]] = {
    SignalStatus.ISSUED: {SignalStatus.ACKED, SignalStatus.EXECUTED, SignalStatus.REJECTED_BY_EA,
                          SignalStatus.EXPIRED, SignalStatus.PAPER_FILLED},
    SignalStatus.ACKED: {SignalStatus.EXECUTED, SignalStatus.REJECTED_BY_EA, SignalStatus.EXPIRED},
    SignalStatus.EXECUTED: set(),
    SignalStatus.REJECTED_BY_EA: set(),
    SignalStatus.EXPIRED: set(),
    SignalStatus.PAPER_FILLED: set(),
    SignalStatus.NO_TRADE: set(),
}


def can_transition(current: str, new: str) -> bool:
    try:
        return SignalStatus(new) in ALLOWED_TRANSITIONS[SignalStatus(current)]
    except (ValueError, KeyError):
        return False


def is_expired(expires_at: datetime, now: datetime) -> bool:
    return ensure_utc(now) >= ensure_utc(expires_at)
