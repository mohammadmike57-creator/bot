#!/usr/bin/env python3
"""Train the signal model with walk-forward validation and register it (as CANDIDATE).

  DATABASE_URL=... python scripts/train_models.py --data XAUUSD=data/XAUUSD_M15.csv OIL=data/OIL_M15.csv \
      --specs data/specs.json --version v1 [--activate]

Steps:
  1. Walk-forward: a fresh model per fold, trained only on past data, evaluated out-of-sample.
  2. Final model trained on all but the last ``--holdout-bars`` bars; the holdout is a last OOS check.
  3. Registered with full metadata (dataset period, features, hyper-parameters, validation and OOS
     results). ``--activate`` only succeeds if OOS criteria are met; otherwise it stays CANDIDATE.
Then call POST /v1/model/reload (dashboard: Model > Reload).
"""

from __future__ import annotations

import argparse

import pandas as pd

from _common import DISCLAIMER, common_args, load_inputs, write_report
from app.ai.features import FEATURE_COLUMNS, compute_features
from app.ai.labeling import triple_barrier_labels
from app.ai.model_registry import ModelRegistry
from app.ai.signal_model import SignalModel
from app.backtesting.engine import BacktestEngine
from app.backtesting.walk_forward import WalkForwardConfig, run_walk_forward
from app.config.settings import get_settings
from app.database.database import Database


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    common_args(p)
    p.add_argument("--version", required=True)
    p.add_argument("--holdout-bars", type=int, default=1500)
    p.add_argument("--horizon", type=int, default=24)
    p.add_argument("--barrier-atr", type=float, default=1.5)
    p.add_argument("--model-type", choices=["hgb", "lightgbm"], default="hgb")
    p.add_argument("--activate", action="store_true")
    p.add_argument("--min-oos-trades", type=int, default=50)
    p.add_argument("--min-oos-pf", type=float, default=1.1)
    args = p.parse_args()
    data, specs, cfg = load_inputs(args)

    wf = WalkForwardConfig(label_horizon=args.horizon, embargo_bars=args.horizon, label_barrier_atr=args.barrier_atr)
    wf_res = run_walk_forward(data, specs, cfg, wf, use_model=True)
    print("walk-forward OOS:", {k: v for k, v in wf_res.oos_metrics.items() if not isinstance(v, dict)})

    feats = {s: compute_features(df) for s, df in data.items()}
    xs, ys = [], []
    holdout_start = None
    for f in feats.values():
        cut = len(f) - args.holdout_bars - args.horizon
        y = triple_barrier_labels(f, args.barrier_atr, args.horizon)
        xs.append(f.iloc[:cut][FEATURE_COLUMNS])
        ys.append(y.iloc[:cut])
        t = f["time"].iloc[len(f) - args.holdout_bars]
        holdout_start = t if holdout_start is None else max(holdout_start, t)
    model = SignalModel(model_type=args.model_type).fit(pd.concat(xs), pd.concat(ys))
    probs = {s: model.predict_proba(f[FEATURE_COLUMNS]) for s, f in feats.items()}
    for s, f in feats.items():
        probs[s][f[FEATURE_COLUMNS].isna().any(axis=1).to_numpy()] = float("nan")
    holdout = BacktestEngine(cfg, specs, data, probs, feats).run(holdout_start.to_pydatetime())
    oos = {k: v for k, v in holdout.metrics.items() if not isinstance(v, dict)}
    wf_oos = {k: v for k, v in wf_res.oos_metrics.items() if not isinstance(v, dict)}
    print("holdout OOS:", oos)

    settings = get_settings()  # DATABASE_URL / MODELS_DIR from the environment or the repo-root .env
    db = Database(settings.database_url)
    if db.url.startswith("sqlite"):
        db.create_all()
    registry = ModelRegistry(db, settings.models_dir)
    first = min(f["time"].iloc[0] for f in feats.values()).to_pydatetime()
    meta = registry.register(
        model, version=args.version, dataset_start=first, dataset_end=holdout_start.to_pydatetime(),
        features=model.features, hyperparameters={**model.params, "model_type": args.model_type,
                                                  "horizon": args.horizon, "barrier_atr": args.barrier_atr},
        validation_metrics={"walk_forward_oos": wf_oos}, oos_metrics={"holdout": oos},
    )
    print(f"registered {meta.model_id} version={meta.version} status=CANDIDATE")
    ok = (oos.get("trades", 0) >= args.min_oos_trades and (oos.get("profit_factor") or 0) >= args.min_oos_pf
          and (wf_oos.get("profit_factor") or 0) >= args.min_oos_pf)
    if args.activate:
        if ok:
            registry.set_status(meta.model_id, "ACTIVE")
            print("ACTIVATED (call POST /v1/model/reload or use the dashboard)")
        else:
            print(f"NOT activated: OOS criteria not met (trades>={args.min_oos_trades}, PF>={args.min_oos_pf})")
    write_report(f"train_{args.version}", {"model_id": meta.model_id, "walk_forward": wf_res.summary(),
                                           "holdout": oos, "activation_criteria_met": ok})
    print(DISCLAIMER)


if __name__ == "__main__":
    main()
