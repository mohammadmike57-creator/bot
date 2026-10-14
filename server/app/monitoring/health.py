"""Aggregated component health for the dashboard status panel."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.database import repositories as repo
from app.sessions.market_hours import market_status

if TYPE_CHECKING:
    from app.services.container import Container


def system_status(c: Container, now: datetime) -> dict[str, Any]:
    db_ok = c.db.ping()
    controls = c.risk_state.controls() if db_ok else None
    ea: dict[str, Any] = {"status": "OFFLINE", "last_heartbeat": None}
    mt5_account = paper_account = None
    trades_today = 0
    if db_ok:
        with c.db.session() as s:
            st = repo.latest_ea_status(s)
            if st and st.last_heartbeat:
                age = (now - st.last_heartbeat).total_seconds()
                ea = {
                    "status": "ONLINE" if age <= c.settings.heartbeat_timeout_seconds else "OFFLINE",
                    "last_heartbeat": st.last_heartbeat.isoformat(), "age_seconds": round(age, 1),
                    "account_login": st.account_login, "ea_version": st.ea_version, "ea_mode": st.ea_mode,
                    "terminal_connected": st.terminal_connected, "trade_allowed": st.trade_allowed,
                    "local_kill_switch": st.local_kill_switch, "latency_ms": st.latency_ms,
                    "quotes": st.quotes, "open_positions": st.open_positions,
                    "last_signal_at": st.last_signal_at.isoformat() if st.last_signal_at else None,
                    "last_trade_at": st.last_trade_at.isoformat() if st.last_trade_at else None,
                }
            for label, acc in (("mt5", repo.latest_mt5_account(s)), ("paper", repo.get_account(s, repo.PAPER_LOGIN))):
                if acc is None:
                    continue
                risk = repo.get_risk_state(s, acc.login)
                data = {
                    "login": acc.login, "balance": acc.balance, "equity": acc.equity, "free_margin": acc.free_margin,
                    "margin_level": acc.margin_level, "margin": acc.margin, "currency": acc.currency,
                    "daily_pnl": round(acc.equity - risk.day_start_equity, 2) if risk.day_start_equity else 0.0,
                    "total_pnl": round(acc.equity - risk.initial_equity, 2) if risk.initial_equity else 0.0,
                    "drawdown": round((risk.peak_equity - acc.equity) / risk.peak_equity, 4) if risk.peak_equity else 0.0,
                    "daily_loss_locked": risk.daily_loss_locked, "drawdown_locked": risk.drawdown_locked,
                    "consecutive_losses": risk.consecutive_losses or 0,
                    "cooldown_until": risk.cooldown_until.isoformat()
                    if risk.cooldown_until and risk.cooldown_until > now else None,
                }
                if label == "mt5":
                    mt5_account = data
                else:
                    paper_account = data
            trades_today = sum(1 for p in repo.closed_positions(s, None, 500)
                               if p.closed_at and (now - p.closed_at).total_seconds() < 86400)
    session = c.session_engine.classify(now)
    mkt = market_status(now)
    news = c.calendar.status(now)
    return {
        "time": now.isoformat(),
        "trading_mode": c.settings.effective_trading_mode.value,
        "configured_mode": c.settings.trading_mode.value,
        "live_permitted": c.settings.live_trading_permitted,
        "backend": {"status": "ONLINE", "database": "OK" if db_ok else "DOWN"},
        "ea": ea,
        "controls": controls.__dict__ if controls else None,
        "session": {"primary": session.primary.value, "active": sorted(x.value for x in session.active),
                    "activity": session.activity.value, "market_open": mkt.open, "market_reason": mkt.reason},
        "regimes": c.last_regime,
        "confidence": c.last_confidence,
        "news": {"status": news.value},
        "model": {"mode": c.inference.mode.value, "version": c.inference.version,
                  "problems": c.inference.health.problems()},
        "accounts": {"mt5": mt5_account, "paper": paper_account},
        "trades_today": trades_today,
        "error_rate": c.metrics.snapshot()["error_rate"],
    }
