#!/usr/bin/env python3
"""Robust parameter optimisation on an in-sample window, then an untouched out-of-sample check.

  python scripts/optimize.py --data XAUUSD=data/XAUUSD_M15.csv --specs data/specs.json \
      --is-start 2023-01-01 --is-end 2024-06-30 --oos-end 2024-12-31 --trials 40

The optimiser scores candidates with a multi-objective robustness score (profit factor,
expectancy, Sharpe/Sortino, return/drawdown) minus penalties for drawdown, few trades and
parameter sensitivity. It never maximises raw historical profit.
"""

from __future__ import annotations

import argparse

from _common import DISCLAIMER, ROOT, common_args, load_inputs, parse_time, write_report
from app.backtesting.engine import BacktestEngine
from app.optimization.optimizer import Optimizer, apply_params
from app.optimization.parameter_space import default_space, extended_space
from app.optimization.validation import degradation, robust_score


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(p)
    p.add_argument("--is-start", required=True)
    p.add_argument("--is-end", required=True)
    p.add_argument("--oos-end", required=True)
    p.add_argument("--trials", type=int, default=30)
    p.add_argument("--extended", action="store_true", help="larger space (needs more data)")
    p.add_argument("--min-trades", type=int, default=30)
    args = p.parse_args()
    data, specs, cfg = load_inputs(args)
    space = extended_space() if args.extended else default_space()
    is_start, is_end, oos_end = parse_time(args.is_start), parse_time(args.is_end), parse_time(args.oos_end)
    assert is_start and is_end and oos_end
    opt = Optimizer(data, specs, cfg, space, min_trades=args.min_trades)
    trials = opt.optimize(is_start, is_end, n_trials=args.trials)
    best = trials[0]
    best_cfg = apply_params(cfg, best.params, space)
    oos = BacktestEngine(best_cfg, specs, data).run(is_end, oos_end).metrics
    deg = degradation(best.metrics, oos)
    print(DISCLAIMER, "\n")
    print("best (robust) params:", best.params)
    print(f"in-sample score {best.score:.3f}  robust {best.robust}  OOS score {robust_score(oos, args.min_trades):.3f}")
    print(f"IS->OOS degradation: {deg:.0%}" + ("  <-- likely overfit, do NOT deploy" if deg > 0.5 else ""))
    for k in ("trades", "profit_factor", "expectancy", "max_drawdown", "sharpe", "net_profit"):
        print(f"{k:>16}: IS {best.metrics.get(k)}  OOS {oos.get(k)}")
    path = write_report("optimize", {"best": best.params, "in_sample": best.metrics, "out_of_sample": oos,
                                     "degradation": deg,
                                     "trials": [{"params": t.params, "score": t.score, "robust": t.robust}
                                                for t in trials]})
    print(f"\nreport: {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
