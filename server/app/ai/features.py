"""Causal feature pipeline.

Every feature at bar ``t`` uses only data available at the *close* of bar
``t`` (rolling / ewm windows look backwards only). This is enforced by a unit
test that recomputes features on truncated data and compares.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.domain import SessionName
from app.sessions.session_engine import SessionEngine

REQUIRED_COLUMNS = ("time", "open", "high", "low", "close")

SESSION_FEATURES = [f"sess_{s.value.lower()}" for s in SessionName if s is not SessionName.OFF_HOURS]

FEATURE_COLUMNS: list[str] = [
    "ret_1",
    "ret_5",
    "ret_20",
    "atr_pct",
    "atr_pctile",
    "rsi_14",
    "dist_ema20_atr",
    "dist_ema50_atr",
    "ema20_slope_atr",
    "ema_fast_slow_atr",
    "macd_hist_atr",
    "adx_14",
    "di_diff",
    "vol_20",
    "vol_ratio",
    "body_ratio",
    "upper_wick_ratio",
    "lower_wick_ratio",
    "range_atr",
    "dist_high20_atr",
    "dist_low20_atr",
    "momentum_10",
    "spread_atr",
    "hour_sin",
    "hour_cos",
    "dow",
    "news_minutes",
    "corr_ref",
    *SESSION_FEATURES,
]


def wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    return wilder(tr, period)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), period)
    loss = wilder(-delta.clip(upper=0), period)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def adx(df: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr_ = atr(df, period)
    plus_di = 100 * wilder(pd.Series(plus_dm, index=df.index), period) / atr_
    minus_di = 100 * wilder(pd.Series(minus_dm, index=df.index), period) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx.fillna(0), period), plus_di, minus_di


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def _rolling_pctile(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank of the latest value within the trailing window (causal)."""
    return series.rolling(window, min_periods=max(20, window // 4)).apply(
        lambda w: (w[:-1] < w[-1]).mean() if len(w) > 1 else np.nan, raw=True
    )


@dataclass
class FeatureConfig:
    atr_period: int = 14
    rsi_period: int = 14
    adx_period: int = 14
    atr_pctile_window: int = 200
    point: float = 0.0  # needed to convert spread (points) to price


def validate_bars(df: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bars missing columns: {missing}")
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True)
    out = out.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)
    bad = (out["high"] < out["low"]) | (out["close"] <= 0)
    if bad.any():
        raise ValueError("bars contain invalid OHLC rows")
    return out


def _session_flags(times: pd.Series, engine: SessionEngine) -> pd.DataFrame:
    # Sessions change at most every 30 minutes - classify unique half-hours only.
    keys = times.dt.floor("30min")
    cache: dict[pd.Timestamp, frozenset[SessionName]] = {}
    for k in keys.unique():
        cache[k] = engine.classify(k.to_pydatetime()).active
    data = {
        col: keys.map(lambda k, s=s: 1.0 if s in cache[k] else 0.0).astype(float)
        for col, s in zip(SESSION_FEATURES, [s for s in SessionName if s is not SessionName.OFF_HOURS], strict=True)
    }
    return pd.DataFrame(data, index=times.index)


def compute_features(
    bars: pd.DataFrame,
    config: FeatureConfig | None = None,
    *,
    news_minutes: pd.Series | None = None,
    reference_close: pd.Series | None = None,
    session_engine: SessionEngine | None = None,
) -> pd.DataFrame:
    """Return ``bars`` plus all indicator and feature columns.

    ``reference_close`` (optional) is another instrument's close indexed by UTC
    timestamp, used for a rolling correlation feature.
    """
    cfg = config or FeatureConfig()
    df = validate_bars(bars)
    close = df["close"]
    logret = np.log(close).diff()

    df["atr"] = atr(df, cfg.atr_period)
    df["ema20"] = ema(close, 20)
    df["ema50"] = ema(close, 50)
    df["ema200"] = ema(close, 200)
    df["rsi"] = rsi(close, cfg.rsi_period)
    df["adx"], df["plus_di"], df["minus_di"] = adx(df, cfg.adx_period)
    macd = ema(close, 12) - ema(close, 26)
    macd_hist = macd - macd.ewm(span=9, adjust=False).mean()
    a = df["atr"].replace(0, np.nan)
    rng = (df["high"] - df["low"]).replace(0, np.nan)

    df["ret_1"] = logret
    df["ret_5"] = np.log(close / close.shift(5))
    df["ret_20"] = np.log(close / close.shift(20))
    df["atr_pct"] = df["atr"] / close
    df["atr_pctile"] = _rolling_pctile(df["atr"], cfg.atr_pctile_window)
    df["rsi_14"] = df["rsi"] / 100.0
    df["dist_ema20_atr"] = (close - df["ema20"]) / a
    df["dist_ema50_atr"] = (close - df["ema50"]) / a
    df["ema20_slope_atr"] = (df["ema20"] - df["ema20"].shift(5)) / a
    df["ema_fast_slow_atr"] = (df["ema20"] - df["ema50"]) / a
    df["macd_hist_atr"] = macd_hist / a
    df["adx_14"] = df["adx"] / 100.0
    df["di_diff"] = (df["plus_di"] - df["minus_di"]) / 100.0
    df["vol_20"] = logret.rolling(20).std()
    df["vol_ratio"] = logret.rolling(10).std() / logret.rolling(50).std()
    df["body_ratio"] = (close - df["open"]) / rng
    df["upper_wick_ratio"] = (df["high"] - df[["open", "close"]].max(axis=1)) / rng
    df["lower_wick_ratio"] = (df[["open", "close"]].min(axis=1) - df["low"]) / rng
    df["range_atr"] = (df["high"] - df["low"]) / a
    df["high20"] = df["high"].rolling(20).max()
    df["low20"] = df["low"].rolling(20).min()
    df["dist_high20_atr"] = (df["high20"] - close) / a
    df["dist_low20_atr"] = (close - df["low20"]) / a
    df["momentum_10"] = close / close.shift(10) - 1
    if "spread" in df.columns and cfg.point > 0:
        df["spread_atr"] = df["spread"] * cfg.point / a
    else:
        df["spread_atr"] = 0.0
    hours = df["time"].dt.hour + df["time"].dt.minute / 60
    df["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    df["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    df["dow"] = df["time"].dt.dayofweek / 6.0
    df["news_minutes"] = (
        news_minutes.reindex(df.index).clip(upper=1440).fillna(1440) / 1440 if news_minutes is not None else 1.0
    )
    if reference_close is not None:
        aligned = reference_close.reindex(pd.DatetimeIndex(df["time"])).to_numpy(dtype=float)
        ref = pd.Series(aligned, index=df.index).ffill()
        df["corr_ref"] = logret.rolling(50).corr(np.log(ref).diff())
    else:
        df["corr_ref"] = 0.0
    flags = _session_flags(df["time"], session_engine or SessionEngine())
    for col in SESSION_FEATURES:
        df[col] = flags[col]
    df[FEATURE_COLUMNS] = df[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    return df


def feature_matrix(features: pd.DataFrame) -> pd.DataFrame:
    return features[FEATURE_COLUMNS]


class FeatureCache:
    """LRU cache keyed by (symbol, timeframe, last bar time, bar count).

    The EA requests a decision once per new candle; repeated requests for the
    same candle (retries) reuse the computed frame instead of recomputing.
    """

    def __init__(self, max_items: int = 64):
        self._items: OrderedDict[tuple[str, str, str, int], pd.DataFrame] = OrderedDict()
        self._max = max_items
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, symbol: str, timeframe: str, bars: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
        last = str(pd.to_datetime(bars["time"].iloc[-1], utc=True))
        key = (symbol, timeframe, last, len(bars))
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                self.hits += 1
                return self._items[key]
        frame = compute_features(bars, **kwargs)  # type: ignore[arg-type]
        with self._lock:
            self.misses += 1
            self._items[key] = frame
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return frame
