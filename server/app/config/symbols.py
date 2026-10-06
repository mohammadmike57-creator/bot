"""Logical instrument registry.

Logical names (XAUUSD, OIL, US30) are stable inside the platform. The broker
symbol for each logical name comes from configuration and the contract
specification (tick size, tick value, volumes, ...) is always reported by MT5.
Nothing broker-specific is assumed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssetClass(str, Enum):
    METAL = "METAL"
    ENERGY = "ENERGY"
    INDEX = "INDEX"
    FX = "FX"
    OTHER = "OTHER"


@dataclass(frozen=True)
class InstrumentProfile:
    logical: str
    asset_class: AssetClass
    magic: int
    # Currencies whose macro events move this instrument.
    news_currencies: frozenset[str]
    # Event-name keywords that make an event relevant regardless of currency.
    news_keywords: frozenset[str] = field(default_factory=frozenset)


INSTRUMENTS: dict[str, InstrumentProfile] = {
    "XAUUSD": InstrumentProfile(
        logical="XAUUSD",
        asset_class=AssetClass.METAL,
        magic=10001,
        news_currencies=frozenset({"USD"}),
        news_keywords=frozenset({"gold", "fomc", "fed"}),
    ),
    "OIL": InstrumentProfile(
        logical="OIL",
        asset_class=AssetClass.ENERGY,
        magic=10002,
        news_currencies=frozenset({"USD"}),
        news_keywords=frozenset({"crude", "oil", "eia", "api weekly", "opec", "inventor", "gasoline", "distillate"}),
    ),
    "US30": InstrumentProfile(
        logical="US30",
        asset_class=AssetClass.INDEX,
        magic=10003,
        news_currencies=frozenset({"USD"}),
        news_keywords=frozenset({"fomc", "fed", "nonfarm", "cpi"}),
    ),
}

# USD-only events that are clearly irrelevant for crude oil are still treated
# as relevant: USD strength moves oil. Inventory events are NOT relevant to
# gold/US30 unless their currency rule matches (they are USD events, so a
# keyword-only exclusion list narrows that).
ENERGY_ONLY_KEYWORDS = frozenset({"crude", "eia", "api weekly", "inventor", "gasoline", "distillate", "opec", "rig count"})


def profile_for(logical: str) -> InstrumentProfile:
    key = logical.upper()
    if key in INSTRUMENTS:
        return INSTRUMENTS[key]
    return InstrumentProfile(logical=key, asset_class=AssetClass.OTHER, magic=10099, news_currencies=frozenset({"USD"}))


class SymbolResolver:
    """Bidirectional logical <-> broker symbol mapping."""

    def __init__(self, mapping: dict[str, str]):
        self._to_broker = {k.upper(): v for k, v in mapping.items()}
        self._to_logical = {v: k for k, v in self._to_broker.items()}

    def to_broker(self, logical: str) -> str | None:
        return self._to_broker.get(logical.upper())

    def to_logical(self, broker_symbol: str) -> str | None:
        if broker_symbol in self._to_logical:
            return self._to_logical[broker_symbol]
        # Accept logical names directly (e.g. backtests use logical names).
        if broker_symbol.upper() in self._to_broker or broker_symbol.upper() in INSTRUMENTS:
            return broker_symbol.upper()
        return None

    def is_known(self, symbol: str) -> bool:
        return self.to_logical(symbol) is not None

    def broker_symbols(self) -> list[str]:
        return list(self._to_logical)

    def logical_symbols(self) -> list[str]:
        return list(self._to_broker)
