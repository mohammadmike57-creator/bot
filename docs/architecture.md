# Architecture

## Components

```
                ┌────────────────────────── Mac M2 ──────────────────────────┐
                │ VS Code · Python venv · research scripts · dashboard (Vite) │
                └─────────────┬───────────────────────────────┬──────────────┘
                              │ (dev: localhost)              │
┌──── Parallels: Windows 11 ARM ─────┐          ┌──────── Backend (local or Render) ────────┐
│ MetaTrader 5 (CFI, hedging)        │  HTTPS   │ FastAPI ─ auth (HMAC / JWT) ─ rate limit   │
│ PRO_MAX_EA                         │ ───────► │ signal pipeline ─ strategies ─ ML/regime   │
│  · local risk + validation         │  signed  │ risk engine ─ portfolio ─ news ─ sessions  │
│  · local BE/trailing/partials      │ ◄─────── │ paper broker ─ alerts ─ metrics            │
│  · emergency stop button           │ decision │ PostgreSQL (state) · Redis (optional)      │
└────────────────────────────────────┘          └───────────────▲───────────────────────────┘
                                                                │ JWT
                                                     Dashboard (static site)
```

**MT5 is the execution authority.** The backend never sends orders to the broker; it returns a
decision. The EA re-checks everything locally and can always refuse. Conversely, the backend can only
make the EA *more* conservative (kill switch, pause, safe mode, close request) — never less.

## Request flow (one closed candle, one symbol)

1. `OnTimer` notices a new bar on `InpSignalTimeframe` → `POST /v1/signal` with the last N **closed**
   bars (timestamps converted from broker server time to UTC), bid/ask, spread, broker-session flag, EA mode.
2. Backend pipeline (`server/app/services/signal_service.py`), first failure wins → `NO_TRADE` + reason:
   1. market hours (generic CFD schedule **and** the broker session reported by the EA)
   2. session (DST-aware; dashboard can disable sessions)
   3. spread (absolute points and spread/ATR)
   4. news (symbol-relevant blackout; calendar unavailable ⇒ SAFE MODE)
   5. account risk (kill switch, pause, safe mode, EA heartbeat freshness, daily-loss and drawdown locks)
   6. portfolio pre-check (position slots)
   7. strategy (session × regime × asset-class router; flat is the default)
   8. model confidence (calibrated P(BUY/SELL/HOLD) must confirm the strategy; else rule-based fallback
      in PAPER/DEMO only if enabled, or NO TRADE)
   9–11. risk engine: SL/TP validity, position size from the binding risk budget (trade / symbol /
      strategy / portfolio / remaining daily loss / correlated exposure), margin
   12. execution validation against the broker spec and current quote
3. The approved decision is persisted with an expiry (`SIGNAL_TTL_SECONDS`), the full `KEY=VALUE`
   decision line goes to `trade_logs`, and in PAPER mode the paper broker fills it immediately.
4. DEMO/LIVE: the EA validates mode, account type, permissions, local locks, broker session, local news
   (MT5 calendar), spread, price drift, SL/TP vs stop level, volume (min of backend and local sizing),
   portfolio risk, margin, signal freshness and duplicates — then sends the order with comment
   `PMX|<signal_id>` and a magic number `symbolBase*100 + strategyCode`.
5. Fills/closes are reported via `OnTradeTransaction` → `POST /v1/trade` (idempotent event ids,
   retried from an outbox). Positions are fully re-synced every 30 s (`POST /v1/position`).

## Failure behaviour

| Failure | Behaviour |
|---|---|
| Backend offline | EA: no new trades; open positions still managed locally (emergency SL, BE, partial, trailing) |
| News provider down / not configured / stale | SAFE MODE: no new trades on any symbol |
| Database down | `/health` degraded; requests fail → EA treats backend as offline |
| No healthy model | RULE_BASED (PAPER/DEMO, if allowed) or SAFE (no trades); LIVE requires a model unless explicitly allowed |
| Model drift / poor recent PF / error rate | automatically leaves MODEL mode |
| MT5 disconnected | EA refuses new orders (`TERMINAL_DISCONNECTED`) |
| Abnormal slippage | SAFE MODE until manually cleared |
| Duplicate request / retry / restart | replay protection (request ids), one decision per bar, idempotent trade events, `PMX|<id>` comment check against open positions and 7 days of history |

## Security

- EA → backend: HMAC-SHA256 over `METHOD\nPATH\nTIMESTAMP\nREQUEST_ID\nsha256(body)`, ±30 s timestamp
  window, every request id accepted once (stored in `processed_requests`). The EA self-tests its HMAC
  implementation against RFC 4231 at start-up.
- Dashboard: admin API key → short-lived JWT (HS256). Control endpoints accept only the admin JWT.
- Strict request validation (unknown fields rejected, finite numbers, OHLC sanity), per-IP rate limits
  (stricter on `/v1/auth`), structured JSON logs with request ids, no secrets in the repository, secrets
  hidden from `/v1/settings`.

## Data model (PostgreSQL)

`accounts`, `equity_snapshots`, `symbol_specs`, `positions`, `orders` (trade events), `signals`,
`model_predictions`, `news_events`, `risk_events`, `performance`, `trade_logs`, `system_events`,
`configuration_versions`, `risk_state` (persisted locks/kill switch), `ea_status`, `processed_requests`,
`slippage`, `model_registry`. Migrations: `server/alembic/versions`.

## Time

UTC everywhere internally (`UTCDateTime` column type rejects naive datetimes). Session windows are
defined in each market's own IANA zone (Sydney, Tokyo, London, New York), so DST is automatic. The
trading day rolls at 17:00 New York, identical in backend and EA. The EA converts MT5 server
timestamps to UTC before sending.

## What is deliberately NOT included

- No martingale, grid, loss-doubling, averaging down or unbounded hedging — the risk engine rejects them.
- No reinforcement-learning agent with access to risk settings. RL (if added) may only be used offline
  for research/recommendations; hard limits live in `RiskLimits` and the EA inputs.
- No sequence model (LSTM) is shipped; `SignalModel` is the interface where one would plug in.
