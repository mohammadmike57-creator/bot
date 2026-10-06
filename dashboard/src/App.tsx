import { useEffect, useState } from "react";
import type { Source, Status } from "./types/api";
import { clearToken, getToken } from "./services/api";
import { usePoll } from "./hooks/usePoll";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Trades } from "./pages/Trades";
import { PerformancePage } from "./pages/PerformancePage";
import { RiskSettings } from "./pages/RiskSettings";
import { NewsPage } from "./pages/NewsPage";
import { ModelPage } from "./pages/ModelPage";
import { LogsPage } from "./pages/LogsPage";

const PAGES = ["Overview", "Trades", "Performance", "Risk & Settings", "News", "Model", "Logs"] as const;
type Page = (typeof PAGES)[number];

function ModeBanner({ status }: { status: Status | null }) {
  if (!status) return null;
  const m = status.trading_mode;
  const text = m === "PAPER" ? "PAPER MODE — orders are simulated, nothing is sent to the broker"
    : m === "DEMO" ? "DEMO MODE — orders go to a DEMO account only" : "LIVE MODE — real money";
  return (
    <div className={`mode-banner ${m}`} role="status">
      {text}
      <small>configured {status.configured_mode} · live permitted: {String(status.live_permitted)}</small>
    </div>
  );
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const [page, setPage] = useState<Page>(() => (localStorageGet("tai_page") as Page) || "Overview");
  const status = usePoll<Status>("/v1/status", 10000);
  const [source, setSource] = useState<Source>("PAPER");
  useEffect(() => {
    if (status.data) setSource(status.data.trading_mode === "PAPER" ? "PAPER" : "MT5");
  }, [status.data?.trading_mode]);
  useEffect(() => localStorageSet("tai_page", page), [page]);
  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>Trading AI</h1>
        <nav>
          {PAGES.map((p) => (
            <button key={p} className={p === page ? "active" : ""} onClick={() => setPage(p)}>{p}</button>
          ))}
        </nav>
        <div className="foot">
          <button className="btn" onClick={onLogout}>Sign out</button>
        </div>
      </aside>
      <main>
        <ModeBanner status={status.data} />
        <div className="toolbar">
          <span className="muted">Account view</span>
          <div className="seg">
            {(["PAPER", "MT5"] as Source[]).map((s) => (
              <button key={s} className={s === source ? "on" : ""} onClick={() => setSource(s)}>{s === "PAPER" ? "Paper" : "MT5 (demo/live)"}</button>
            ))}
          </div>
        </div>
        {page === "Overview" && <Overview source={source} />}
        {page === "Trades" && <Trades source={source} />}
        {page === "Performance" && <PerformancePage source={source} />}
        {page === "Risk & Settings" && <RiskSettings source={source} />}
        {page === "News" && <NewsPage />}
        {page === "Model" && <ModelPage />}
        {page === "Logs" && <LogsPage />}
      </main>
    </div>
  );
}

function localStorageGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function localStorageSet(k: string, v: string): void {
  try { localStorage.setItem(k, v); } catch { /* ignore */ }
}

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  useEffect(() => {
    const out = () => setAuthed(false);
    window.addEventListener("tai-logout", out);
    return () => window.removeEventListener("tai-logout", out);
  }, []);
  if (!authed) return <Login onLogin={() => setAuthed(true)} />;
  return <Shell onLogout={() => { clearToken(); setAuthed(false); }} />;
}
