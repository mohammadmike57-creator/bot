from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.config.settings import NewsFailurePolicy
from app.news.economic_calendar import CalendarStatus, EconomicCalendar
from app.news.news_filter import BlackoutConfig, NewsFilter, is_relevant
from app.news.news_provider import (
    EconomicEvent,
    FinnhubProvider,
    FmpProvider,
    NewsProviderError,
    TradingEconomicsProvider,
    build_provider,
)

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def ev(name, currency="USD", importance="HIGH", minutes=10):
    return EconomicEvent(name, currency, "US", importance, NOW + timedelta(minutes=minutes))


def filt(events, **cfg):
    cal = EconomicCalendar(provider=None)
    cal.load_events(events, NOW)
    return NewsFilter(cal, BlackoutConfig(**cfg))


def test_relevance_by_symbol():
    nfp = ev("Nonfarm Payrolls")
    crude = ev("EIA Crude Oil Inventories")
    ecb = ev("ECB Interest Rate Decision", currency="EUR")
    assert all(is_relevant(nfp, s) for s in ("XAUUSD", "OIL", "US30"))
    assert is_relevant(crude, "OIL")
    assert not is_relevant(crude, "XAUUSD") and not is_relevant(crude, "US30")
    assert not any(is_relevant(ecb, s) for s in ("XAUUSD", "OIL", "US30"))


def test_high_impact_blackout_window():
    f = filt([ev("CPI m/m", minutes=10)])
    assert f.check("XAUUSD", NOW).blocked  # 10 min before (window 15)
    assert not f.check("XAUUSD", NOW - timedelta(minutes=10)).blocked  # 20 min before
    assert f.check("XAUUSD", NOW + timedelta(minutes=29)).blocked  # 19 min after
    assert not f.check("XAUUSD", NOW + timedelta(minutes=31)).blocked  # 21 min after


def test_inventory_event_blocks_oil_only():
    f = filt([ev("EIA Crude Oil Inventories", minutes=5)])
    assert f.check("OIL", NOW).blocked
    assert not f.check("XAUUSD", NOW).blocked
    assert not f.check("US30", NOW).blocked


def test_medium_only_when_enabled():
    events = [ev("Consumer Credit", importance="MEDIUM", minutes=2)]
    assert not filt(events).check("US30", NOW).blocked
    assert filt(events, block_medium=True).check("US30", NOW).blocked


def test_failure_means_safe_mode():
    class Broken:
        name = "broken"

        def fetch(self, start, end):
            raise NewsProviderError("down")

    cal = EconomicCalendar(provider=Broken())
    cal.refresh_if_due(NOW, force=True)
    assert cal.status(NOW) is CalendarStatus.FAILED
    d = NewsFilter(cal, BlackoutConfig()).check("XAUUSD", NOW)
    assert d.blocked and d.reason == "NEWS_SERVICE_FAILED"
    assert not NewsFilter(cal, BlackoutConfig(failure_policy=NewsFailurePolicy.ALLOW)).check("XAUUSD", NOW).blocked


def test_unconfigured_and_stale_block():
    cal = EconomicCalendar(provider=None)
    assert NewsFilter(cal, BlackoutConfig()).check("US30", NOW).reason == "NEWS_SERVICE_NOT_CONFIGURED"
    cal.load_events([], NOW - timedelta(hours=3))
    assert cal.status(NOW) is CalendarStatus.STALE


def _client(payload):
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))


def test_fmp_parsing():
    p = FmpProvider("k", client=_client([{"event": "CPI", "date": "2026-10-06 12:30:00", "country": "US",
                                           "currency": "USD", "impact": "High", "previous": 0.2, "estimate": 0.3,
                                           "actual": None}]))
    out = p.fetch(NOW, NOW + timedelta(days=1))
    assert out[0].importance == "HIGH" and out[0].currency == "USD"
    assert out[0].scheduled_at == datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
    assert out[0].forecast == "0.3" and out[0].actual == ""


def test_finnhub_parsing_maps_country_to_currency():
    p = FinnhubProvider("k", client=_client({"economicCalendar": [
        {"event": "GDP", "time": "2026-10-06 12:30:00", "country": "US", "impact": "medium", "prev": 1, "estimate": 2,
         "actual": 3}]}))
    e = p.fetch(NOW, NOW)[0]
    assert e.currency == "USD" and e.importance == "MEDIUM" and e.actual == "3"


def test_tradingeconomics_refuses_guest_key():
    p = TradingEconomicsProvider("guest:guest", client=_client([]))
    with pytest.raises(NewsProviderError):
        p.fetch(NOW, NOW)


def test_provider_requires_key_and_handles_http_errors():
    with pytest.raises(NewsProviderError):
        FmpProvider("")
    bad = FmpProvider("k", client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(NewsProviderError):
        bad.fetch(NOW, NOW)
    assert build_provider("none", "") is None
