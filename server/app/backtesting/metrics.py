"""Performance metrics shared by backtests, walk-forward and the live dashboard."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TradeResult:
    ticket: str
    symbol: str
    strategy: str
    direction: str
    session: str
    regime: str
    opened_at: datetime
    closed_at: datetime
    profit: float
    risk_money: float

    @property
    def r_multiple(self) -> float:
        return self.profit / self.risk_money if self.risk_money > 0 else 0.0


def max_drawdown(equity: list[float] | np.ndarray) -> float:
    arr = np.asarray(equity, dtype=float)
    if arr.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(arr)
    dd = np.where(peaks > 0, (peaks - arr) / peaks, 0.0)
    return float(dd.max())


def drawdown_series(equity: pd.Series) -> pd.Series:
    peaks = equity.cummax()
    return ((peaks - equity) / peaks.replace(0, np.nan)).fillna(0.0)


def _ratio(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def _group_stats(trades: list[TradeResult], key: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[TradeResult]] = defaultdict(list)
    for t in trades:
        groups[getattr(t, key) or "UNKNOWN"].append(t)
    out = {}
    for name, items in groups.items():
        wins = [t.profit for t in items if t.profit > 0]
        losses = [-t.profit for t in items if t.profit <= 0]
        pf = _ratio(sum(wins), sum(losses))
        out[name] = {
            "trades": len(items),
            "net": round(sum(t.profit for t in items), 2),
            "win_rate": round(len(wins) / len(items), 4),
            "profit_factor": round(pf, 3) if pf is not None else None,
            "avg_r": round(float(np.mean([t.r_multiple for t in items])), 3),
        }
    return out  # type: ignore[return-value]


def compute_metrics(trades: list[TradeResult], equity_times: list[datetime], equity_values: list[float],
                    initial_equity: float) -> dict[str, Any]:
    n = len(trades)
    profits = np.array([t.profit for t in trades], dtype=float)
    wins = profits[profits > 0]
    losses = -profits[profits <= 0]
    gross_profit, gross_loss = float(wins.sum()), float(losses.sum())
    pf = _ratio(gross_profit, gross_loss)
    eq = pd.Series(equity_values, index=pd.to_datetime(equity_times, utc=True)) if equity_values else pd.Series(dtype=float)
    mdd = max_drawdown(eq.to_numpy()) if len(eq) else 0.0
    final = float(eq.iloc[-1]) if len(eq) else initial_equity
    total_return = (final - initial_equity) / initial_equity if initial_equity > 0 else 0.0

    sharpe = sortino = None
    if len(eq) > 2:
        daily = eq.resample("1D").last().dropna().pct_change().dropna()
        if len(daily) > 5 and daily.std() > 0:
            sharpe = float(daily.mean() / daily.std() * math.sqrt(252))
            downside = daily[daily < 0]
            if len(downside) > 1 and downside.std() > 0:
                sortino = float(daily.mean() / downside.std() * math.sqrt(252))

    daily_pnl: dict[str, float] = defaultdict(float)
    monthly_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        daily_pnl[t.closed_at.strftime("%Y-%m-%d")] += t.profit
        monthly_pnl[t.closed_at.strftime("%Y-%m")] += t.profit

    return {
        "trades": n,
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / n, 4) if n else 0.0,
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_profit": round(float(profits.sum()), 2) if n else 0.0,
        "profit_factor": round(pf, 3) if pf is not None else None,
        "avg_win": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "expectancy": round(float(profits.mean()), 4) if n else 0.0,
        "avg_r": round(float(np.mean([t.r_multiple for t in trades])), 3) if n else 0.0,
        "max_drawdown": round(mdd, 4),
        "total_return": round(total_return, 4),
        "return_over_drawdown": round(total_return / mdd, 3) if mdd > 0 else None,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "final_equity": round(final, 2),
        "by_symbol": _group_stats(trades, "symbol"),
        "by_strategy": _group_stats(trades, "strategy"),
        "by_session": _group_stats(trades, "session"),
        "by_regime": _group_stats(trades, "regime"),
        "by_direction": _group_stats(trades, "direction"),
        "daily_pnl": {k: round(v, 2) for k, v in sorted(daily_pnl.items())},
        "monthly_pnl": {k: round(v, 2) for k, v in sorted(monthly_pnl.items())},
    }
