"""Shared fixtures.

NOTE: ``make_bars`` generates SYNTHETIC random-walk prices. It exists only to
exercise code paths in tests. Results on synthetic data say nothing about
real-market performance.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.database.database import Database
from app.domain import SymbolSpec
from app.main import create_app
from app.monitoring.metrics import metrics
from app.services.container import Container
from app.services.security import sign

EA_KEY_ID = "ea-test-key-0001"
EA_SECRET = "test-hmac-secret-0123456789abcdef"
ADMIN_KEY = "admin-test-key-0123456789abcdef"
JWT_SECRET = "jwt-test-secret-0123456789abcdef"

# A fixed Tuesday 14:30 UTC in October (London/NY overlap, markets open).
FIXED_NOW = datetime(2026, 10, 6, 14, 30, tzinfo=UTC)


def make_settings(tmp_path: Any, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        environment="test", database_url="sqlite://", ea_api_key_id=EA_KEY_ID, ea_hmac_secret=EA_SECRET,
        api_key=ADMIN_KEY, jwt_secret=JWT_SECRET, xauusd_symbol="XAUUSD.t", oil_symbol="USOIL.t",
        us30_symbol="US30.t", news_provider="none", models_dir=str(tmp_path / "models"),
        rate_limit_per_minute=10000, rate_limit_auth_per_minute=1000,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def gold_spec(**kw: Any) -> SymbolSpec:
    base = dict(symbol="XAUUSD", digits=2, point=0.01, tick_size=0.01, tick_value=1.0, contract_size=100.0,
                volume_min=0.01, volume_max=50.0, volume_step=0.01, stops_level=0, margin_per_lot=400.0)
    base.update(kw)
    return SymbolSpec(**base)  # type: ignore[arg-type]


def make_bars(n: int = 3000, start: str = "2026-01-05 00:00", freq: str = "15min", price: float = 2000.0,
              vol: float = 0.0015, seed: int = 7, spread_points: float = 20.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.normal(0, 0.0004, size=n // 200 + 1), 200)[:n]  # regime-like drift segments
    rets = drift + rng.normal(0, vol, size=n)
    close = price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[price], close[:-1]])
    wick = np.abs(rng.normal(0, vol * price * 0.6, size=n))
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * price * 0.6, size=n))
    times = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    df = pd.DataFrame({"time": times, "open": open_, "high": high, "low": low, "close": close,
                       "tick_volume": rng.integers(50, 500, size=n), "spread": spread_points})
    weekend = df["time"].dt.dayofweek >= 5
    return df[~weekend].reset_index(drop=True)


@pytest.fixture
def settings(tmp_path: Any) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def container(settings: Settings) -> Container:
    db = Database("sqlite://")
    db.create_all()
    return Container(settings, db)


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    metrics.reset()
    app = create_app(container.settings, container)
    with TestClient(app) as c:
        yield c


class Signer:
    def __init__(self, client: TestClient):
        self.client = client

    def headers(self, method: str, path: str, body: bytes, *, ts: int | None = None, request_id: str | None = None,
                key_id: str = EA_KEY_ID, secret: str = EA_SECRET) -> dict[str, str]:
        timestamp = str(ts if ts is not None else int(time.time()))
        rid = request_id or uuid.uuid4().hex
        return {
            "Content-Type": "application/json", "X-Api-Key-Id": key_id, "X-Timestamp": timestamp,
            "X-Request-Id": rid, "X-Signature": sign(secret, method, path, timestamp, rid, body),
        }

    def post(self, path: str, payload: Any, **kw: Any) -> Any:
        body = json.dumps(payload).encode()
        return self.client.post(path, content=body, headers=self.headers("POST", path, body, **kw))

    def get(self, path: str, **kw: Any) -> Any:
        return self.client.get(path, headers=self.headers("GET", path, b"", **kw))


@pytest.fixture
def signer(client: TestClient) -> Signer:
    return Signer(client)


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    r = client.post("/v1/auth", json={"api_key": ADMIN_KEY})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
