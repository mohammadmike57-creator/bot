import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from "recharts";
import type { TooltipProps } from "recharts";

const axis = { stroke: "var(--axis)", tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false };

function Tip({ active, payload, label, fmt }: TooltipProps<number, string> & { fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tip">
      <div className="muted">{String(label)}</div>
      <strong>{fmt(Number(payload[0].value))}</strong>
    </div>
  );
}

const shortDate = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

/** Single series over time (equity). Crosshair + tooltip via recharts Tooltip. */
export function TimeLine({ data, x, y, fmt }: { data: Record<string, unknown>[]; x: string; y: string; fmt: (v: number) => string }) {
  if (!data.length) return <Empty />;
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={x} tickFormatter={shortDate} minTickGap={40} {...axis} />
        <YAxis domain={["auto", "auto"]} width={64} tickFormatter={(v: number) => fmt(v)} {...axis} axisLine={false} />
        <Tooltip content={<Tip fmt={fmt} />} cursor={{ stroke: "var(--axis)" }} labelFormatter={(l) => new Date(String(l)).toLocaleString()} />
        <Line type="monotone" dataKey={y} stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Drawdown (0 at top, deeper = lower). */
export function DrawdownArea({ data }: { data: { t: string; dd: number }[] }) {
  if (!data.length) return <Empty />;
  const rows = data.map((d) => ({ t: d.t, dd: -d.dd * 100 }));
  const fmt = (v: number) => `${v.toFixed(2)}%`;
  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="t" tickFormatter={shortDate} minTickGap={40} {...axis} />
        <YAxis width={64} tickFormatter={fmt} domain={["auto", 0]} {...axis} axisLine={false} />
        <Tooltip content={<Tip fmt={fmt} />} cursor={{ stroke: "var(--axis)" }} />
        <Area type="monotone" dataKey="dd" stroke="var(--neg)" strokeWidth={2} fill="var(--neg)" fillOpacity={0.12} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Polarity bars (P/L): diverging poles, gain = blue, loss = red, zero baseline. */
export function PnlBars({ data, fmt }: { data: { k: string; v: number }[]; fmt: (v: number) => string }) {
  if (!data.length) return <Empty />;
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }} barCategoryGap={2}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="k" minTickGap={24} {...axis} />
        <YAxis width={64} tickFormatter={fmt} domain={[(min: number) => Math.min(0, min), (max: number) => Math.max(0, max)]} {...axis} axisLine={false} />
        <ReferenceLine y={0} stroke="var(--axis)" />
        <Tooltip content={<Tip fmt={fmt} />} cursor={{ fill: "var(--grid)", fillOpacity: 0.4 }} />
        <Bar dataKey="v" maxBarSize={18} radius={[2, 2, 2, 2]} isAnimationActive={false}>
          {data.map((d) => <Cell key={d.k} fill={d.v >= 0 ? "var(--pos)" : "var(--neg)"} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Empty() {
  return <div className="muted" style={{ padding: "24px 0" }}>No data yet.</div>;
}
