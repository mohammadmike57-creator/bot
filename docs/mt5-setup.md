# MT5 setup: Windows ARM (Parallels) · MT5 · CFI · PRO_MAX_EA

MetaEditor (the MQL5 compiler) and the full MT5 terminal are Windows programs. On an M2 Mac the most
reliable setup is **Windows 11 ARM in Parallels Desktop**, where MT5 runs under Windows' built-in x64
emulation. (MetaQuotes also ships a macOS package based on Wine; it is not covered here.)

## 1. Install Windows 11 ARM in Parallels

1. Install **Parallels Desktop** for Mac (Apple Silicon build) from parallels.com.
2. Open Parallels → **File › New** → **Get Windows 11 from Microsoft** → *Continue*. Parallels downloads
   and installs Windows 11 on ARM automatically. Activate Windows with your licence.
3. Recommended VM settings (*Configure › Hardware*): 4 CPUs, 6–8 GB RAM.
4. *Configure › Options › Optimization*: disable "Pause Windows when possible" so MT5 keeps running.
5. In Windows: **Settings › Time & language › Date & time** → *Set time automatically* **On**, then
   *Sync now*. Request signatures fail if the clock is more than 30 s off.
6. Keep Windows from sleeping: *Settings › System › Power* → Screen and sleep → **Never** while trading.

Networking: Parallels' default *Shared Network* lets Windows reach the Mac. The Mac's address as seen from
Windows is usually `10.211.55.2` (check on the Mac with `ifconfig bridge100` → `inet`).

## 2. Install MT5 for CFI and log in

1. In Windows, download the **MetaTrader 5** installer from CFI's website or client portal (use CFI's
   own download so the CFI servers are pre-configured). Install with defaults.
2. Start MT5 → **File › Login to Trade Account**.
3. Enter the **login, password and server exactly as shown in your CFI account email / client portal**
   (the server name is CFI-specific; do not guess it). Tick *Save password* only on a machine you trust.
4. Start with a **demo** account. Check: the title bar / *Navigator › Accounts* shows the demo account and
   the status bar shows a live connection (not "No connection").
5. Confirm the account is **hedging**: in *Navigator › Accounts* hedging accounts are labelled "Hedge";
   the EA also checks at start-up and logs an error (and refuses orders) if the account is netting.

## 3. Find your broker's exact symbol names

Open **View › Market Watch** → right-click → **Symbols**. Search for gold (e.g. names containing `XAU`
or `GOLD`), WTI/crude oil (`OIL`, `WTI`, `USOIL`) and the Dow (`US30`, `DJ30`, `WS30`). Note the **exact**
names including any suffix (e.g. `.cash`, `.r`, `-ECN`). Click *Show* for each so they appear in Market
Watch. Open **Specification** (right-click → *Specification*) to see contract size, minimum volume, step,
tick size/value — the EA reports all of these to the backend automatically; nothing is hardcoded.

If the EA is started with wrong names it stops and logs a list of candidate symbols it found.

## 4. Copy the EA into MT5

1. In MT5: **File › Open Data Folder**. A Windows Explorer window opens at the terminal's data folder.
2. Go to `MQL5\Experts\` and create a folder `PRO_MAX_EA`.
3. Copy from this repository (e.g. via the Parallels shared Mac folder `\\Mac\Home\...`):
   - `mt5_ea\PRO_MAX_EA.mq5` → `MQL5\Experts\PRO_MAX_EA\PRO_MAX_EA.mq5`
   - `mt5_ea\Include\*.mqh` → `MQL5\Experts\PRO_MAX_EA\Include\`
   - `mt5_ea\Scripts\ExportHistory.mq5` → `MQL5\Scripts\ExportHistory.mq5`

Resulting layout:

```
MQL5\Experts\PRO_MAX_EA\PRO_MAX_EA.mq5
MQL5\Experts\PRO_MAX_EA\Include\Config.mqh  (and the other .mqh files)
MQL5\Scripts\ExportHistory.mq5
```

## 5. Compile the EA in MetaEditor

1. In MT5 press **F4** (or *Tools › MetaQuotes Language Editor*) to open MetaEditor.
2. In MetaEditor's Navigator open `Experts › PRO_MAX_EA › PRO_MAX_EA.mq5`.
3. Press **F7** (*Compile*). The *Errors* tab must show **0 errors**. Warnings should be reviewed.
   A `PRO_MAX_EA.ex5` appears next to the source.
4. Also compile `Scripts › ExportHistory.mq5`.

> The EA was written against the documented MQL5 API but this repository's CI cannot run MetaEditor.
> If MetaEditor reports an error (e.g. a changed function signature in your terminal build), fix it
> before running — and update MT5 to the latest build first (*Help › Check for updates*).

## 6. Enable algorithmic trading and WebRequest

1. **Tools › Options › Expert Advisors**:
   - tick **Allow algorithmic trading**;
   - tick **Allow WebRequest for listed URL** and add your backend's base URL, e.g.
     `https://trading-ai-api.onrender.com` (Render) or `http://10.211.55.2:8000` (backend on the Mac);
   - leave *Allow DLL imports* **off** (not needed).
2. Press the **Algo Trading** button in the toolbar so it is green.

For a backend on the Mac, start uvicorn listening on all interfaces so the VM can reach it:
`uvicorn app.main:app --host 0.0.0.0 --port 8000`, and check from Windows' browser that
`http://10.211.55.2:8000/health` answers. (If your terminal build refuses a non-standard port, use the
deployed HTTPS URL instead — see troubleshooting.)

## 7. Attach the EA (PAPER mode)

1. Open **one** chart (any of your symbols, e.g. gold M15). The EA manages all configured symbols from
   this single chart; do not attach it to several charts.
2. Drag `PRO_MAX_EA` from *Navigator › Expert Advisors* onto the chart.
3. *Common* tab: tick **Allow Algo Trading**.
4. *Inputs* tab — minimum configuration:

| Input | Value |
|---|---|
| `InpTradingMode` | **PAPER** (default) |
| `InpBackendUrl` | same URL you whitelisted, no trailing slash |
| `InpApiKeyId` | `EA_API_KEY_ID` from the backend `.env` / Render |
| `InpHmacSecret` | `EA_HMAC_SECRET` from the backend `.env` / Render |
| `InpSymbols` | `XAUUSD=<gold name>,OIL=<oil name>,US30=<dow name>` using the exact names from step 3 |
| `InpSignalTimeframe` | `M15` (default) |

   The backend must map the same names: `XAUUSD_SYMBOL`, `OIL_SYMBOL`, `US30_SYMBOL` in `.env` / Render.
5. Click **OK**. The chart shows a status panel (top-left) and an **EMERGENCY STOP** button (top-right).

## 8. Confirm heartbeat, account data and PAPER mode

- **Experts** tab (Toolbox): `PRO_MAX_EA 1.0.0 starting, mode=PAPER`, the symbol mapping lines with
  contract size and minimum volume, and `PAPER MODE: ... will NOT send orders`.
- Chart panel: `MODE: PAPER (no orders are sent)` and `BACKEND: ONLINE`.
- Dashboard: banner **PAPER MODE**, *EA heartbeat* **ONLINE**, *MT5 connection* **CONNECTED**, quotes in
  the Market table, symbol specifications under *Risk & Settings* (all reported by MT5).
- After the next candle closes, the *Logs* page shows one decision per symbol (most will be
  `NO_TRADE` with a reason — that is expected).
- In PAPER mode any approved signal appears under *Trades* with source PAPER; the MT5 *Trade* tab stays
  empty because no orders are sent.

## 9. Local emergency stop and resets

- **EMERGENCY STOP** button on the chart: activates the local kill switch (persisted in terminal
  global variables, survives restarts). With `InpCloseOnKillSwitch=true` it also closes the EA's own
  positions.
- To clear it: re-open the EA inputs, set `InpLocalKillSwitch=false` **and** `InpManualResetKillSwitch=true`,
  click OK, then set `InpManualResetKillSwitch` back to `false`.
- Drawdown / daily-loss locks are reset the same way (`InpManualResetDrawdown`, `InpManualResetDailyLoss`).
  Daily loss also resets at the 17:00 New York rollover; drawdown never resets by itself.

## 10. Exporting history for backtests

Drag **ExportHistory** from *Navigator › Scripts* onto a chart, choose symbol, timeframe, date range and
how your broker's clock relates to UTC (`TZ_NY_CLOSE` is common for CFD brokers — confirm with CFI or
by comparing a known news-candle time). The CSV is written to `MQL5\Files\<symbol>_<tf>.csv` with UTC
timestamps; copy it to the Mac's `data/` folder. See [backtesting.md](backtesting.md).

## 11. Demo testing (DEMO mode)

Only after PAPER mode has run cleanly (see [live-checklist.md](live-checklist.md)):

1. Backend: `TRADING_MODE=DEMO` (restart / redeploy).
2. EA input `InpTradingMode=DEMO`. The EA **refuses** to send orders if the logged-in account is not a demo.
3. Watch the first trades: the MT5 *Trade* tab shows positions with comment `PMX|<id>` and magic
   numbers like `1000102`; the dashboard (*MT5* view) shows the same tickets with strategy, risk and R.
