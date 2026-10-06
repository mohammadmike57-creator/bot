import { useState } from "react";
import type { ModelStatus } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { api } from "../services/api";
import { fmtNum, fmtTime } from "../services/format";
import { Card, ErrorBox, StatusBadge } from "../components/ui";

export function ModelPage() {
  const { data: m, error, refresh } = usePoll<ModelStatus>("/v1/model/status", 30000);
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <>
      <ErrorBox error={error} />
      {m && (
        <Card title="Model status">
          <p>
            <StatusBadge level={m.mode === "MODEL" ? "good" : m.mode === "RULE_BASED" ? "warning" : "critical"} label={m.mode} /> version <strong>{m.version}</strong>
            {m.load_error && <span className="muted"> · {m.load_error}</span>}
          </p>
          <table>
            <tbody>
              <tr><td>Predictions / errors</td><td className="num">{m.health.predictions} / {m.health.errors}</td></tr>
              <tr><td>Feature drift (median PSI)</td><td className="num">{fmtNum(m.health.drift_psi, 3)}</td></tr>
              <tr><td>Recent trades / profit factor</td><td className="num">{m.health.recent_trades} / {fmtNum(m.health.recent_profit_factor)}</td></tr>
              <tr><td>Mean confidence</td><td className="num">{fmtNum(m.health.mean_confidence)}</td></tr>
              <tr><td>Problems</td><td className="num">{m.health.problems.join(", ") || "none"}</td></tr>
            </tbody>
          </table>
          <p>
            <button className="btn" onClick={async () => {
              try { await api.post("/v1/model/reload", {}); setMsg("Reloaded"); void refresh(); } catch (e) { setMsg(String(e)); }
            }}>Reload active model</button> {msg && <span className="muted">{msg}</span>}
          </p>
        </Card>
      )}
      <Card title="Model registry">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Model ID</th><th>Version</th><th>Status</th><th>Created</th><th>Dataset</th><th>OOS</th></tr></thead>
            <tbody>
              {(m?.registry ?? []).map((r) => (
                <tr key={r.model_id}>
                  <td>{r.model_id}</td><td>{r.version}</td><td>{r.status}</td><td>{fmtTime(r.created_at)}</td>
                  <td>{r.dataset_start?.slice(0, 10)} → {r.dataset_end?.slice(0, 10)}</td>
                  <td><code>{JSON.stringify(r.oos_metrics).slice(0, 120)}</code></td>
                </tr>
              ))}
              {!m?.registry.length && <tr><td colSpan={6} className="muted">No models trained yet (scripts/train_models.py).</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
