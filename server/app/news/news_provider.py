"""Economic-calendar providers.

Production providers require an API key (``NEWS_API_KEY``). Field mappings
follow each provider's public documentation; verify them with your own key
before relying on them (see docs/troubleshooting.md). No provider fabricates
events: a failed fetch raises, and the calendar then reports FAILED, which
puts trading into SAFE MODE.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

COUNTRY_TO_CURRENCY = {
    "US": "USD", "UNITED STATES": "USD", "EU": "EUR", "EURO AREA": "EUR", "EUROZONE": "EUR", "DE": "EUR",
    "GERMANY": "EUR", "FR": "EUR", "FRANCE": "EUR", "IT": "EUR", "ES": "EUR", "GB": "GBP", "UK": "GBP",
    "UNITED KINGDOM": "GBP", "JP": "JPY", "JAPAN": "JPY", "CN": "CNY", "CHINA": "CNY", "AU": "AUD",
    "AUSTRALIA": "AUD", "CA": "CAD", "CANADA": "CAD", "CH": "CHF", "SWITZERLAND": "CHF", "NZ": "NZD",
    "NEW ZEALAND": "NZD",
}


class NewsProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class EconomicEvent:
    event: str
    currency: str
    country: str
    importance: str  # LOW / MEDIUM / HIGH
    scheduled_at: datetime
    previous: str = ""
    forecast: str = ""
    actual: str = ""
    provider: str = ""


def normalize_importance(value: Any) -> str:
    v = str(value).strip().lower()
    if v in {"3", "high", "h"}:
        return "HIGH"
    if v in {"2", "medium", "moderate", "m"}:
        return "MEDIUM"
    return "LOW"


def _parse_time(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip().replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    dt = datetime.fromisoformat(text)
    # Providers documented to publish UTC without an offset.
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _currency(currency: Any, country: Any) -> str:
    cur = _s(currency).upper()
    if len(cur) == 3:
        return cur
    return COUNTRY_TO_CURRENCY.get(_s(country).upper(), "")


class NewsProvider(ABC):
    name = "base"

    @abstractmethod
    def fetch(self, start: datetime, end: datetime) -> list[EconomicEvent]: ...


class _HttpProvider(NewsProvider):
    default_base = ""

    def __init__(self, api_key: str, base_url: str = "", client: httpx.Client | None = None, timeout: float = 10.0):
        if not api_key:
            raise NewsProviderError(f"{self.name}: NEWS_API_KEY is required")
        self.api_key = api_key
        self.base_url = (base_url or self.default_base).rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            resp = self.client.get(f"{self.base_url}{path}", params=params)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NewsProviderError(f"{self.name}: {exc.__class__.__name__}") from exc


class FmpProvider(_HttpProvider):
    """Financial Modeling Prep economic calendar."""

    name = "fmp"
    default_base = "https://financialmodelingprep.com/api/v3"

    def fetch(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        data = self._get("/economic_calendar", {"from": f"{start:%Y-%m-%d}", "to": f"{end:%Y-%m-%d}",
                                                "apikey": self.api_key})
        if not isinstance(data, list):
            raise NewsProviderError("fmp: unexpected payload")
        return [
            EconomicEvent(
                event=_s(row.get("event")), currency=_currency(row.get("currency"), row.get("country")),
                country=_s(row.get("country")), importance=normalize_importance(row.get("impact")),
                scheduled_at=_parse_time(row["date"]), previous=_s(row.get("previous")),
                forecast=_s(row.get("estimate")), actual=_s(row.get("actual")), provider=self.name,
            )
            for row in data if row.get("date") and row.get("event")
        ]


class FinnhubProvider(_HttpProvider):
    name = "finnhub"
    default_base = "https://finnhub.io/api/v1"

    def fetch(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        data = self._get("/calendar/economic", {"from": f"{start:%Y-%m-%d}", "to": f"{end:%Y-%m-%d}",
                                                "token": self.api_key})
        rows = data.get("economicCalendar") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise NewsProviderError("finnhub: unexpected payload")
        return [
            EconomicEvent(
                event=_s(row.get("event")), currency=_currency(None, row.get("country")),
                country=_s(row.get("country")), importance=normalize_importance(row.get("impact")),
                scheduled_at=_parse_time(row["time"]), previous=_s(row.get("prev")),
                forecast=_s(row.get("estimate")), actual=_s(row.get("actual")), provider=self.name,
            )
            for row in rows if row.get("time") and row.get("event")
        ]


class TradingEconomicsProvider(_HttpProvider):
    """Trading Economics calendar with an authenticated key (not the guest key)."""

    name = "tradingeconomics"
    default_base = "https://api.tradingeconomics.com"

    def fetch(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        if self.api_key.lower().startswith("guest"):
            raise NewsProviderError("tradingeconomics: guest credentials are not allowed")
        data = self._get(f"/calendar/country/all/{start:%Y-%m-%d}/{end:%Y-%m-%d}", {"c": self.api_key, "f": "json"})
        if not isinstance(data, list):
            raise NewsProviderError("tradingeconomics: unexpected payload")
        return [
            EconomicEvent(
                event=_s(row.get("Event")), currency=_currency(row.get("Currency"), row.get("Country")),
                country=_s(row.get("Country")), importance=normalize_importance(row.get("Importance")),
                scheduled_at=_parse_time(row["Date"]), previous=_s(row.get("Previous")),
                forecast=_s(row.get("Forecast") or row.get("TEForecast")), actual=_s(row.get("Actual")),
                provider=self.name,
            )
            for row in data if row.get("Date") and row.get("Event")
        ]


class CsvProvider(NewsProvider):
    """Local CSV (event,currency,country,importance,scheduled_at[,previous,forecast,actual]).

    Intended for backtests with a historical calendar export, or as a manually
    maintained calendar. ``scheduled_at`` must be ISO-8601 UTC.
    """

    name = "csv"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self, start: datetime, end: datetime) -> list[EconomicEvent]:
        if not self.path.exists():
            raise NewsProviderError(f"csv: {self.path} not found")
        out = []
        with self.path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                ts = _parse_time(row["scheduled_at"])
                if start <= ts <= end:
                    out.append(EconomicEvent(
                        event=row["event"], currency=row.get("currency", "").upper(), country=row.get("country", ""),
                        importance=normalize_importance(row.get("importance", "")), scheduled_at=ts,
                        previous=row.get("previous", ""), forecast=row.get("forecast", ""),
                        actual=row.get("actual", ""), provider=self.name,
                    ))
        return out


def build_provider(name: str, api_key: str, base_url: str = "", csv_path: str = "") -> NewsProvider | None:
    name = (name or "none").lower()
    if name == "none":
        return None
    if name == "fmp":
        return FmpProvider(api_key, base_url)
    if name == "finnhub":
        return FinnhubProvider(api_key, base_url)
    if name == "tradingeconomics":
        return TradingEconomicsProvider(api_key, base_url)
    if name == "csv":
        return CsvProvider(csv_path)
    raise NewsProviderError(f"unknown news provider '{name}'")


def default_window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=1), now + timedelta(days=7)
