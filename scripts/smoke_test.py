#!/usr/bin/env python3
"""Connectivity + authentication smoke test against a running backend.

It signs requests exactly like the EA does, so a pass here means the EA's
credentials and the server clock are fine. It sends ONLY a PAPER-mode
heartbeat (no account data, no trades, no signals).

  BACKEND_URL=http://localhost:8000 EA_API_KEY_ID=... EA_HMAC_SECRET=... API_KEY=... \
      python scripts/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from app.services.security import sign  # noqa: E402


def main() -> int:
    base = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
    key_id, secret = os.environ.get("EA_API_KEY_ID", ""), os.environ.get("EA_HMAC_SECRET", "")
    admin = os.environ.get("API_KEY", "")
    ok = True
    with httpx.Client(timeout=15) as c:
        r = c.get(f"{base}/health")
        print("GET /health ->", r.status_code, r.text)
        ok &= r.status_code == 200
        if key_id and secret:
            body = json.dumps({"account_login": "SMOKETEST", "ea_version": "smoke", "ea_mode": "PAPER",
                               "terminal_connected": False, "trade_allowed": False, "open_positions": 0,
                               "quotes": []}).encode()
            ts, rid = str(int(time.time())), uuid.uuid4().hex
            headers = {"Content-Type": "application/json", "X-Api-Key-Id": key_id, "X-Timestamp": ts,
                       "X-Request-Id": rid, "X-Signature": sign(secret, "POST", "/v1/heartbeat", ts, rid, body)}
            r = c.post(f"{base}/v1/heartbeat", content=body, headers=headers)
            print("POST /v1/heartbeat (signed) ->", r.status_code, r.text[:300])
            ok &= r.status_code == 200
            r = c.post(f"{base}/v1/heartbeat", content=body, headers=headers)
            print("replayed request ->", r.status_code, "(409 expected)")
            ok &= r.status_code == 409
        else:
            print("skip signed heartbeat: set EA_API_KEY_ID and EA_HMAC_SECRET")
        if admin:
            r = c.post(f"{base}/v1/auth", json={"api_key": admin})
            print("POST /v1/auth ->", r.status_code)
            ok &= r.status_code == 200
            if r.status_code == 200:
                token = r.json()["access_token"]
                s = c.get(f"{base}/v1/status", headers={"Authorization": f"Bearer {token}"}).json()
                print("trading_mode:", s["trading_mode"], "| live_permitted:", s["live_permitted"],
                      "| news:", s["news"]["status"], "| model:", s["model"]["mode"])
        r = c.get(f"{base}/v1/portfolio")
        print("unauthenticated /v1/portfolio ->", r.status_code, "(401 expected)")
        ok &= r.status_code == 401
    print("\nSMOKE TEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
