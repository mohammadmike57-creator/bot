import { useState } from "react";
import type { Performance, Portfolio, Source, Status } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { api } from "../services/api";
import { fmtMoney, fmtNum, fmtPct, fmtPrice, fmtTime } from "../services/format";
import { Card, ErrorBox, StatusBadge, Tile } from "../components/ui";

function Controls({ status, onChange }: { status: Status; onChange: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const c = status.controls;
  const act = async (fn: () => Promise<unknown>, done: string) => {
    try {
      await fn();
      setMsg(done);
      onChange();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };
  const reset = (items: string[], label: string) => {
    const typed = window.prompt(`Manual reset of ${label}.\nThis re-enables trading. Type RESET to confirm:`);
    if (typed !== "RESET") return;
    void act(() => api.post("/v1/reset-risk", { items, confirm: "RESET", note: "dashboard" }), `${label} reset`);
  };
  return (
    <Card title="Controls" note="the risk engine keeps absolute authority">
      <div className="toolbar">
        <button className="btn primary" onClick={() => act(() => api.post("/v1/control", { action: "START" }), "System started")}>START SYSTEM</button>
        <button className="btn" onClick={() => act(() => api.post("/v1/control", { action: "PAUSE" }), "New trades paused")}>PAUSE NEW TRADES</button>
        <button className="btn" onClick={() => act(() => api.post("/v1/control", { action: "RESUME" }), "Resumed")}>RESUME</button>
        <span className="spacer" />
        <button
          className="btn danger"
          onClick={() => {
            const reason = window.prompt("EMERGENCY STOP - reason:");
            if (!reason || reason.length < 3) return;
            const close = window.confirm("Also CLOSE all open positions?\nOK = close, Cancel = keep managing them");
            void act(() => api.post("/v1/kill-switch", { reason, close_positions: close }), "EMERGENCY STOP ACTIVE");
          }}
        >
          EMERGENCY STOP
        </button>
      </div>
      <div className="toolbar">
        <button className="btn" onClick={() => reset(["daily_loss"], "daily risk")}>RESET DAILY RISK</button>
        <button className="btn" onClick={() => reset(["drawdown"], "drawdown lock")}>RESET DRAWDOWN LOCK</button>
        <button className="btn" disabled={!c?.kill_switch} onClick={() => reset(["kill_switch"], "kill switch")}>RESET KILL SWITCH</button>
        <button className="btn" disabled={!c?.safe_mode} onClick={() => reset(["safe_mode"], "safe mode")}>CLEAR SAFE MODE</button>
      </div>
      {msg && <div className="muted">{msg}</div>}
    </Card>
  );
}

export function Overview({ source }: { source: Source }) {
  const status = usePoll<Status>("/v1/status", 5000);
  const perf = usePoll<Performance>(`/v1/performance?source=${source}`, 30000);
  const pf = usePoll<Portfolio>(`/v1/portfolio?source=${source}`, 10000);
  const s = status.data;
  const p = perf.data;
  const port = pf.data;
  const acc = s ? (source === "PAPER" ? s.accounts.paper : s.accounts.mt5) : null;
  const c = s?.controls;
  const quotes = s?.ea.quotes ?? {};
  const regimes = s ? Object.entries(s.regimes).map(([k, v]) => `${k}: ${v}`).join("  ") : "";
  const conf = s ? Object.entries(s.confidence).map(([k, v]) => `${k}: ${v ? fmtNum(v) : "rule-based"}`).join("  ") : "";

  return (
    <>
      <ErrorBox error={status.error || perf.error || pf.error} />
      {s && (
        <div className="grid">
          <Tile label="System status" value={<StatusBadge level={c?.kill_switch ? "critical" : c?.safe_mode ? "serious" : c?.paused ? "warning" : "good"} label={c?.kill_switch ? "KILLED" : c?.safe_mode ? "SAFE MODE" : c?.paused ? "PAUSED" : "RUNNING"} />} sub={c?.kill_reason || c?.safe_mode_reason || " "} />
          <Tile label="Backend" value={<StatusBadge level={s.backend.database === "OK" ? "good" : "critical"} label={`API ${s.backend.status} · DB ${s.backend.database}`} />} />
          <Tile label="EA heartbeat" value={<StatusBadge level={s.ea.status === "ONLINE" ? "good" : "critical"} label={s.ea.status} />} sub={s.ea.last_heartbeat ? `${fmtNum(s.ea.age_seconds ?? 0, 0)}s ago · ${fmtNum(s.ea.latency_ms ?? 0, 0)} ms` : "never"} />
          <Tile label="MT5 connection" value={<StatusBadge level={s.ea.terminal_connected ? "good" : "critical"} label={s.ea.terminal_connected ? "CONNECTED" : "DISCONNECTED"} />} sub={s.ea.trade_allowed ? "algo trading allowed" : "algo trading OFF"} />
          <Tile label="Kill switch" value={<StatusBadge level={c?.kill_switch || s.ea.local_kill_switch ? "critical" : "good"} label={c?.kill_switch ? "ACTIVE" : s.ea.local_kill_switch ? "EA LOCAL ACTIVE" : "OFF"} />} />
          <Tile label="News" value={<StatusBadge level={s.news.status === "OK" ? "good" : "serious"} label={s.news.status === "OK" ? "OK" : `${s.news.status} → SAFE`} />} />
          <Tile label="Model" value={<StatusBadge level={s.model.mode === "MODEL" ? "good" : s.model.mode === "RULE_BASED" ? "warning" : "critical"} label={s.model.mode} />} sub={`version ${s.model.version}`} />
          <Tile label="Session" value={s.session.primary} sub={`${s.session.activity} · market ${s.session.market_open ? "open" : s.session.market_reason}`} />
        </div>
      )}

      <div className="grid">
        <Tile label="Balance" value={fmtMoney(acc?.balance)} sub={acc?.currency} />
        <Tile label="Equity" value={fmtMoney(acc?.equity)} />
        <Tile label="Free margin" value={fmtMoney(acc?.free_margin)} />
        <Tile label="Margin level" value={acc?.margin_level ? `${fmtNum(acc.margin_level, 0)}%` : "—"} />
        <Tile label="Daily P/L" value={fmtMoney(acc?.daily_pnl)} tone={acc && acc.daily_pnl < 0 ? "neg" : acc && acc.daily_pnl > 0 ? "pos" : undefined} sub={acc?.daily_loss_locked ? "DAILY LOSS LOCK" : undefined} />
        <Tile label="Total P/L" value={fmtMoney(acc?.total_pnl)} tone={acc && acc.total_pnl < 0 ? "neg" : acc && acc.total_pnl > 0 ? "pos" : undefined} />
        <Tile label="Drawdown" value={fmtPct(acc?.drawdown)} sub={acc?.drawdown_locked ? "DRAWDOWN LOCK" : undefined} />
        <Tile label="Open positions" value={port?.open_positions ?? "—"} sub={port?.hedged_symbols.length ? `hedged: ${port.hedged_symbols.join(", ")}` : undefined} />
        <Tile label="Open risk" value={fmtMoney(port?.total_risk)} sub={fmtPct(port?.total_risk_pct)} />
        <Tile label="Portfolio exposure" value={port ? Object.entries(port.risk_by_direction).map(([k, v]) => `${k} ${fmtMoney(v)}`).join(" / ") || "flat" : "—"} sub={port?.margin_usage != null ? `margin ${fmtPct(port.margin_usage)}` : undefined} />
        <Tile label="Win rate" value={fmtPct(p?.win_rate, 1)} sub={`${p?.trades ?? 0} trades`} />
        <Tile label="Profit factor" value={fmtNum(p?.profit_factor)} />
        <Tile label="Average win" value={fmtMoney(p?.avg_win)} />
        <Tile label="Average loss" value={fmtMoney(p?.avg_loss)} />
        <Tile label="Expectancy" value={fmtMoney(p?.expectancy)} sub={`avg ${fmtNum(p?.avg_r)} R`} />
        <Tile label="Trades today" value={s?.trades_today ?? "—"} />
      </div>

      {s && (
        <Card title="Market" note={`updated ${fmtTime(s.time)}`}>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Symbol</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spread (pts)</th></tr></thead>
              <tbody>
                {Object.entries(quotes).map(([sym, q]) => (
                  <tr key={sym}><td>{sym}</td><td className="num">{fmtPrice(q.bid)}</td><td className="num">{fmtPrice(q.ask)}</td><td className="num">{fmtNum(q.spread, 1)}</td></tr>
                ))}
                {!Object.keys(quotes).length && <tr><td colSpan={4} className="muted">No quotes - is the EA running?</td></tr>}
              </tbody>
            </table>
          </div>
          <p className="muted">Regime: {regimes || "—"}<br />AI confidence (last decision): {conf || "—"}</p>
        </Card>
      )}
      {s && <Controls status={s} onChange={() => void status.refresh()} />}
    </>
  );
}
