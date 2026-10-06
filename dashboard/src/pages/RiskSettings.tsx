import { useState } from "react";
import type { Portfolio, Source } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { api } from "../services/api";
import { fmtMoney, fmtNum, fmtPct } from "../services/format";
import { Card, ErrorBox } from "../components/ui";

type Settings = Record<string, unknown> & {
  effective_limits: Record<string, number>;
  runtime_overrides: Record<string, unknown>;
  symbol_map: Record<string, string>;
  effective_trading_mode: string;
};
type SymbolRow = Record<string, string | number>;
const SESSIONS = ["SYDNEY", "TOKYO", "LONDON", "NEW_YORK", "OVERLAP"];

export function RiskSettings({ source }: { source: Source }) {
  const settings = usePoll<Settings>("/v1/settings", 30000);
  const pf = usePoll<Portfolio>(`/v1/portfolio?source=${source}`, 10000);
  const acct = usePoll<{ symbols: SymbolRow[] }>("/v1/account", 30000);
  const [msg, setMsg] = useState<string | null>(null);
  const s = settings.data;
  const lim = s?.effective_limits;
  const [risk, setRisk] = useState("");
  const [maxPos, setMaxPos] = useState("");
  const enabledSessions = (s?.runtime_overrides.enabled_sessions as string[] | undefined) ?? SESSIONS;
  const symbols = Object.keys(s?.symbol_map ?? {});
  const enabledSymbols = (s?.runtime_overrides.enabled_symbols as string[] | undefined) ?? symbols;

  const save = async (body: Record<string, unknown>) => {
    try {
      await api.post("/v1/settings", body);
      setMsg("Saved (can only tighten configured limits).");
      void settings.refresh();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  };
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const corr = pf.data?.correlation ?? {};
  const corrKeys = Object.keys(corr);

  return (
    <>
      <ErrorBox error={settings.error || pf.error} />
      <div className="cols">
        <Card title="Effective risk limits" note="from environment, tightened by overrides">
          {lim && (
            <table>
              <tbody>
                <tr><td>Risk per trade</td><td className="num">{fmtPct(lim.risk_per_trade)}</td></tr>
                <tr><td>Max daily loss</td><td className="num">{fmtPct(lim.max_daily_loss)}</td></tr>
                <tr><td>Max drawdown</td><td className="num">{fmtPct(lim.max_drawdown)}</td></tr>
                <tr><td>Max symbol risk</td><td className="num">{fmtPct(lim.max_symbol_risk)}</td></tr>
                <tr><td>Max strategy risk</td><td className="num">{fmtPct(lim.max_strategy_risk)}</td></tr>
                <tr><td>Max portfolio risk</td><td className="num">{fmtPct(lim.max_portfolio_risk)}</td></tr>
                <tr><td>Max correlated exposure</td><td className="num">{fmtPct(lim.max_correlated_exposure)}</td></tr>
                <tr><td>Max margin usage</td><td className="num">{fmtPct(lim.max_margin_usage, 0)}</td></tr>
                <tr><td>Max positions (total / per symbol)</td><td className="num">{lim.max_positions} / {lim.max_positions_per_symbol}</td></tr>
                <tr><td>Risk multiplier bounds</td><td className="num">{lim.risk_multiplier_min} – {lim.risk_multiplier_max}</td></tr>
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Adjust (tighten only)">
          <div className="form-row">
            <label htmlFor="risk">MAX RISK per trade (%)</label>
            <input id="risk" type="number" step="0.05" min="0.05" placeholder={lim ? String(lim.risk_per_trade * 100) : ""} value={risk} onChange={(e) => setRisk(e.target.value)} />
            <button className="btn" disabled={!risk} onClick={() => save({ risk_per_trade: Number(risk) / 100 })}>Apply</button>
          </div>
          <div className="form-row">
            <label htmlFor="maxpos">MAX POSITIONS</label>
            <input id="maxpos" type="number" min="1" placeholder={lim ? String(lim.max_positions) : ""} value={maxPos} onChange={(e) => setMaxPos(e.target.value)} />
            <button className="btn" disabled={!maxPos} onClick={() => save({ max_positions: Number(maxPos) })}>Apply</button>
          </div>
          <div className="form-row">
            <label>SESSION SETTINGS</label>
            {SESSIONS.map((x) => (
              <label key={x} style={{ minWidth: 0 }}>
                <input type="checkbox" checked={enabledSessions.includes(x)} onChange={() => save({ enabled_sessions: toggle(enabledSessions, x) })} /> {x}
              </label>
            ))}
          </div>
          <div className="form-row">
            <label>SYMBOL SETTINGS</label>
            {symbols.map((x) => (
              <label key={x} style={{ minWidth: 0 }}>
                <input type="checkbox" checked={enabledSymbols.includes(x)} onChange={() => save({ enabled_symbols: toggle(enabledSymbols, x) })} /> {x} ({s?.symbol_map[x]})
              </label>
            ))}
            {!symbols.length && <span className="muted">No symbols mapped - set XAUUSD_SYMBOL / OIL_SYMBOL / US30_SYMBOL.</span>}
          </div>
          {msg && <div className="muted">{msg}</div>}
        </Card>
      </div>
      <div className="cols">
        <Card title="Portfolio exposure" note={source}>
          {pf.data && (
            <table>
              <tbody>
                <tr><td>Open risk</td><td className="num">{fmtMoney(pf.data.total_risk)} ({fmtPct(pf.data.total_risk_pct)})</td></tr>
                <tr><td>Unrealized P/L</td><td className="num">{fmtMoney(pf.data.unrealized_pnl)}</td></tr>
                {Object.entries(pf.data.risk_by_symbol).map(([k, v]) => <tr key={k}><td>Risk · {k}</td><td className="num">{fmtMoney(v)}</td></tr>)}
                {Object.entries(pf.data.risk_by_strategy).map(([k, v]) => <tr key={k}><td>Risk · {k}</td><td className="num">{fmtMoney(v)}</td></tr>)}
                <tr><td>Hedged symbols</td><td className="num">{pf.data.hedged_symbols.join(", ") || "none"}</td></tr>
                <tr><td>Positions without stop</td><td className="num">{pf.data.positions_without_stop}</td></tr>
              </tbody>
            </table>
          )}
        </Card>
        <Card title="Correlation matrix" note="rolling, from reported bars; unknown pairs use a conservative fallback">
          {corrKeys.length ? (
            <div className="table-wrap">
              <table>
                <thead><tr><th />{corrKeys.map((k) => <th key={k} className="num">{k}</th>)}</tr></thead>
                <tbody>
                  {corrKeys.map((a) => (
                    <tr key={a}><td>{a}</td>{corrKeys.map((b) => <td key={b} className="num">{fmtNum(corr[a][b])}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <span className="muted">No data.</span>}
        </Card>
      </div>
      <Card title="Symbol specifications" note="reported by MT5 - never assumed">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Broker symbol</th><th>Logical</th><th className="num">Digits</th><th className="num">Tick size</th><th className="num">Tick value</th><th className="num">Contract</th><th className="num">Vol min / step / max</th><th className="num">Stops lvl</th><th className="num">Margin/lot</th><th>Trade mode</th><th>Updated</th></tr></thead>
            <tbody>
              {(acct.data?.symbols ?? []).map((r) => (
                <tr key={String(r.broker_symbol)}>
                  <td>{r.broker_symbol}</td><td>{r.logical_symbol}</td><td className="num">{r.digits}</td><td className="num">{r.tick_size}</td><td className="num">{r.tick_value}</td><td className="num">{r.contract_size}</td>
                  <td className="num">{r.volume_min} / {r.volume_step} / {r.volume_max}</td><td className="num">{r.stops_level}</td><td className="num">{r.margin_per_lot}</td><td>{r.trade_mode}</td><td>{new Date(String(r.updated_at)).toLocaleString()}</td>
                </tr>
              ))}
              {!acct.data?.symbols.length && <tr><td colSpan={11} className="muted">No specs yet - the EA reports them on start.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
