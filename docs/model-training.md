# Model training

## Pipeline

1. **Features** (`app/ai/features.py`, all causal): returns (1/5/20), ATR %, ATR percentile, RSI, distance
   to EMA20/50 in ATR, EMA slope, EMA fast–slow, MACD histogram, ADX, DI difference, volatility and
   volatility ratio, candle body/wick ratios, range/ATR, distance to 20-bar high/low, momentum, spread/ATR,
   hour (sin/cos), day of week, session flags, minutes to next relevant news event (when supplied),
   rolling correlation to a reference instrument (when supplied).
2. **Labels** (`app/ai/labeling.py`): triple barrier — BUY if +k·ATR is touched before −k·ATR within
   *h* bars, SELL for the mirror, HOLD otherwise (or both inside one bar). The last *h* rows are dropped.
3. **Model** (`app/ai/signal_model.py`): gradient-boosted trees (`HistGradientBoostingClassifier`; LightGBM
   optional) with sigmoid calibration on forward-chaining splits. Outputs P(BUY), P(SELL), P(HOLD).
4. **Decision** (`app/ai/probability.py`): the model only *confirms* a strategy's direction, and only if
   the probability ≥ `CONFIDENCE_THRESHOLD`, beats the opposite side by `MIN_PROBABILITY_EDGE` and HOLD is
   below `MAX_HOLD_PROBABILITY`. Then the risk engine decides.
5. **Regime** (`app/ai/regime_model.py`): transparent rule-based classifier (ADX, EMA structure, ATR
   percentile, bar range) → TREND_UP/DOWN, RANGE, HIGH/LOW_VOLATILITY, UNSTABLE (= no trade).

No sequence model (LSTM) is included; it would plug into the same `fit/predict_proba` interface.

## Train and register

```bash
source .venv/bin/activate
python scripts/train_models.py --data XAUUSD=data/XAUUSD_M15.csv OIL=data/OIL_M15.csv US30=data/US30_M15.csv \
  --specs data/specs.json --equity 1000 --commission 7 --version v2026-10-a
```

The script runs a walk-forward (fresh model per fold), trains the final model on everything except the
last `--holdout-bars`, back-tests the holdout, and registers the model as **CANDIDATE** with: id, version,
training date, dataset period, features, hyper-parameters, walk-forward OOS and holdout metrics.
`--activate` promotes it to ACTIVE only if `--min-oos-trades` and `--min-oos-pf` are met on both the
walk-forward and holdout results; a model without OOS results can never be activated.

Then reload in the running backend: dashboard *Model › Reload active model* or `POST /v1/model/reload`.
Pin a specific version with `MODEL_VERSION`.

## Integrity and safety

- Artifacts are joblib files; the registry stores their SHA-256 and refuses to load a file whose hash
  differs or that lives outside `MODELS_DIR`. Only load artifacts you trained yourself.
- `ModelHealth` monitors error rate, feature drift (median PSI vs. training deciles), recent profit
  factor of model-confirmed trades and mean confidence. Breaching `MODEL_MAX_DRIFT_PSI`,
  `MODEL_MIN_RECENT_PROFIT_FACTOR` (after `MODEL_MIN_RECENT_TRADES`) or `MODEL_MAX_ERROR_RATE` moves the
  system to RULE_BASED (PAPER/DEMO if allowed) or SAFE (no trades). LIVE requires a healthy model unless
  `ALLOW_RULE_BASED_IN_LIVE=true`.

## Reinforcement learning

Not implemented. If added later it must run offline (strategy selection, parameter research) and may
only *recommend* configurations; it must never get write access to risk limits, which live outside any
model in `RiskLimits` and the EA inputs.
