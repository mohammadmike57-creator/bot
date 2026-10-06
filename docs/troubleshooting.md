# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| EA log: `WebRequest not allowed ...` (error 4014) | Add the exact base URL in *Tools › Options › Expert Advisors › Allow WebRequest for listed URL*. Scheme and host must match `InpBackendUrl`. |
| `BACKEND: OFFLINE` on the chart | Backend not reachable from Windows. Open `<url>/health` in Windows' browser. For a backend on the Mac run uvicorn with `--host 0.0.0.0` and use the Mac's Parallels address (`ifconfig bridge100`). If your MT5 build refuses a non-standard port, use the deployed HTTPS URL or a tunnel on port 443. |
| HTTP 401 `TIMESTAMP_OUT_OF_WINDOW` | Windows clock off by > 30 s. *Settings › Time & language › Sync now*. The EA also warns about clock skew vs. the backend. |
| HTTP 401 `INVALID_SIGNATURE` / `INVALID_API_KEY` | `InpApiKeyId` / `InpHmacSecret` differ from `EA_API_KEY_ID` / `EA_HMAC_SECRET` (watch for spaces). |
| HTTP 503 `EA_AUTH_NOT_CONFIGURED` | Backend has no `EA_API_KEY_ID`/`EA_HMAC_SECRET` — it fails closed. |
| HTTP 409 `DUPLICATE_REQUEST_ID` | A request id was reused (replay protection). Harmless if occasional; the EA generates new ids per request. |
| HTTP 422 `INVALID_SYMBOL` | Broker symbol in `InpSymbols` not mapped in the backend (`XAUUSD_SYMBOL`, ...). Both must use the exact MT5 names. |
| EA init fails: `No valid symbols` | `InpSymbols` empty or names wrong; the log lists candidate symbols found in the terminal. |
| EA init fails: `HMAC-SHA256 self-test failed` | `CryptEncode` unavailable/changed in this terminal build — update MT5. |
| Every decision `NEWS_SERVICE_NOT_CONFIGURED` / `FAILED` / `STALE` | By design: no calendar ⇒ SAFE MODE. Configure `NEWS_PROVIDER` + `NEWS_API_KEY` (dashboard *News* shows the provider error). Field mappings follow provider docs; if a provider changed its format, `/v1/news` shows `last_error`. |
| `MIN_VOLUME_EXCEEDS_RISK` on most signals | Expected on small accounts — see risk-management.md "The $100 account". |
| `EA_HEARTBEAT_STALE` | EA not sending heartbeats (detached, Algo Trading off, VM paused). |
| `ACCOUNT_NOT_HEDGING` | Account is netting. Use a hedging account. |
| `DEMO_MODE_BUT_ACCOUNT_IS_NOT_DEMO` | EA in DEMO mode logged into a real account — orders refused. |
| `MAX_DRAWDOWN_REACHED` persists after restart | Correct: requires a manual reset (dashboard or EA input). |
| `SPREAD_TOO_HIGH` around rollover / news | Expected; spreads widen. Tune `MAX_SPREAD_ATR_RATIO` only with backtest evidence. |
| `SYMBOL_SPEC_UNKNOWN` | EA has not reported specs yet (sent every 30 s) or the market was closed so tick value was 0; wait for the next sync. |
| Paper stop filled worse than the stop | Paper positions are checked on each heartbeat quote (every `InpHeartbeatSec`); a price that passes the stop between quotes fills at the observed quote (conservative). |
| Dashboard login fails with CORS error | Add the dashboard origin to `CORS_ORIGINS`. |
| Dashboard shows wrong backend | `VITE_API_URL` is baked in at build time — rebuild/redeploy. |
| `alembic upgrade head` fails on Render | Check `DATABASE_URL` points at the Render database; the app converts `postgres://` automatically. |
| Model stays `RULE_BASED` | No ACTIVE model, artifact missing from `MODELS_DIR`, checksum mismatch, or health problems — see dashboard *Model*. |
