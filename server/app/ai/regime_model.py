"""Market-regime classifier.

A transparent, deterministic classifier over causal features (ADX, EMA
structure, ATR percentile, bar range). It is deliberately rule-based so its
decisions are auditable; thresholds are configurable and can be optimised in
walk-forward tests. ``UNSTABLE`` always means NO TRADE.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from app.domain import Regime


@dataclass(frozen=True)
class RegimeConfig:
    trend_adx: float = 25.0
    range_adx: float = 20.0
    high_vol_pctile: float = 0.85
    low_vol_pctile: float = 0.15
    unstable_pctile: float = 0.98
    unstable_range_atr: float = 4.0


@dataclass(frozen=True)
class RegimeResult:
    regime: Regime
    reason: str


def classify_row(row: pd.Series, cfg: RegimeConfig = RegimeConfig()) -> RegimeResult:
    needed = ("adx", "ema20", "ema50", "atr", "atr_pctile", "range_atr", "close", "ema20_slope_atr")
    for col in needed:
        value = row.get(col)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return RegimeResult(Regime.UNSTABLE, f"INSUFFICIENT_DATA:{col}")
    pctile = float(row["atr_pctile"])
    if pctile >= cfg.unstable_pctile or float(row["range_atr"]) >= cfg.unstable_range_atr:
        return RegimeResult(Regime.UNSTABLE, f"EXTREME_VOLATILITY pctile={pctile:.2f} range_atr={row['range_atr']:.2f}")
    adx = float(row["adx"])
    up = row["ema20"] > row["ema50"] and row["ema20_slope_atr"] > 0 and row["close"] > row["ema50"]
    down = row["ema20"] < row["ema50"] and row["ema20_slope_atr"] < 0 and row["close"] < row["ema50"]
    if pctile >= cfg.high_vol_pctile:
        return RegimeResult(Regime.HIGH_VOLATILITY, f"atr_pctile={pctile:.2f}")
    if adx >= cfg.trend_adx and up:
        return RegimeResult(Regime.TREND_UP, f"adx={adx:.1f} ema20>ema50")
    if adx >= cfg.trend_adx and down:
        return RegimeResult(Regime.TREND_DOWN, f"adx={adx:.1f} ema20<ema50")
    if pctile <= cfg.low_vol_pctile and adx < cfg.range_adx:
        return RegimeResult(Regime.LOW_VOLATILITY, f"atr_pctile={pctile:.2f} adx={adx:.1f}")
    return RegimeResult(Regime.RANGE, f"adx={adx:.1f}")


def classify_series(features: pd.DataFrame, cfg: RegimeConfig = RegimeConfig()) -> pd.Series:
    return features.apply(lambda r: classify_row(r, cfg).regime.value, axis=1)
