import type { Performance, Source } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { fmtMoney, fmtNum, fmtPct } from "../services/format";
import { DrawdownArea, PnlBars, TimeLine } from "../components/charts";
import { GroupBreakdown } from "../components/GroupTable";
import { Card, ErrorBox, Tile } from "../components/ui";

export function PerformancePage({ source }: { source: Source }) {
  const { data: p, error } = usePoll<Performance>(`/v1/performance?source=${source}`, 30000);
  const daily = p ? Object.entries(p.daily_pnl).map(([k, v]) => ({ k, v })) : [];
  const monthly = p ? Object.entries(p.monthly_pnl).map(([k, v]) => ({ k, v })) : [];
  const sym = (name: string) => p?.by_symbol[name];
  return (
    <>
      <ErrorBox error={error} />
      <p className="muted">Live/paper results only. Past performance - simulated or real - does not imply future results.</p>
      <div className="grid">
        <Tile label="Net profit" value={fmtMoney(p?.net_profit)} />
        <Tile label="Win rate" value={fmtPct(p?.win_rate, 1)} sub={`${p?.wins ?? 0}W / ${p?.losses ?? 0}L`} />
        <Tile label="Profit factor" value={fmtNum(p?.profit_factor)} />
        <Tile label="Average R" value={fmtNum(p?.avg_r)} />
        <Tile label="Max drawdown" value={fmtPct(p?.max_drawdown)} />
        <Tile label="Sharpe / Sortino" value={`${fmtNum(p?.sharpe)} / ${fmtNum(p?.sortino)}`} />
        {["XAUUSD", "OIL", "US30"].map((s) => (
          <Tile key={s} label={`${s} performance`} value={fmtMoney(sym(s)?.net)} sub={sym(s) ? `${sym(s)!.trades} trades · PF ${fmtNum(sym(s)!.profit_factor)}` : "no trades"} />
        ))}
      </div>
      <div className="cols">
        <Card title="Equity curve"><TimeLine data={p?.equity_curve ?? []} x="t" y="equity" fmt={(v) => fmtMoney(v)} /></Card>
        <Card title="Drawdown"><DrawdownArea data={p?.drawdown_curve ?? []} /></Card>
        <Card title="Daily P/L"><PnlBars data={daily} fmt={(v) => fmtMoney(v)} /></Card>
        <Card title="Monthly P/L"><PnlBars data={monthly} fmt={(v) => fmtMoney(v)} /></Card>
      </div>
      <div className="cols">
        <Card title="By symbol"><GroupBreakdown groups={p?.by_symbol ?? {}} /></Card>
        <Card title="By strategy"><GroupBreakdown groups={p?.by_strategy ?? {}} /></Card>
        <Card title="By session"><GroupBreakdown groups={p?.by_session ?? {}} /></Card>
        <Card title="By market regime"><GroupBreakdown groups={p?.by_regime ?? {}} /></Card>
        <Card title="Long vs short"><GroupBreakdown groups={p?.by_direction ?? {}} /></Card>
      </div>
    </>
  );
}
