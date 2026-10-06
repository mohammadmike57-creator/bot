import type { GroupStats } from "../types/api";
import { fmtMoney, fmtNum, fmtPct } from "../services/format";
import { PnlBars } from "./charts";
import { Signed } from "./ui";

/** Breakdown: net P/L bars (polarity) plus a full table view. */
export function GroupBreakdown({ groups }: { groups: Record<string, GroupStats> }) {
  const rows = Object.entries(groups).sort((a, b) => b[1].net - a[1].net);
  if (!rows.length) return <div className="muted">No closed trades yet.</div>;
  return (
    <>
      <PnlBars data={rows.map(([k, g]) => ({ k, v: g.net }))} fmt={(v) => fmtMoney(v)} />
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Group</th><th className="num">Trades</th><th className="num">Net</th><th className="num">Win rate</th><th className="num">PF</th><th className="num">Avg R</th></tr>
          </thead>
          <tbody>
            {rows.map(([k, g]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="num">{g.trades}</td>
                <td className="num"><Signed value={g.net}>{fmtMoney(g.net)}</Signed></td>
                <td className="num">{fmtPct(g.win_rate, 1)}</td>
                <td className="num">{fmtNum(g.profit_factor)}</td>
                <td className="num">{fmtNum(g.avg_r)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
