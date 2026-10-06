import { useState } from "react";
import { api, login } from "../services/api";
import { ErrorBox } from "../components/ui";

export function Login({ onLogin }: { onLogin: () => void }) {
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login card">
      <h2>Trading AI Platform</h2>
      <p className="muted">Backend: {api.base}</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await login(key.trim());
            onLogin();
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="key">Admin API key</label>
        <input id="key" type="password" autoComplete="current-password" value={key} onChange={(e) => setKey(e.target.value)} />
        <ErrorBox error={error} />
        <button className="btn primary" disabled={busy || key.length < 16}>Sign in</button>
      </form>
    </div>
  );
}
