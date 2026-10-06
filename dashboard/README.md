# Dashboard

React 18 + Vite + TypeScript + Recharts. Talks to the backend with a short-lived JWT obtained from the
admin `API_KEY` (stored in `sessionStorage` only).

```bash
cd dashboard
cp .env.example .env          # VITE_API_URL=http://localhost:8000
npm install
npm run dev                   # http://localhost:5173
npm run build                 # strict type-check + production build into dist/
```

Pages: **Overview** (mode banner, system/backend/EA/MT5/kill-switch/news/model status, session, account
KPIs, open risk & exposure, win rate, profit factor, expectancy, quotes/spreads, regime, AI confidence,
START / PAUSE / RESUME / EMERGENCY STOP / resets), **Trades** (open/closed table), **Performance** (equity,
drawdown, daily/monthly P/L, breakdowns by symbol, strategy, session, regime, long vs short),
**Risk & Settings** (effective limits, tighten-only overrides: max risk, max positions, sessions, symbols;
exposure; correlation matrix; MT5 symbol specs), **News**, **Model**, **Logs**.

The *Account view* toggle switches between the PAPER account and the MT5 (demo/live) account.
`VITE_API_URL` is compiled in at build time; set `CORS_ORIGINS` on the backend to the dashboard's origin.
Light and dark themes follow the OS setting.
