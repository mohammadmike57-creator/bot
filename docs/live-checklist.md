# Paper → Demo → (optional) Live

Nothing here can be skipped by configuration alone: LIVE needs three explicit backend settings **and**
three explicit EA inputs, and the EA additionally checks the account type.

## 1. Verify PAPER mode

- [ ] `/health` shows `"trading_mode": "PAPER"`; dashboard banner says **PAPER MODE**.
- [ ] EA log shows `mode=PAPER` and `will NOT send orders`; chart panel says `MODE: PAPER`.
- [ ] Dashboard: EA heartbeat ONLINE, MT5 CONNECTED, quotes visible, symbol specs listed (from MT5).
- [ ] News status **OK** with a real provider (otherwise everything is blocked by design).
- [ ] After candles close, *Logs* shows one decision per symbol with reasons.
- [ ] Approved signals appear as PAPER trades; the MT5 Trade tab stays empty.
- [ ] EMERGENCY STOP (dashboard) blocks new signals; reset works only with typed `RESET`.
- [ ] Chart EMERGENCY STOP button activates the local kill switch; it survives an EA restart.
- [ ] Stop the backend for a few minutes: chart shows BACKEND OFFLINE, no new trades, nothing breaks.

## 2. Validate the strategy offline (with real exported data)

- [ ] Backtest with realistic costs (`--commission`, `--slippage-points`, `--spread-multiplier 1.5`) and
      a historical news calendar.
- [ ] Walk-forward (`scripts/validate.py`) — judge the **out-of-sample** result only.
- [ ] Out-of-sample: enough trades (≥ 100), profit factor and expectancy after costs > 0 with margin,
      max drawdown inside your tolerance, results not dependent on one month/symbol.
- [ ] If a model is used: trained with `train_models.py`, activation criteria met, drift monitoring OK.
- [ ] Optimiser IS→OOS degradation is small; parameters are not at the edge of the search space.

## 3. DEMO testing (weeks, not days)

- [ ] Backend `TRADING_MODE=DEMO`, EA `InpTradingMode=DEMO`, logged into a **demo** account.
- [ ] Each order in MT5 has comment `PMX|<signal_id>`, a `1000x..` magic, SL and TP.
- [ ] Multiple positions and BUY+SELL on the same symbol are tracked individually (dashboard MT5 view).
- [ ] Trailing / break-even / partial close happen as configured (EA log lines `TIGHTEN`, `BREAK_EVEN`,
      `PARTIAL_TP1`, `TRAIL_ATR`).
- [ ] Restart the EA and the backend during open trades: no duplicate orders, positions still managed,
      daily loss/drawdown state unchanged.
- [ ] Daily-loss and drawdown locks trigger (test with tightened limits) and require manual reset.
- [ ] Slippage, spreads and fills compared with the backtest assumptions; adjust and re-validate.
- [ ] Demo results over ≥ 4–8 weeks are consistent with the out-of-sample backtest.

## 4. Before enabling LIVE

- [ ] You accept that losses can exceed expectations and that past results do not guarantee anything.
- [ ] Capital is sufficient for the minimum volumes at your risk %, or you have consciously chosen the risk.
- [ ] Notifications (Telegram/e-mail/webhook) tested, including kill switch and EA offline alerts.
- [ ] Render (or host) on an always-on paid plan; database backups enabled; secrets rotated from demo.
- [ ] Windows VM set to never sleep; MT5 auto-starts; clock sync verified.

## 5. Enabling LIVE (explicit, manual)

Backend environment:

```
TRADING_MODE=LIVE
ENABLE_LIVE_TRADING=true
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_LIVE_TRADING_RISKS
```

If any of the three is missing the backend silently stays in PAPER (`effective_trading_mode`).
Dashboard banner then shows **LIVE MODE**.

EA inputs: `InpTradingMode=LIVE`, `InpEnableLiveTrading=true`,
`InpLiveConfirmation=I_UNDERSTAND_LIVE_TRADING_RISKS`. On a real account the EA additionally requires the
backend to answer with `trading_mode=LIVE`.

Start with the smallest risk settings, watch the first trades closely, and keep the EMERGENCY STOP within
reach. Going back to safety is one change: `TRADING_MODE=PAPER` (or the kill switch).
