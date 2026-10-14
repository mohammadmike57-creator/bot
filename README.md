# trading-ai-platform

A risk-first, multi-asset trading platform for a **MetaTrader 5 hedging account** (built for CFI), with:

- a **Python/FastAPI backend** (signals, ML, regime detection, session engine, news filter, portfolio + risk engine, paper broker, PostgreSQL, monitoring, alerts),
- an **MQL5 Expert Advisor** (`PRO_MAX_EA.mq5`) that is the execution authority and independently re-validates every order and manages open positions locally,
- a **React dashboard** (status, controls, emergency stop, trades, performance, risk, news, model, logs),
- a **research toolkit** (bar-by-bar backtester, walk-forward validation, robust optimiser, model training + registry).

> **Default: `TRADING_MODE=PAPER`, `ENABLE_LIVE_TRADING=false`.** Nothing in the code changes these.
> This software does not promise profit. Passing tests and compiling are not evidence of an edge.
> Validate out-of-sample and on a demo account first (see [the live checklist](docs/live-checklist.md)).

**New here? Start with the illustrated step-by-step guide: [docs/setup-guide.html](docs/setup-guide.html)**
(open it in a browser; it covers Render, MT5 and how the bot limits losses, with screenshots).

## How it fits together

```
Mac M2 ─ VS Code ─ backend (FastAPI) ─ PostgreSQL ─ dashboard (React) ─ research scripts
   │
   └─ Parallels: Windows 11 ARM ─ MetaTrader 5 (CFI) ─ PRO_MAX_EA ──HTTPS + HMAC──► backend (local or Render)
```

Once per **closed candle** per symbol the EA sends recent bars + quote; the backend runs the
12-gate decision pipeline (market hours → session → spread → news → account risk → portfolio →
strategy → model confidence → SL → size → margin → execution) and answers `BUY`/`SELL`/`NO_TRADE`
with an explicit reason. In PAPER mode the backend simulates the fill; in DEMO/LIVE the EA repeats
its own checks and only then sends the order. Details: [docs/architecture.md](docs/architecture.md).

## Quick start on the Mac (Apple Silicon)

```bash
git clone <this repo> trading-ai-platform && cd trading-ai-platform
./setup_mac.sh                 # checks Python 3.11+, pip, git, node/npm, docker (optional);
                               # creates .venv, installs deps, creates .env with random secrets,
                               # runs migrations and the test suite
```

Then, in two terminals:

```bash
# 1) backend  ->  http://localhost:8000/health
source .venv/bin/activate
cd server
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 2) dashboard  ->  http://localhost:5173  (sign in with API_KEY from .env)
cd dashboard
npm run dev
```

Optional: PostgreSQL locally instead of SQLite:

```bash
docker compose up -d postgres
# in .env: DATABASE_URL=postgresql://trading:localdev@localhost:5432/trading
cd server && alembic upgrade head
```

Or the whole stack in Docker: `docker compose up --build` (API on :8000).

Verify the running backend (signed request exactly like the EA, PAPER heartbeat only):

```bash
source .venv/bin/activate && set -a && source .env && set +a
BACKEND_URL=http://localhost:8000 python scripts/smoke_test.py
```

## Connect MetaTrader 5

Windows 11 ARM in Parallels → MT5 from CFI → copy `mt5_ea/` into `MQL5/Experts/PRO_MAX_EA/` →
compile `PRO_MAX_EA.mq5` in MetaEditor → allow WebRequest for the backend URL → attach to one chart
with `InpSymbols` set to **your broker's exact symbol names**. Step-by-step: [docs/mt5-setup.md](docs/mt5-setup.md).

## Repository layout

| Path | What |
|---|---|
| `server/app/api` | REST endpoints (`/health`, `/v1/*`) |
| `server/app/services` | signal pipeline, ingest, paper broker, risk state, security |
| `server/app/risk` | position sizing, limits, drawdown/daily loss, exposure, margin, correlation, risk engine |
| `server/app/strategies` | Tokyo gold, London, New York, overlap, oil, US30 + router |
| `server/app/ai` | features, labelling, regime classifier, signal model, calibration, registry, inference/drift |
| `server/app/sessions` | DST-aware sessions, market hours |
| `server/app/news` | authenticated calendar providers, cache, symbol-aware blackout |
| `server/app/execution` | trade management (BE/trailing/partials), simulator, order validation, slippage, lifecycle |
| `server/app/backtesting`, `optimization` | engine, metrics, costs, walk-forward, optimiser, robustness scoring |
| `server/alembic` | database migrations |
| `server/tests` | 195 automated tests |
| `mt5_ea/` | `PRO_MAX_EA.mq5`, `Include/*.mqh`, `Scripts/ExportHistory.mq5` |
| `dashboard/` | React + Vite + TypeScript dashboard |
| `scripts/` | `backtest.py`, `validate.py`, `optimize.py`, `train_models.py`, `smoke_test.py` |
| `docs/` | architecture, MT5 setup, risk, deployment, backtesting, model training, troubleshooting, live checklist |

## Checks

```bash
source .venv/bin/activate
cd server && python -m pytest -q        # unit, API-security, end-to-end paper flow, backtest/WF/ML
cd .. && ruff check . && (cd server && mypy app)
(cd dashboard && npm run build)         # strict TypeScript + production build
```

The EA cannot be compiled outside MetaEditor (Windows). Compile it as described in
[docs/mt5-setup.md](docs/mt5-setup.md#5-compile-the-ea-in-metaeditor) and fix anything MetaEditor reports
before attaching it.

## Important for a $100 account

Risk is sized from equity × risk % ÷ money-lost-per-lot-at-the-stop and rounded **down** to the
broker's volume step. With $100 and 0.5% risk the budget is **$0.50 per trade**. If the broker's minimum
volume at an ATR-based stop risks more than that (very likely for gold, oil and US30 CFDs), the trade is
**rejected** with `Minimum broker volume exceeds allowed account risk.` — risk is never increased to
make a trade possible. Expect many such rejections; that is the system protecting the account.
See [docs/risk-management.md](docs/risk-management.md#the-100-account).

## Documentation

- [Architecture](docs/architecture.md) · [MT5 / Parallels / CFI setup](docs/mt5-setup.md) ·
  [Risk management](docs/risk-management.md) · [Deployment (Render)](docs/deployment.md) ·
  [Backtesting](docs/backtesting.md) · [Model training](docs/model-training.md) ·
  [Troubleshooting](docs/troubleshooting.md) · [Demo testing & LIVE checklist](docs/live-checklist.md)
