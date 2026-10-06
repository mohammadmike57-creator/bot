import type { ReactNode } from "react";

export function Tile({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "pos" | "neg" }) {
  return (
    <div className="tile">
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Card({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <h2>
        {title}
        {note && <small>{note}</small>}
      </h2>
      {children}
    </section>
  );
}

type Level = "good" | "warning" | "serious" | "critical" | "neutral";
const ICON: Record<Level, string> = { good: "✓", warning: "!", serious: "!", critical: "✕", neutral: "•" };

/** Status = icon + label + color (never color alone). */
export function StatusBadge({ level, label }: { level: Level; label: string }) {
  const color = level === "neutral" ? "var(--axis)" : `var(--${level})`;
  return (
    <span className="badge" title={label}>
      <span className="dot" style={{ background: color }} aria-hidden />
      <span aria-hidden>{ICON[level]}</span>
      {label}
    </span>
  );
}

export function ErrorBox({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function Signed({ value, children }: { value: number | null | undefined; children: ReactNode }) {
  const cls = value === null || value === undefined || value === 0 ? "" : value > 0 ? "pos" : "neg";
  return <span className={cls}>{children}</span>;
}
