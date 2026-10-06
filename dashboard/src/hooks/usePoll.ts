import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../services/api";

/** Polls a GET endpoint. Returns data, error, a manual refresh and the last update time. */
export function usePoll<T>(path: string | null, intervalMs = 5000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    if (!path) return;
    try {
      const d = await api.get<T>(path);
      if (!alive.current) return;
      setData(d);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    void refresh();
    if (!path) return;
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => {
      alive.current = false;
      window.clearInterval(id);
    };
  }, [path, intervalMs, refresh]);

  return { data, error, refresh, updatedAt };
}
