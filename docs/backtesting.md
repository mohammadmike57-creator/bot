# Backtesting, walk-forward and optimisation

## Data

Export history from MT5 with `Scripts/ExportHistory.mq5` (see mt5-setup.md §10). Each CSV has UTC
`time,open,high,low,close,tick_volume,spread` (MT5's per-bar spread in points). Put the files in `data/`
(git-ignored). Symbol specifications must come from your broker: either a JSON file (start from
`scripts/specs.example.json` and copy the real values from MT5 *Specification* or the dashboard), or
`--specs-from-db $DATABASE_URL` to use what the EA reported.

A historical economic calendar is optional: CSV `event,currency,country,importance,scheduled_at[,previous,forecast,actual]`.
Without it the report states **news filter NOT APPLIED**.

## Run a backtest

```bash
source .venv/bin/activate
python scripts/backtest.py --data XAUUSD=data/XAUUSD_M15.csv OIL=data/OIL_M15.csv US30=data/US30_M15.csv \
  --specs data/specs.json --equity 100 --commission 7 --slippage-points 3 --news-csv data/calendar.csv
```

Stress costs with `--spread-multiplier 1.5`. Reports land in `reports/` (JSON + equity CSV).

## What the engine simulates

The backtester re-uses the live components: features, regime classifier, session engine, market hours,
news filter, strategy router, model confirmation, the risk engine (all limits including correlation,
daily loss and drawdown locks, equity scaling) and the same trade management/simulator as PAPER mode.

- Decision at the **close** of bar *t*; fill at the **open** of bar *t+1* (latency) at ask/bid + slippage.
- Spread per bar from the data; commission per lot; swap per night if configured.
- Stop assumed hit **before** target when both are inside one bar; gaps through stops fill at the gap.
- Stop moves computed on bar *t* apply from bar *t+1*.
- Features are causal; a unit test recomputes them on truncated data and asserts equality.
- Daily-loss and drawdown locks behave as live: once the drawdown lock triggers, the run stops trading.

Limitations: bar data cannot show intrabar order of highs/lows (hence the conservative rule); spreads
during news may be wider than the per-bar value; no partial fills or requotes; swap rates are static.

## Walk-forward validation

```bash
python scripts/validate.py --data XAUUSD=data/XAUUSD_M15.csv --specs data/specs.json --mode rules
python scripts/validate.py ... --mode rules-optimized --trials 20   # re-optimise params per fold
python scripts/validate.py ... --mode model                          # fresh ML model per fold
```

Folds are chronological: *train → (embargo) → validate → (embargo) → test*, then roll forward by the
test length. The embargo (≥ label horizon) prevents training labels from overlapping the next segment.
Only the concatenated **test** segments form the out-of-sample result. Risk locks reset per fold
(noted in the report).

## Optimisation

```bash
python scripts/optimize.py --data XAUUSD=data/XAUUSD_M15.csv --specs data/specs.json \
  --is-start 2023-01-01 --is-end 2024-06-30 --oos-end 2024-12-31 --trials 40
```

- Small, bounded parameter spaces (`app/optimization/parameter_space.py`): ATR multiplier, TP R,
  ADX threshold, RSI threshold, trailing distance, BE trigger; `--extended` adds confidence threshold,
  partial fraction, news blackout and risk per trade.
- Score = profit factor (capped), expectancy (R), Sharpe, Sortino, return/drawdown − penalties for
  drawdown > 15%, fewer than `--min-trades` trades.
- The top candidates are re-evaluated at every one-step neighbour; sharp optima are penalised
  (parameter sensitivity). The most **robust** point wins, not the most profitable.
- The best point is then run once on the untouched OOS window; IS→OOS degradation > 50% is flagged
  as likely overfitting.

Never deploy parameters chosen on the same data you report as evidence.
