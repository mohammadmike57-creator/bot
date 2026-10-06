export const fmtMoney = (v: number | null | undefined, ccy = "") =>
  v === null || v === undefined || Number.isNaN(v)
    ? "—"
    : `${v < 0 ? "-" : ""}${ccy ? ccy + " " : ""}${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const fmtPct = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : `${(v * 100).toFixed(digits)}%`;

export const fmtNum = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(digits);

export const fmtTime = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "—");

export const fmtDuration = (minutes: number) => {
  if (minutes < 60) return `${Math.round(minutes)}m`;
  const h = Math.floor(minutes / 60);
  if (h < 48) return `${h}h ${Math.round(minutes % 60)}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
};

/** Prices: at most 5 decimals, trailing zeros dropped. */
export const fmtPrice = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v) || v === 0 ? "—" : String(+v.toFixed(5));
