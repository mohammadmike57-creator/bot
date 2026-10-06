"""Authentication primitives: HMAC request signing (EA), JWT (dashboard), rate limiting.

EA request signature
--------------------
Headers: ``X-Api-Key-Id``, ``X-Timestamp`` (unix seconds, UTC),
``X-Request-Id`` (unique, 16-64 chars ``[A-Za-z0-9-]``), ``X-Signature``.

    canonical = METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + REQUEST_ID + "\\n" + hex(sha256(BODY))
    signature = hex(hmac_sha256(EA_HMAC_SECRET, canonical))

The timestamp must be within ``SIGNATURE_MAX_SKEW_SECONDS`` and each request
id is accepted exactly once (replay protection, persisted in the database).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")


class AuthError(Exception):
    def __init__(self, code: str, status: int = 401):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str  # "ea" | "admin"
    method: str  # "hmac" | "jwt"


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_string(method: str, path: str, timestamp: str, request_id: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, timestamp, request_id, body_hash(body)])


def sign(secret: str, method: str, path: str, timestamp: str, request_id: str, body: bytes) -> str:
    msg = canonical_string(method, path, timestamp, request_id, body).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secret: str,
    expected_key_id: str,
    key_id: str | None,
    timestamp: str | None,
    request_id: str | None,
    signature: str | None,
    method: str,
    path: str,
    body: bytes,
    max_skew: int,
    now: float | None = None,
) -> str:
    """Validate headers + signature. Returns the request id (caller enforces uniqueness)."""
    if not secret or not expected_key_id:
        raise AuthError("EA_AUTH_NOT_CONFIGURED", 503)
    if not key_id or not timestamp or not request_id or not signature:
        raise AuthError("MISSING_AUTH_HEADERS")
    if not hmac.compare_digest(key_id, expected_key_id):
        raise AuthError("INVALID_API_KEY")
    if not REQUEST_ID_RE.match(request_id):
        raise AuthError("INVALID_REQUEST_ID", 400)
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise AuthError("INVALID_TIMESTAMP", 400) from exc
    current = now if now is not None else time.time()
    if abs(current - ts) > max_skew:
        raise AuthError("TIMESTAMP_OUT_OF_WINDOW")
    expected = sign(secret, method, path, timestamp, request_id, body)
    if not hmac.compare_digest(expected, signature.lower()):
        raise AuthError("INVALID_SIGNATURE")
    return request_id


# ----------------------------------------------------------------------------- JWT


def issue_token(secret: str, subject: str, role: str, ttl_minutes: int) -> tuple[str, datetime]:
    if not secret:
        raise AuthError("JWT_NOT_CONFIGURED", 503)
    exp = datetime.now(UTC) + timedelta(minutes=ttl_minutes)
    token = jwt.encode({"sub": subject, "role": role, "exp": exp, "iat": datetime.now(UTC)}, secret, algorithm="HS256")
    return token, exp


def verify_token(secret: str, token: str) -> Principal:
    if not secret:
        raise AuthError("JWT_NOT_CONFIGURED", 503)
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub", "role"]})
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("TOKEN_EXPIRED") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("INVALID_TOKEN") from exc
    return Principal(subject=str(payload["sub"]), role=str(payload["role"]), method="jwt")


def check_api_key(configured: str, provided: str) -> bool:
    return bool(configured) and hmac.compare_digest(configured.encode(), provided.encode())


# --------------------------------------------------------------------- rate limit


class RateLimiter:
    """Sliding-window limiter (per process). Use Redis for multi-instance deployments."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: float = 60.0, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and t - q[0] >= window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(t)
            return True


class RedisRateLimiter:
    def __init__(self, url: str):
        import redis  # optional dependency

        self._r = redis.Redis.from_url(url, socket_timeout=1)

    def allow(self, key: str, limit: int, window_seconds: float = 60.0, now: float | None = None) -> bool:
        bucket = f"rl:{key}:{int(time.time() // window_seconds)}"
        try:
            count = self._r.incr(bucket)
            if count == 1:
                self._r.expire(bucket, int(window_seconds) + 1)
            return int(count) <= limit  # type: ignore[arg-type]
        except Exception:
            return True  # never lock out the EA because the cache is down; auth still applies
