"use client";

import { useCallback, useEffect, useState } from "react";
import AppFrame from "@/components/AppFrame";
import ObservabilityCharts from "@/components/observability/ObservabilityCharts";
import { fetchObservabilitySnapshot, getAuth, type ObservabilitySnapshot } from "@/lib/api_client";

const POLL_MS = 50_000;
const IST_TIME_FMT = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** Backend `ResolvedBrokerContext.source` — what feeds index candles for this snapshot. */
function marketDataLabel(source: string | undefined): string {
  switch ((source || "").trim()) {
    case "user_fyers":
      return "Fyers (your connected broker)";
    case "user_zerodha":
      return "Zerodha (your connected broker)";
    case "platform_shared":
      return "Platform shared session (paper)";
    case "platform_only_unavailable":
      return "Platform broker configured but session not available";
    case "none":
      return "No market-data session";
    default:
      return source || "—";
  }
}

export default function ObservabilityPage() {
  const [snap, setSnap] = useState<ObservabilitySnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (refresh: boolean) => {
    setErr(null);
    if (refresh) setLoading(true);
    try {
      const data = await fetchObservabilitySnapshot(refresh);
      setSnap(data);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load observability");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    const id = window.setInterval(() => void load(false), POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);

  return (
    <AppFrame
      title="Observability"
      subtitle="Spot indicators and session data use the same broker session and math as the live engine. One panel per subscribed strategy (Stochastic BNF, SuperTrend Trail, PS/VS MTF)."
    >
      <div className="obs-page-actions">
        <button type="button" className="toggle-button" disabled={loading} onClick={() => void load(true)}>
          {loading ? "Loading…" : "Refresh now"}
        </button>
        {snap?.fetchedAt ? (
          <span className="obs-fetched">
            Signed in as <strong>{getAuth()?.username ?? "—"}</strong>
            {" · "}
            Market data: <strong>{marketDataLabel(snap.brokerSource)}</strong>
            {" · "}
            Last fetch: {IST_TIME_FMT.format(new Date(snap.fetchedAt * 1000))} IST
          </span>
        ) : null}
      </div>

      {err ? <div className="obs-banner obs-banner--warn">{err}</div> : null}

      {loading && !snap ? (
        <div className="table-card">
          <p>Loading charts…</p>
        </div>
      ) : snap ? (
        <ObservabilityCharts snapshot={snap} />
      ) : null}
    </AppFrame>
  );
}
