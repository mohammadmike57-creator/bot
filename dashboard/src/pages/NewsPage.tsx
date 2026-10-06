import type { News } from "../types/api";
import { usePoll } from "../hooks/usePoll";
import { fmtTime } from "../services/format";
import { Card, ErrorBox, StatusBadge } from "../components/ui";

export function NewsPage() {
  const { data: n, error } = usePoll<News>("/v1/news?hours=72", 60000);
  return (
    <>
      <ErrorBox error={error} />
      {n && (
        <Card title="News service" note={`provider ${n.provider}`}>
          <p>
            <StatusBadge level={n.status === "OK" ? "good" : "serious"} label={n.status} />{" "}
            <span className="muted">last success {fmtTime(n.last_success)} {n.last_error && `· ${n.last_error}`} {n.provider_error && `· ${n.provider_error}`}</span>
          </p>
          {n.status !== "OK" && <p>News data unavailable → SAFE MODE: no new trades on any symbol.</p>}
          <table>
            <thead><tr><th>Symbol</th><th>Blackout status</th></tr></thead>
            <tbody>{Object.entries(n.blackout).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v === "CLEAR" ? <StatusBadge level="good" label="CLEAR" /> : <StatusBadge level="warning" label={v} />}</td></tr>)}</tbody>
          </table>
        </Card>
      )}
      <Card title="Upcoming medium / high impact events">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Time</th><th>Impact</th><th>Ccy</th><th>Event</th><th>Prev</th><th>Forecast</th><th>Actual</th><th>Affects</th></tr></thead>
            <tbody>
              {(n?.events ?? []).map((e, i) => (
                <tr key={i}>
                  <td>{fmtTime(e.scheduled_at)}</td><td>{e.importance}</td><td>{e.currency}</td><td>{e.event}</td>
                  <td>{e.previous}</td><td>{e.forecast}</td><td>{e.actual}</td><td>{e.relevant_to.join(", ")}</td>
                </tr>
              ))}
              {!n?.events.length && <tr><td colSpan={8} className="muted">No events loaded.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
