"""Loading historical bars (from ExportHistory.mq5) and symbol specifications."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.domain import SymbolSpec

SPEC_FIELDS = ("digits", "point", "tick_size", "tick_value", "contract_size", "volume_min", "volume_max",
               "volume_step")


def load_bars_csv(path: str | Path) -> pd.DataFrame:
    """CSV with columns time,open,high,low,close[,tick_volume,spread]; time in UTC.

    Accepts MT5 ``YYYY.MM.DD HH:MM:SS`` or ISO-8601 timestamps.
    """
    df = pd.read_csv(path)
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing columns {sorted(missing)}")
    raw = df["time"].astype(str).str.replace(r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", regex=True)
    df["time"] = pd.to_datetime(raw, utc=True)
    for col in ("tick_volume", "spread"):
        if col not in df.columns:
            df[col] = 0
    df = df[["time", "open", "high", "low", "close", "tick_volume", "spread"]]
    df = df.dropna().sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(df) < 500:
        raise ValueError(f"{path}: only {len(df)} bars - export more history")
    return df


def load_specs_json(path: str | Path) -> dict[str, SymbolSpec]:
    """JSON: {"XAUUSD": {"digits": 2, "point": 0.01, ...}, ...} (values copied from MT5 / the dashboard)."""
    data = json.loads(Path(path).read_text())
    out = {}
    for logical, spec in data.items():
        if logical.startswith("_"):
            continue
        missing = [f for f in SPEC_FIELDS if f not in spec]
        if missing:
            raise ValueError(f"spec {logical}: missing {missing}")
        out[logical.upper()] = SymbolSpec(
            symbol=logical.upper(), digits=int(spec["digits"]), point=float(spec["point"]),
            tick_size=float(spec["tick_size"]), tick_value=float(spec["tick_value"]),
            tick_value_loss=float(spec.get("tick_value_loss", 0.0)), contract_size=float(spec["contract_size"]),
            volume_min=float(spec["volume_min"]), volume_max=float(spec["volume_max"]),
            volume_step=float(spec["volume_step"]), stops_level=int(spec.get("stops_level", 0)),
            freeze_level=int(spec.get("freeze_level", 0)), margin_per_lot=float(spec.get("margin_per_lot", 0.0)),
        )
    return out


def specs_from_database(database_url: str) -> dict[str, SymbolSpec]:
    """Specs the EA reported to the backend (most accurate source)."""
    from app.database import repositories as repo
    from app.database.database import Database
    from app.portfolio.portfolio_manager import spec_from_record

    db = Database(database_url)
    with db.session() as s:
        return {sp.symbol: sp for sp in (spec_from_record(r) for r in repo.symbol_specs(s))}


def parse_data_args(items: list[str]) -> dict[str, pd.DataFrame]:
    """["XAUUSD=data/XAUUSD_M15.csv", ...] -> {logical: bars}"""
    out = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--data expects LOGICAL=path.csv, got {item!r}")
        logical, path = item.split("=", 1)
        out[logical.strip().upper()] = load_bars_csv(path.strip())
    return out
