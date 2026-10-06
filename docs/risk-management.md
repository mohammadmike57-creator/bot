# Risk management

The risk engine (`server/app/risk/risk_engine.py`) has final authority over every new trade. The EA
enforces its own copy of the limits (`Include/RiskManager.mqh`, `PortfolioManager.mqh`) and cannot be
overridden by the backend. Strategies and the ML model can only **reduce** risk.

## Limits (defaults, all configurable)

| Setting | Default | Meaning |
|---|---|---|
| `RISK_PER_TRADE` | 0.5% | money lost if the stop is hit, as % of equity |
| `MAX_DAILY_LOSS` | 2% | loss vs. equity at the 17:00 NY day start → no new trades until the next day |
| `MAX_DRAWDOWN` | 8% | from peak equity → no new trades until **manual** reset |
| `MAX_SYMBOL_RISK` | 1.5% | open risk on one symbol |
| `MAX_STRATEGY_RISK` | 2% | open risk of one strategy |
| `MAX_PORTFOLIO_RISK` | 3% | open risk of all positions |
| `MAX_CORRELATED_EXPOSURE` | 2% | open risk moving together (|ρ| ≥ `CORRELATION_THRESHOLD` in the held direction) |
| `MAX_MARGIN_USAGE` | 40% | margin used / equity after the trade |
| `MAX_POSITIONS` / per symbol | 8 / 3 | secondary limits only |

"Open risk" of a position = money lost if its stop is hit **from the current price**; a stop already in
profit contributes 0. A position without a stop blocks all new risk.

The new trade's risk money is the *minimum* of: per-trade budget, remaining symbol / strategy /
portfolio / correlated budgets and the remaining daily-loss allowance minus existing open risk. So a
trade can be refused while position slots are free.

## Position sizing

```
risk_money   = equity × risk_fraction × risk_multiplier × strategy_scale
loss_per_lot = |entry − stop| / tick_size × tick_value(loss) + commission_per_lot
volume       = floor(risk_money / loss_per_lot / volume_step) × volume_step   (≤ volume_max, ≤ margin room)
volume < volume_min  ⇒  REJECT "Minimum broker volume exceeds allowed account risk."
```

Tick size/value, contract size and volume limits come from MT5 (`SYMBOL_TRADE_TICK_VALUE_LOSS` when
available). The EA re-computes the size with `OrderCalcProfit` (exact, including currency conversion)
and uses the smaller of its own and the backend's volume.

## Equity-based scaling

`EQUITY_SCALING_STEPS=1.0:1.0,1.25:1.05,1.5:1.10,2.0:1.20` maps equity growth (equity ÷ initial
equity) to a multiplier, clamped to `[RISK_MULTIPLIER_MIN, RISK_MULTIPLIER_MAX]` = [0.75, 1.25].
Below the starting equity the multiplier is at most 1.0; in a drawdown deeper than half the limit it is
forced to 0.75. Scaling never increases risk to recover losses.

## Stops, targets and trade management

- Every trade has a stop. Stops are the wider of an ATR stop (`SL_ATR_MULTIPLIER`, oil uses ≥ 2×ATR) and
  a structural stop (swing / range edge + buffer). Max 20% of price, ≥ broker stop level.
- Targets: TP1 = 1R (partial close of `TP1_CLOSE_FRACTION`), TP2 = 2R as the broker TP; range strategies
  target the range midpoint and are skipped if that is < 1R. Minimum reward:risk 1.0.
- Management (identical in backend paper broker, backtester and EA):
  - +0.8R → stop tightened to −0.5R
  - +1.0R → break-even + `BE_OFFSET_R` (covers costs); partial close at TP1
  - ≥ +1.0R → ATR trailing (`TRAIL_ATR_MULTIPLIER`)
  - a stop only ever moves in the protective direction; candidates closer than the broker's stop/freeze
    level are skipped; the most protective valid one is used.
  - EA only: an own position without a stop gets an emergency stop (`InpEmergencySlAtr`×ATR) or is closed.

## Hedging

The account is hedging, so BUY and SELL positions on one symbol can coexist and each is managed
individually (own SL/TP/BE/trail, magic `symbolBase*100+strategyCode`, comment `PMX|<signal>`).
Opening the opposite direction on a symbol with an open position is **rejected** unless the proposal
carries an explicit purpose (`EXPOSURE_REDUCTION`, `EVENT_RISK`, `STRATEGY_TRANSITION`, `PORTFOLIO_HEDGE`).
The shipped strategies never set one, so they never hedge themselves.

## Prohibited

Martingale, loss-doubling (`volume_multiplier_after_loss > 1`), grids, averaging down (adding to a
losing position at a worse price) and risk scales > 1 are rejected by the engine.

## Daily loss / drawdown / kill switch

- State is persisted (database `risk_state`, EA terminal global variables). Restarting the EA, the
  terminal or the backend does **not** reset anything.
- Daily-loss lock clears only at the next trading day (17:00 New York) or via manual reset.
- Drawdown lock clears only via manual reset (dashboard *RESET DRAWDOWN LOCK*, typed confirmation, or EA
  input `InpManualResetDrawdown`). Resets are recorded as risk events.
- `EMERGENCY_CLOSE_POLICY=CLOSE_ALL` additionally asks the EA to flatten its own positions when a lock or
  the kill switch triggers; default `KEEP_POSITIONS` keeps managing them with their stops.
- Kill switch: `POST /v1/kill-switch` (dashboard EMERGENCY STOP) → no new signals or orders, SAFE MODE,
  optional close; cleared only by `POST /v1/reset-risk` with `confirm: "RESET"`. The EA has its own chart
  button and input.

## The $100 account

With $100 equity and 0.5% risk the budget is **$0.50**. Typical CFD minimums (0.01 lot of gold ≈ $1 per
$1 move; ATR-based M15 stops of several dollars) mean the minimum position often risks **several
dollars** — 5–10× the budget. The system then rejects the trade. It will not trade more than allowed.
Options, in order of preference: trade with more capital; accept a higher `RISK_PER_TRADE` *consciously*
(e.g. 1–2%, knowing it raises the chance of ruin); use instruments/brokers with smaller minimum
contracts. Expect costs (spread/commission) to be a large fraction of each trade's risk at this size.

## Correlation

`CorrelationMatrix` computes rolling correlations of log returns from the bars the EA sends. Pairs with
insufficient overlapping data use a conservative fallback (0.7, i.e. treated as correlated). Positions
count toward correlated exposure when `ρ × dir_new × dir_open ≥ threshold` — long gold + long US30 with
ρ=0.8 add up; long gold + short US30 offset.
