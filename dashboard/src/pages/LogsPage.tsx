import { useState } from "react";
import type { DecisionLog } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { fmtTime } from "../services/format";
import { Card, ErrorBox } from "../components/ui";

interface Events {
  risk_events: { ts: string; type: string; severity: string; reason: string; symbol: string }[];
  system_events: { ts: string; component: string; event: string; level: string; message: string }[];
  alerts: { ts: string; type: string; severity: string; message: string }[];
}

export function LogsPage() {
  const [filter, setFilter] = useState<string>("");
  const logs = usePoll<DecisionLog[]>(`/v1/logs?limit=200${filter ? `&decision=${filter}` : ""}`, 10000);
  const ev = usePoll<Events>("/v1/events?limit=100", 15000);
  return (
    <>
      <ErrorBox error={logs.error || ev.error} />
      <div className="toolbar">
        <div className="seg">
          {["", "BUY", "SELL", "REJECT"].map((f) => (
            <button key={f || "all"} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f || "All"}</button>
          ))}
        </div>
      </div>
      <Card title="Decision log" note="every accepted and rejected decision, with its reason">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Time</th><th>Symbol</th><th>Decision</th><th>Reason</th></tr></thead>
            <tbody>
              {(logs.data ?? []).map((l, i) => (
                <tr key={i} title={l.line}><td>{fmtTime(l.ts)}</td><td>{l.symbol}</td><td>{l.decision}</td><td style={{ whiteSpace: "normal" }}>{l.reason}<pre className="log">{l.line}</pre></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <div className="cols">
        <Card title="Risk events">
          <table><tbody>{(ev.data?.risk_events ?? []).map((e, i) => <tr key={i}><td>{fmtTime(e.ts)}</td><td>{e.severity}</td><td>{e.type}</td><td style={{ whiteSpace: "normal" }}>{e.reason}</td></tr>)}</tbody></table>
        </Card>
        <Card title="Alerts">
          <table><tbody>{(ev.data?.alerts ?? []).map((e, i) => <tr key={i}><td>{fmtTime(e.ts)}</td><td>{e.severity}</td><td style={{ whiteSpace: "normal" }}>{e.message}</td></tr>)}</tbody></table>
        </Card>
        <Card title="System events">
          <table><tbody>{(ev.data?.system_events ?? []).map((e, i) => <tr key={i}><td>{fmtTime(e.ts)}</td><td>{e.component}</td><td>{e.event}</td><td style={{ whiteSpace: "normal" }}>{e.message}</td></tr>)}</tbody></table>
        </Card>
      </div>
    </>
  );
}
