"""Shared CLI helpers for the research scripts (run from the repository root)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.backtesting.data_loader import load_specs_json, parse_data_args, specs_from_database  # noqa: E402
from app.backtesting.engine import BacktestConfig  # noqa: E402
from app.backtesting.transaction_costs import CostAssumptions  # noqa: E402
from app.news.news_provider import CsvProvider  # noqa: E402
from app.risk.limits import RiskLimits  # noqa: E402

DISCLAIMER = (
    "Results are historical simulations under the stated cost assumptions. They are not a forecast and do not\n"
    "establish that the strategy is profitable. Validate out-of-sample and on a DEMO account before any live use."
)


def common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", nargs="+", required=True, help="LOGICAL=path.csv (exported by ExportHistory.mq5)")
    p.add_argument("--specs", help="symbol specs JSON (see scripts/specs.example.json)")
    p.add_argument("--specs-from-db", help="read specs reported by the EA from this DATABASE_URL")
    p.add_argument("--equity", type=float, default=100.0)
    p.add_argument("--timeframe-minutes", type=int, default=15)
    p.add_argument("--commission", type=float, default=0.0, help="round-trip commission per lot (account ccy)")
    p.add_argument("--slippage-points", type=float, default=2.0)
    p.add_argument("--spread-multiplier", type=float, default=1.0, help="stress test, e.g. 1.5")
    p.add_argument("--fallback-spread-points", type=float, default=30.0)
    p.add_argument("--risk-per-trade", type=float, default=0.005)
    p.add_argument("--news-csv", help="historical economic calendar CSV; omit = no news filter (reported)")
    p.add_argument("--no-market-hours", action="store_true")


def load_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], BacktestConfig]:
    data = parse_data_args(args.data)
    if args.specs:
        specs = load_specs_json(args.specs)
    elif args.specs_from_db:
        specs = specs_from_database(args.specs_from_db)
    else:
        raise SystemExit("symbol specs required: --specs file.json or --specs-from-db URL (never assumed)")
    missing = set(data) - set(specs)
    if missing:
        raise SystemExit(f"no symbol spec for {sorted(missing)}")
    news = None
    if args.news_csv:
        news = CsvProvider(args.news_csv).fetch(datetime(1970, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))
    limits = RiskLimits(risk_per_trade=args.risk_per_trade, max_symbol_risk=max(0.015, args.risk_per_trade),
                        commission_per_lot=args.commission)
    cfg = BacktestConfig(
        initial_equity=args.equity, timeframe_minutes=args.timeframe_minutes, limits=limits,
        costs=CostAssumptions(commission_per_lot=args.commission, slippage_points=args.slippage_points,
                              fallback_spread_points=args.fallback_spread_points,
                              spread_multiplier=args.spread_multiplier),
        news_events=news, apply_market_hours=not args.no_market_hours,
    )
    return data, specs, cfg


def write_report(name: str, payload: dict[str, Any]) -> Path:
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    path = reports / f"{name}_{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
