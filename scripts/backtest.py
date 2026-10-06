#!/usr/bin/env python3
"""Run a portfolio backtest on exported MT5 history.

Example (from the repository root):
  python scripts/backtest.py --data XAUUSD=data/XAUUSD_M15.csv US30=data/US30_M15.csv \
      --specs data/specs.json --equity 100 --commission 7 --news-csv data/calendar.csv
"""

from __future__ import annotations

import argparse
import csv

from _common import DISCLAIMER, ROOT, common_args, load_inputs, parse_time, write_report
from app.backtesting.engine import BacktestEngine


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(p)
    p.add_argument("--start", help="ISO date, UTC")
    p.add_argument("--end", help="ISO date, UTC")
    args = p.parse_args()
    data, specs, cfg = load_inputs(args)
    result = BacktestEngine(cfg, specs, data).run(parse_time(args.start), parse_time(args.end))
    m = result.metrics
    summary = {k: v for k, v in m.items() if not isinstance(v, dict)}
    print(DISCLAIMER, "\n")
    print("ASSUMPTIONS:", result.assumptions)
    for k, v in summary.items():
        print(f"{k:>22}: {v}")
    print("\nDECISIONS:", dict(result.decisions.most_common()))
    report = write_report("backtest", {"assumptions": result.assumptions, "metrics": m,
                                       "trades": [t.__dict__ for t in result.trades]})
    eq_path = report.with_suffix(".equity.csv")
    with eq_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "equity"])
        w.writerows(zip(result.equity_times, result.equity_values, strict=True))
    print(f"\nreport: {report.relative_to(ROOT)}\nequity: {eq_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
