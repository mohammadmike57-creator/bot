"""End-to-end PAPER flow through the real API with signed requests."""

from datetime import timedelta

import pandas as pd
import pytest

from app.api import account as account_api
from app.api import health as health_api
from app.api import signals as signals_api
from app.api import trades as trades_api
from app.domain import Direction
from app.execution.execution_state import can_transition, is_expired
from app.strategies.base import StrategySignal
from tests.conftest import FIXED_NOW, make_bars

SPEC = {"broker_symbol": "XAUUSD.t", "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 1.0,
        "contract_size": 100.0, "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01, "stops_level": 0,
        "margin_per_lot": 400.0, "trade_mode": "FULL", "spread_points": 20}
ACCOUNT = {"login": "1001", "server": "CFI-Demo", "company": "test", "currency": "USD", "leverage": 500,
           "trade_mode": "DEMO", "margin_mode": "HEDGING", "trade_allowed": True, "balance": 10000.0,
           "equity": 10000.0, "margin": 0.0, "free_margin": 10000.0, "margin_level": 0.0, "symbols": [SPEC]}


def heartbeat(quotes=()):
    return {"account_login": "1001", "ea_version": "1.0.0", "ea_mode": "PAPER", "terminal_connected": True,
            "trade_allowed": True, "open_positions": 0, "quotes": list(quotes)}


def bars_payload(n=300):
    df = make_bars(n + 50, start=str((FIXED_NOW - timedelta(minutes=15 * (n + 120))).replace(tzinfo=None)))
    df = df[df["time"] < pd.Timestamp(FIXED_NOW)].tail(n)
    rows = [[int(t.timestamp()), o, h, low, c, v, s] for t, o, h, low, c, v, s in
            zip(df["time"], df["open"], df["high"], df["low"], df["close"], df["tick_volume"], df["spread"],
                strict=True)]
    return rows, float(df["close"].iloc[-1])


def signal_payload(rows, bid):
    return {"account_login": "1001", "symbol": "XAUUSD.t", "timeframe": "M15", "bid": bid, "ask": round(bid + 0.2, 2),
            "spread_points": 20, "market_open": True, "ea_mode": "PAPER", "bars": rows}


@pytest.fixture
def frozen(monkeypatch):
    for mod in (signals_api, account_api, trades_api, health_api):
        monkeypatch.setattr(mod, "utcnow", lambda: FIXED_NOW)


@pytest.fixture
def setup(client, signer, container, frozen):
    container.settings.initial_equity = 10000.0
    with container.db.session() as s:
        from app.database import repositories as repo

        repo.upsert_account(s, {"login": repo.PAPER_LOGIN, "balance": 10000.0, "equity": 10000.0,
                                "free_margin": 10000.0, "trade_allowed": True, "source": "PAPER",
                                "margin_mode": "HEDGING"})
    container.calendar.load_events([], FIXED_NOW)  # calendar healthy, no events
    assert signer.post("/v1/account", ACCOUNT).status_code == 200
    assert signer.post("/v1/heartbeat", heartbeat()).status_code == 200
    return container


def force_signal(container, monkeypatch, direction=Direction.BUY):
    def fake(ctx):
        entry = ctx.entry_for(direction)
        risk = 2 * ctx.atr
        return StrategySignal(direction, entry, entry - direction.sign * risk, entry + direction.sign * 2 * risk,
                              entry + direction.sign * 2 * risk, "TEST_STRATEGY", "FORCED_FOR_TEST"), ["TEST:signal"]

    monkeypatch.setattr(container.router, "evaluate", fake)


def test_paper_trade_lifecycle(setup, signer, client, admin_headers, monkeypatch):
    c = setup
    force_signal(c, monkeypatch)
    rows, bid = bars_payload()
    r = signer.post("/v1/signal", signal_payload(rows, bid))
    assert r.status_code == 200, r.text
    sig = r.json()
    assert sig["action"] == "BUY", sig["reason"] + " " + sig["decision"]
    assert sig["paper"] is True and sig["trading_mode"] == "PAPER"
    assert sig["risk_approved"] and 0 < sig["risk_percent"] <= 0.5 + 1e-9
    assert sig["stop_loss"] < bid < sig["take_profit"]
    assert "DECISION=BUY" in sig["decision"]

    open_trades = client.get("/v1/trades?status=open&source=PAPER", headers=admin_headers).json()
    assert len(open_trades) == 1 and open_trades[0]["strategy"] == "TEST_STRATEGY"

    # Duplicate request for the same closed bar returns the SAME signal (no second order)
    r2 = signer.post("/v1/signal", signal_payload(rows, bid))
    assert r2.json()["signal_id"] == sig["signal_id"]
    assert len(client.get("/v1/trades?status=open", headers=admin_headers).json()) == 1

    # Price runs through TP -> paper position closes with profit
    tp = sig["take_profit"]
    q = {"symbol": "XAUUSD.t", "bid": tp + 1, "ask": tp + 1.2, "spread_points": 20, "time": int(FIXED_NOW.timestamp())}
    assert signer.post("/v1/heartbeat", heartbeat([q])).status_code == 200
    closed = client.get("/v1/trades?status=closed", headers=admin_headers).json()
    assert len(closed) == 1 and closed[0]["profit"] > 0
    # Regression: with no open positions, paper equity must equal balance (no stale floating P/L).
    paper = client.get("/v1/account", headers=admin_headers).json()["paper"]
    assert paper["equity"] == pytest.approx(paper["balance"])
    assert paper["balance"] == pytest.approx(10000 + closed[0]["profit"])
    perf = client.get("/v1/performance?source=PAPER", headers=admin_headers).json()
    assert perf["trades"] == 1 and perf["win_rate"] == 1.0
    status = client.get("/v1/status", headers=admin_headers).json()
    assert status["trading_mode"] == "PAPER" and status["ea"]["status"] == "ONLINE"


def test_small_account_rejects_min_volume(setup, signer, monkeypatch):
    c = setup
    with c.db.session() as s:
        from app.database import repositories as repo

        acc = repo.get_account(s, repo.PAPER_LOGIN)
        acc.balance = acc.equity = acc.free_margin = 100.0
    force_signal(c, monkeypatch)
    rows, bid = bars_payload()
    sig = signer.post("/v1/signal", signal_payload(rows, bid)).json()
    assert sig["action"] == "NO_TRADE"
    assert "Minimum broker volume exceeds allowed account risk." in sig["reason"]


def test_kill_switch_blocks_and_requires_reset(setup, signer, client, admin_headers, monkeypatch):
    force_signal(setup, monkeypatch)
    r = client.post("/v1/kill-switch", json={"reason": "manual test"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["kill_switch"]
    hb = signer.post("/v1/heartbeat", heartbeat()).json()
    assert hb["kill_switch"] and hb["safe_mode"]
    rows, bid = bars_payload()
    assert signer.post("/v1/signal", signal_payload(rows, bid)).json()["reason"] == "KILL_SWITCH_ACTIVE"
    # pause/resume does NOT clear the kill switch
    client.post("/v1/control", json={"action": "RESUME"}, headers=admin_headers)
    assert client.get("/v1/controls", headers=admin_headers).json()["kill_switch"]
    r = client.post("/v1/reset-risk", json={"items": ["kill_switch"], "confirm": "RESET"}, headers=admin_headers)
    assert r.status_code == 200 and not r.json()["kill_switch"]


def test_pause_blocks_new_trades(setup, signer, client, admin_headers, monkeypatch):
    force_signal(setup, monkeypatch)
    client.post("/v1/control", json={"action": "PAUSE"}, headers=admin_headers)
    rows, bid = bars_payload()
    assert signer.post("/v1/signal", signal_payload(rows, bid)).json()["reason"] == "NEW_TRADES_PAUSED"


def test_news_service_down_blocks(client, signer, container, frozen, monkeypatch):
    # calendar never loaded -> NOT_CONFIGURED -> SAFE MODE
    signer.post("/v1/account", ACCOUNT)
    signer.post("/v1/heartbeat", heartbeat())
    force_signal(container, monkeypatch)
    rows, bid = bars_payload()
    sig = signer.post("/v1/signal", signal_payload(rows, bid)).json()
    assert sig["action"] == "NO_TRADE" and sig["reason"].startswith("NEWS_SERVICE_")


def test_stale_heartbeat_blocks(setup, signer, monkeypatch):
    force_signal(setup, monkeypatch)
    monkeypatch.setattr(signals_api, "utcnow", lambda: FIXED_NOW + timedelta(minutes=10))
    rows, bid = bars_payload()
    assert signer.post("/v1/signal", signal_payload(rows, bid)).json()["reason"] == "EA_HEARTBEAT_STALE"


def test_spread_filter(setup, signer, monkeypatch):
    force_signal(setup, monkeypatch)
    rows, bid = bars_payload()
    payload = signal_payload(rows, bid)
    payload["ask"] = bid + 50  # absurd spread
    assert signer.post("/v1/signal", payload).json()["reason"] == "SPREAD_TOO_HIGH"


def test_trade_event_idempotent_and_slippage(setup, signer, client, admin_headers):
    ev = {"event_id": "evt-open-000000001", "account_login": "1001", "event": "OPENED", "signal_id": "",
          "ticket": "555", "symbol": "XAUUSD.t", "magic": 1000101, "direction": "BUY", "volume": 0.01,
          "requested_price": 2000.00, "executed_price": 2000.05, "sl": 1990.0, "tp": 2020.0,
          "time": int(FIXED_NOW.timestamp())}
    first = signer.post("/v1/trade", ev).json()
    assert first["slippage_points"] == pytest.approx(5.0) and not first["duplicate"]
    assert signer.post("/v1/trade", ev).json()["duplicate"] is True  # retried after timeout
    trades = client.get("/v1/trades?source=MT5", headers=admin_headers).json()
    assert len(trades) == 1 and trades[0]["risk"] == pytest.approx(10.05)  # 10.05 price move * $100 per unit * 0.01 lot


def test_position_sync_tracks_hedged_positions(setup, signer, client, admin_headers):
    base = {"symbol": "XAUUSD.t", "magic": 1000101, "volume": 0.01, "entry_price": 2000.0, "current_price": 2001.0,
            "tp": 0.0, "profit": 1.0, "opened_at": int(FIXED_NOW.timestamp())}
    sync = {"account_login": "1001", "positions": [
        {**base, "ticket": "1", "direction": "BUY", "sl": 1990.0},
        {**base, "ticket": "2", "direction": "SELL", "sl": 2010.0, "profit": -1.0},
    ]}
    assert signer.post("/v1/position", sync).json()["created"] == 2
    pf = client.get("/v1/portfolio?source=MT5", headers=admin_headers).json()
    assert pf["hedged_symbols"] == ["XAUUSD"] and pf["open_positions"] == 2
    # Ticket 2 disappears -> marked closed
    sync["positions"] = sync["positions"][:1]
    assert signer.post("/v1/position", sync).json()["closed"] == 1


def test_signal_state_machine_and_expiry():
    assert can_transition("ISSUED", "EXECUTED")
    assert not can_transition("EXECUTED", "EXECUTED")  # duplicate fill report ignored
    assert not can_transition("EXPIRED", "EXECUTED")
    assert is_expired(FIXED_NOW, FIXED_NOW + timedelta(seconds=1))
    assert not is_expired(FIXED_NOW + timedelta(seconds=60), FIXED_NOW)


def test_losing_streak_pauses_signals(setup, signer, monkeypatch):
    from app.services.risk_state_service import apply_trade_result

    force_signal(setup, monkeypatch)
    with setup.db.session() as s:
        from app.database import repositories as repo

        for _ in range(3):
            apply_trade_result(s, repo.PAPER_LOGIN, -5.0, FIXED_NOW, setup.limits.loss_streak)
    rows, bid = bars_payload()
    sig = signer.post("/v1/signal", signal_payload(rows, bid)).json()
    assert sig["action"] == "NO_TRADE" and sig["reason"].startswith("LOSS_STREAK_COOLDOWN")
