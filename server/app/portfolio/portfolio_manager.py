"""Builds the current portfolio picture from persisted positions and specs."""

from __future__ import annotations

from dataclasses import dataclass

from app.config.symbols import SymbolResolver
from app.database import repositories as repo
from app.database.database import Database
from app.database.models import Position, SymbolSpecRecord
from app.domain import AccountState, Direction, OpenPosition, SymbolSpec
from app.portfolio.correlation import CorrelationMatrix
from app.portfolio.exposure import ExposureReport, aggregate


def spec_from_record(r: SymbolSpecRecord) -> SymbolSpec:
    return SymbolSpec(
        symbol=r.logical_symbol or r.broker_symbol,
        digits=r.digits,
        point=r.point,
        tick_size=r.tick_size,
        tick_value=r.tick_value,
        tick_value_loss=r.tick_value_loss,
        contract_size=r.contract_size,
        volume_min=r.volume_min,
        volume_max=r.volume_max,
        volume_step=r.volume_step,
        stops_level=r.stops_level,
        freeze_level=r.freeze_level,
        margin_per_lot=r.margin_per_lot,
        trade_allowed=r.trade_mode in ("FULL", "") ,
    )


def position_from_record(p: Position) -> OpenPosition:
    return OpenPosition(
        ticket=p.ticket,
        symbol=p.logical_symbol or p.symbol,
        direction=Direction(p.direction),
        volume=p.volume,
        entry_price=p.entry_price,
        sl=p.sl,
        tp=p.tp,
        current_price=p.current_price,
        strategy=p.strategy,
        magic=p.magic,
        profit=p.profit,
        opened_at=p.opened_at,
        hedge_purpose=p.hedge_purpose,
        own=p.magic != 0 or p.source == "PAPER",
    )


@dataclass
class PortfolioSnapshot:
    account: AccountState
    positions: list[OpenPosition]
    specs: dict[str, SymbolSpec]
    exposure: ExposureReport


class PortfolioManager:
    def __init__(self, db: Database, resolver: SymbolResolver, correlation: CorrelationMatrix):
        self.db = db
        self.resolver = resolver
        self.correlation = correlation

    def snapshot(self, source: str) -> PortfolioSnapshot:
        with self.db.session() as s:
            account = repo.get_account_state(s, source)
            positions = [position_from_record(p) for p in repo.open_positions(s, source)]
            specs = {spec.symbol: spec for spec in (spec_from_record(r) for r in repo.symbol_specs(s))}
        return PortfolioSnapshot(account, positions, specs, aggregate(positions, specs, account.equity))
