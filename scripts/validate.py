#!/usr/bin/env python3
"""Walk-forward validation (train -> validate -> out-of-sample test, rolling).

  python scripts/validate.py --data XAUUSD=data/XAUUSD_M15.csv --specs data/specs.json --mode rules
  python scripts/validate.py --data XAUUSD=data/XAUUSD_M15.csv --specs data/specs.json --mode model
"""

from __future__ import annotations

import argparse

from _common import DISCLAIMER, ROOT, common_args, load_inputs, write_report
from app.backtesting.walk_forward import WalkForwardConfig, run_walk_forward
from app.optimization.optimizer import Optimizer
from app.optimization.parameter_space import default_space


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(p)
    p.add_argument("--mode", choices=["rules", "rules-optimized", "model"], default="rules")
    p.add_argument("--train-bars", type=int, default=6000)
    p.add_argument("--validate-bars", type=int, default=1500)
    p.add_argument("--test-bars", type=int, default=1500)
    p.add_argument("--embargo-bars", type=int, default=24)
    p.add_argument("--trials", type=int, default=20, help="optimizer trials per fold (rules-optimized)")
    args = p.parse_args()
    data, specs, cfg = load_inputs(args)
    wf = WalkForwardConfig(train_bars=args.train_bars, validate_bars=args.validate_bars, test_bars=args.test_bars,
                           embargo_bars=args.embargo_bars, label_horizon=args.embargo_bars)
    optimize = None
    if args.mode == "rules-optimized":
        def optimize(base, start, end):  # type: ignore[no-untyped-def]
            return Optimizer(data, specs, base, default_space()).best_config(start, end, args.trials)
    res = run_walk_forward(data, specs, cfg, wf, use_model=args.mode == "model", optimize_rules=optimize)
    summary = res.summary()
    print(DISCLAIMER, "\n")
    print(f"folds: {summary['folds']}")
    for f in summary["per_fold"]:
        print(f"  fold {f['fold']}: test {f['test_start'][:10]}..{f['test_end'][:10]} {f['test']} chosen={f['chosen']}")
    print("\nOUT-OF-SAMPLE (all folds concatenated):")
    for k, v in summary["oos"].items():
        print(f"{k:>22}: {v}")
    path = write_report(f"walkforward_{args.mode}", {"summary": summary, "oos_metrics": res.oos_metrics})
    print(f"\nreport: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
