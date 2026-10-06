import { useState } from "react";
import type { Source, Trade } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { fmtDuration, fmtMoney, fmtNum, fmtPrice } from "../services/format";
import { Card, ErrorBox, Signed } from "../components/ui";

export function Trades({ source }: { source: Source }) {
  const [status, setStatus] = useState<"open" | "closed">("open");
  const { data, error } = usePoll<Trade[]>(`/v1/trades?status=${status}&source=${source}&limit=500`, 5000);
  return (
    <>
      <ErrorBox error={error} />
      <div className="toolbar">
        <div className="seg">
          <button className={status === "open" ? "on" : ""} onClick={() => setStatus("open")}>Open</button>
          <button className={status === "closed" ? "on" : ""} onClick={() => setStatus("closed")}>Closed</button>
        </div>
      </div>
      <Card title={`${status === "open" ? "Open" : "Closed"} trades`} note={source}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Ticket</th><th>Symbol</th><th>Strategy</th><th>Direction</th><th className="num">Entry</th>
                <th className="num">{status === "open" ? "Current" : "Exit"}</th><th className="num">SL</th><th className="num">TP</th>
                <th className="num">Lot</th><th className="num">Risk</th><th className="num">Profit</th><th className="num">R</th>
                <th className="num">Duration</th><th className="num">AI conf.</th><th>Session</th><th>Status</th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((t) => (
                <tr key={`${t.source}-${t.ticket}`}>
                  <td>{t.ticket}</td>
                  <td>{t.logical_symbol || t.symbol}</td>
                  <td>{t.strategy || "—"}</td>
                  <td>{t.direction}</td>
                  <td className="num">{fmtPrice(t.entry)}</td>
                  <td className="num">{fmtPrice(t.current)}</td>
                  <td className="num">{fmtPrice(t.sl)}{t.be_applied ? " (BE)" : ""}</td>
                  <td className="num">{fmtPrice(t.tp)}</td>
                  <td className="num">{t.lot}{t.partial_done ? " (P)" : ""}</td>
                  <td className="num">{fmtMoney(t.risk)}</td>
                  <td className="num"><Signed value={t.profit}>{fmtMoney(t.profit)}</Signed></td>
                  <td className="num">{fmtNum(t.r_multiple)}</td>
                  <td className="num">{fmtDuration(t.duration_minutes)}</td>
                  <td className="num">{t.confidence ? fmtNum(t.confidence) : "rule"}</td>
                  <td>{t.session || "—"}</td>
                  <td>{t.status}{t.close_reason ? ` · ${t.close_reason}` : ""}</td>
                </tr>
              ))}
              {!data?.length && <tr><td colSpan={16} className="muted">No {status} trades.</td></tr>}
            </tbody>
          </table>
        </div>
        <p className="muted">(BE) = stop at break-even or better · (P) = partial profit taken</p>
      </Card>
    </>
  );
}
