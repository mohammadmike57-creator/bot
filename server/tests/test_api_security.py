import json
import time

import pytest
from fastapi.testclient import TestClient

from app.database.database import Database
from app.domain import Direction
from app.execution.order_validation import validate_order
from app.main import create_app
from app.services.container import Container
from tests.conftest import EA_SECRET, Signer, gold_spec, make_settings

HB = {"account_login": "1001", "ea_version": "1.0.0", "ea_mode": "PAPER", "terminal_connected": True,
      "trade_allowed": True, "open_positions": 0, "quotes": []}


def test_health_is_public_and_reveals_no_secrets(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["trading_mode"] == "PAPER"
    assert "secret" not in r.text.lower()


def test_missing_auth_headers(client):
    r = client.post("/v1/heartbeat", json=HB)
    assert r.status_code == 401 and r.json()["detail"] == "MISSING_AUTH_HEADERS"


def test_invalid_api_key(signer):
    r = signer.post("/v1/heartbeat", HB, key_id="wrong-key-id-000")
    assert r.status_code == 401 and r.json()["detail"] == "INVALID_API_KEY"


def test_invalid_signature(signer):
    r = signer.post("/v1/heartbeat", HB, secret="not-the-secret-0123456789")
    assert r.status_code == 401 and r.json()["detail"] == "INVALID_SIGNATURE"


def test_tampered_body_rejected(client, signer):
    body = json.dumps(HB).encode()
    headers = signer.headers("POST", "/v1/heartbeat", body)
    tampered = json.dumps({**HB, "open_positions": 5}).encode()
    r = client.post("/v1/heartbeat", content=tampered, headers=headers)
    assert r.status_code == 401 and r.json()["detail"] == "INVALID_SIGNATURE"


def test_expired_timestamp(signer):
    r = signer.post("/v1/heartbeat", HB, ts=int(time.time()) - 120)
    assert r.status_code == 401 and r.json()["detail"] == "TIMESTAMP_OUT_OF_WINDOW"


def test_replayed_request_id_rejected(signer):
    rid = "replay-test-0000000001"
    assert signer.post("/v1/heartbeat", HB, request_id=rid).status_code == 200
    r = signer.post("/v1/heartbeat", HB, request_id=rid)
    assert r.status_code == 409 and r.json()["detail"] == "DUPLICATE_REQUEST_ID"


def test_invalid_request_id_format(signer):
    r = signer.post("/v1/heartbeat", HB, request_id="short")
    assert r.status_code == 400


def test_malformed_json(client, signer):
    body = b'{"account_login": "1001", '
    r = client.post("/v1/heartbeat", content=body, headers=signer.headers("POST", "/v1/heartbeat", body))
    assert r.status_code == 422


def test_unknown_fields_rejected(signer):
    r = signer.post("/v1/heartbeat", {**HB, "inject": "x"})
    assert r.status_code == 422


def test_invalid_symbol_in_signal(signer):
    bars = [[1_700_000_000 + i * 900, 10, 11, 9, 10, 1, 20] for i in range(60)]
    payload = {"account_login": "1001", "symbol": "NOT_CONFIGURED", "timeframe": "M15", "bid": 10, "ask": 10.1,
               "spread_points": 10, "market_open": True, "ea_mode": "PAPER", "bars": bars}
    r = signer.post("/v1/signal", payload)
    assert r.status_code == 422 and "INVALID_SYMBOL" in r.json()["detail"]


def test_invalid_bars_rejected(signer):
    bars = [[1_700_000_000 + i * 900, 10, 9, 11, 10, 1, 20] for i in range(60)]  # high < low
    payload = {"account_login": "1001", "symbol": "XAUUSD.t", "timeframe": "M15", "bid": 10, "ask": 10.1,
               "spread_points": 10, "market_open": True, "ea_mode": "PAPER", "bars": bars}
    assert signer.post("/v1/signal", payload).status_code == 422


@pytest.mark.parametrize("volume", [-0.01, "abc"])
def test_invalid_volume_in_trade_event(signer, volume):
    ev = {"event_id": "evt-0000000001", "account_login": "1001", "event": "OPENED", "symbol": "XAUUSD.t",
          "direction": "BUY", "volume": volume, "time": 1_700_000_000}
    assert signer.post("/v1/trade", ev).status_code == 422


def test_admin_endpoints_require_jwt(client, signer):
    assert client.post("/v1/kill-switch", json={"reason": "test"}).status_code == 401
    # A valid EA signature is NOT enough for admin controls.
    assert signer.post("/v1/kill-switch", {"reason": "test"}).status_code == 401
    bad = {"Authorization": "Bearer not.a.jwt"}
    assert client.post("/v1/kill-switch", json={"reason": "test"}, headers=bad).status_code == 401


def test_read_endpoints_require_auth(client, admin_headers):
    for path in ("/v1/portfolio", "/v1/trades", "/v1/performance", "/v1/settings", "/v1/status", "/v1/news",
                 "/v1/model/status"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers=admin_headers).status_code == 200, path


def test_login_with_wrong_api_key(client):
    assert client.post("/v1/auth", json={"api_key": "x" * 32}).status_code == 401


def test_settings_cannot_loosen_or_go_negative(client, admin_headers):
    assert client.post("/v1/settings", json={"risk_per_trade": -0.1}, headers=admin_headers).status_code == 422
    assert client.post("/v1/settings", json={"risk_per_trade": 0.5}, headers=admin_headers).status_code == 422
    assert client.post("/v1/settings", json={"max_positions": 99}, headers=admin_headers).status_code == 422
    assert client.post("/v1/settings", json={"confidence_threshold": 0.5}, headers=admin_headers).status_code == 422
    ok = client.post("/v1/settings", json={"risk_per_trade": 0.0025, "max_positions": 4}, headers=admin_headers)
    assert ok.status_code == 200
    assert ok.json()["effective_limits"]["risk_per_trade"] == 0.0025


def test_settings_endpoint_hides_secrets(client, admin_headers):
    text = client.get("/v1/settings", headers=admin_headers).text
    for secret in (EA_SECRET, "jwt-test-secret", "admin-test-key"):
        assert secret not in text


def test_reset_requires_explicit_confirmation(client, admin_headers):
    r = client.post("/v1/reset-risk", json={"items": ["kill_switch"], "confirm": "yes"}, headers=admin_headers)
    assert r.status_code == 422


def test_rate_limit(tmp_path):
    settings = make_settings(tmp_path, rate_limit_auth_per_minute=3)
    db = Database("sqlite://")
    db.create_all()
    with TestClient(create_app(settings, Container(settings, db))) as c:
        codes = [c.post("/v1/auth", json={"api_key": "y" * 32}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]


def test_ea_auth_not_configured_fails_closed(tmp_path):
    settings = make_settings(tmp_path, ea_hmac_secret="")
    db = Database("sqlite://")
    db.create_all()
    with TestClient(create_app(settings, Container(settings, db))) as c:
        r = Signer(c).post("/v1/heartbeat", HB)
    assert r.status_code == 503


@pytest.mark.parametrize("kw,reason", [
    (dict(sl=0.0), "STOP_LOSS_REQUIRED"),
    (dict(sl=2001.0), "INVALID_SL"),
    (dict(tp=1999.0), "INVALID_TP"),
    (dict(volume=0.005), "INVALID_VOLUME"),
    (dict(volume=0.015), "VOLUME_NOT_ON_STEP"),
    (dict(volume=100.0), "INVALID_VOLUME"),
])
def test_order_validation(kw, reason):
    args = dict(direction=Direction.BUY, volume=0.1, price=2000.2, sl=1990.0, tp=2020.0, spec=gold_spec(),
                bid=2000.0, ask=2000.2)
    args.update(kw)
    assert validate_order(**args).reason == reason
