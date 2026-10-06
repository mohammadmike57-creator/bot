"""Strategy behaviour on hand-built feature frames (strategies only read feature columns)."""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.domain import Direction, Regime
from app.sessions.session_engine import SessionEngine
from app.strategies.base import StrategyContext, StrategyParams
from app.strategies.gold_tokyo import GoldTokyoStrategy
from app.strategies.london_trend import LondonTrendStrategy
from app.strategies.newyork_trend import NewYorkTrendStrategy
from app.strategies.router import StrategyRouter
from app.strategies.us30_strategy import US30Strategy

ENGINE = SessionEngine()


def frame(end: datetime, n: int = 120, price: float = 2000.0) -> pd.DataFrame:
    times = [end - timedelta(minutes=15 * (n - i)) for i in range(n)]
    df = pd.DataFrame({
        "time": pd.to_datetime(times, utc=True), "open": price, "high": price + 1, "low": price - 1, "close": price,
        "atr": 2.0, "ema20": price, "ema50": price, "adx": 15.0, "rsi": 50.0, "rsi_14": 0.5, "body_ratio": 0.0,
        "upper_wick_ratio": 0.1, "lower_wick_ratio": 0.1, "range_atr": 1.0, "dist_high20_atr": 1.0,
        "dist_low20_atr": 1.0, "spread_atr": 0.02,
    })
    return df


def ctx(df: pd.DataFrame, now: datetime, regime: Regime, symbol: str = "XAUUSD") -> StrategyContext:
    close = float(df["close"].iloc[-1])
    return StrategyContext(symbol=symbol, timeframe="M15", features=df, regime=regime,
                           session=ENGINE.classify(now), bid=close, ask=close + 0.2, params=StrategyParams(),
                           session_engine=ENGINE)


def assert_valid(sig):
    d = sig.direction
    assert (sig.entry - sig.stop_loss) * d.sign > 0, "stop on wrong side"
    assert (sig.take_profit - sig.entry) * d.sign >= abs(sig.entry - sig.stop_loss) - 1e-9, "RR < 1"


TOKYO_NOW = datetime(2026, 10, 6, 5, 0, tzinfo=UTC)  # 14:00 Tokyo
LONDON_NOW = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)  # 09:00 London (BST), Tokyo closed
NY_NOW = datetime(2026, 10, 6, 18, 0, tzinfo=UTC)  # 14:00 New York, London closed


def tokyo_range_frame():
    df = frame(TOKYO_NOW)
    in_tokyo = df["time"] >= pd.Timestamp(datetime(2026, 10, 6, 0, 0, tzinfo=UTC))
    df.loc[in_tokyo, "high"] = 2003.0
    df.loc[in_tokyo, "low"] = 1997.0
    return df


def test_tokyo_range_fade_sells_at_range_high():
    df = tokyo_range_frame()
    i = df.index[-1]
    df.loc[i, ["open", "high", "low", "close", "rsi_14", "upper_wick_ratio"]] = [2002.4, 2003.0, 2002.0, 2002.2, 0.75, 0.5]
    sig = GoldTokyoStrategy().evaluate(ctx(df, TOKYO_NOW, Regime.RANGE))
    assert sig is not None and sig.direction is Direction.SELL
    assert sig.take_profit == pytest.approx(2000.0)  # targets the range midpoint
    assert_valid(sig)


def test_tokyo_needs_rejection_candle():
    df = tokyo_range_frame()
    i = df.index[-1]
    df.loc[i, ["open", "high", "low", "close", "rsi_14", "upper_wick_ratio"]] = [2002.0, 2003.0, 2001.9, 2002.9, 0.75, 0.1]
    assert GoldTokyoStrategy().evaluate(ctx(df, TOKYO_NOW, Regime.RANGE)) is None  # bullish close, no wick


def test_tokyo_strategy_not_used_in_london():
    df = tokyo_range_frame()
    assert not GoldTokyoStrategy().applicable(ctx(df, LONDON_NOW, Regime.RANGE))


def test_tokyo_rejects_wide_spread():
    df = tokyo_range_frame()
    i = df.index[-1]
    df.loc[i, ["open", "high", "low", "close", "rsi_14", "upper_wick_ratio", "spread_atr"]] = \
        [2002.4, 2003.0, 2002.0, 2002.2, 0.75, 0.5, 0.2]
    assert GoldTokyoStrategy().evaluate(ctx(df, TOKYO_NOW, Regime.RANGE)) is None


def london_breakout_frame(confirm: bool):
    df = frame(LONDON_NOW)
    asian = (df["time"] >= pd.Timestamp(datetime(2026, 10, 6, 0, 0, tzinfo=UTC))) & \
            (df["time"] < pd.Timestamp(datetime(2026, 10, 6, 7, 0, tzinfo=UTC)))
    df.loc[asian, ["high", "low"]] = [2002.0, 1998.0]
    df.loc[df.index[-2], ["open", "high", "low", "close"]] = [2001.0, 2003.0, 2000.5, 2002.5 if confirm else 2001.5]
    df.loc[df.index[-1], ["open", "high", "low", "close", "body_ratio", "range_atr", "adx", "ema20", "ema50"]] = \
        [2002.5, 2004.5, 2002.4, 2004.3, 0.8, 1.2, 28.0, 2001.0, 2000.0]
    return df


def test_london_breakout_requires_two_closes():
    sig = LondonTrendStrategy().evaluate(ctx(london_breakout_frame(True), LONDON_NOW, Regime.TREND_UP))
    assert sig is not None and sig.direction is Direction.BUY and "BREAKOUT" in sig.reason
    assert_valid(sig)
    unconfirmed = LondonTrendStrategy().evaluate(ctx(london_breakout_frame(False), LONDON_NOW, Regime.TREND_UP))
    assert unconfirmed is None or "BREAKOUT" not in unconfirmed.reason


def test_london_breakout_needs_adx():
    df = london_breakout_frame(True)
    df.loc[df.index[-1], "adx"] = 15.0  # also below the pullback ADX filter
    assert LondonTrendStrategy().evaluate(ctx(df, LONDON_NOW, Regime.TREND_UP)) is None


def test_high_volatility_halves_risk():
    sig = LondonTrendStrategy().evaluate(ctx(london_breakout_frame(True), LONDON_NOW, Regime.HIGH_VOLATILITY))
    assert sig is not None and sig.risk_scale == pytest.approx(0.5)


def test_new_york_skips_first_minutes():
    now = datetime(2026, 10, 6, 12, 5, tzinfo=UTC)  # 08:05 NY, also London -> overlap; use NY strategy directly
    c = ctx(frame(now), now, Regime.TREND_UP)
    assert NewYorkTrendStrategy().generate(c) is None or c.session.minutes_into_primary >= 15


def test_new_york_trend_pullback():
    df = frame(NY_NOW)
    df.loc[df.index[-5:], ["low"]] = 1999.5
    df.loc[df.index[-1], ["open", "high", "low", "close", "ema20", "ema50", "adx", "rsi_14"]] = \
        [2000.2, 2001.6, 1999.9, 2001.5, 2000.0, 1998.0, 30.0, 0.6]
    sig = NewYorkTrendStrategy().evaluate(ctx(df, NY_NOW, Regime.TREND_UP))
    assert sig is not None and sig.direction is Direction.BUY and sig.strategy.endswith("CONTINUATION")
    assert_valid(sig)


def test_us30_does_not_trade_premarket():
    now = datetime(2026, 10, 6, 13, 0, tzinfo=UTC)  # 09:00 NY
    df = frame(now, price=42000)
    assert US30Strategy().evaluate(ctx(df, now, Regime.TREND_UP, "US30")) is None


@pytest.mark.parametrize("now,regime", [
    (NY_NOW, Regime.UNSTABLE),  # unstable -> never trade
    (datetime(2026, 10, 10, 12, 0, tzinfo=UTC), Regime.TREND_UP),  # Saturday: off hours
    (datetime(2026, 10, 6, 22, 0, tzinfo=UTC), Regime.TREND_UP),  # Sydney only: low activity, off by default
])
def test_router_stays_flat(now, regime):
    router = StrategyRouter()
    sig, notes = router.evaluate(ctx(frame(now), now, regime))
    assert sig is None and notes


def test_router_selects_by_asset_class():
    router = StrategyRouter()
    names = {s.name for s in router.candidates(ctx(frame(TOKYO_NOW), TOKYO_NOW, Regime.RANGE, "XAUUSD"))}
    assert names == {"TOKYO_GOLD"}
    assert router.candidates(ctx(frame(TOKYO_NOW), TOKYO_NOW, Regime.TREND_UP, "OIL")) == []  # oil: London/NY only
