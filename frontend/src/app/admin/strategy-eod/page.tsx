"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { APP_TIMEZONE } from "@/lib/datetime_ist";
import { apiJson } from "@/lib/api_client";

type EodAggregates = {
  closed_trades: number;
  total_realized_pnl: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  exit_reasons: Record<string, number>;
  paper_trades: number;
  live_trades: number;
  avg_entry_vix: number | null;
};

type EodSuggestion = {
  kind: string;
  hint_key: string;
  message: string;
};

type EodPayload = {
  report_date_ist: string;
  strategy_id: string;
  strategy_version: string;
  aggregates: EodAggregates;
  suggestions: EodSuggestion[];
};

type EodReportRow = {
  report_date_ist: string;
  strategy_id: string;
  strategy_version: string;
  payload: EodPayload | string | Record<string, unknown>;
  created_at: string | null;
};

function todayIstYmd(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: APP_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const y = parts.find((p) => p.type === "year")?.value;
  const m = parts.find((p) => p.type === "month")?.value;
  const d = parts.find((p) => p.type === "day")?.value;
  if (y && m && d) return `${y}-${m}-${d}`;
  return new Date().toISOString().slice(0, 10);
}

function parsePayload(raw: EodReportRow["payload"]): EodPayload | null {
  if (raw == null) return null;
  if (typeof raw === "string") {
    try {
      return JSON.parse(raw) as EodPayload;
    } catch {
      return null;
    }
  }
  if (typeof raw === "object" && "aggregates" in raw) {
    return raw as EodPayload;
  }
  return null;
}

function rowKey(r: EodReportRow): string {
  return `${r.report_date_ist}|${r.strategy_id}|${r.strategy_version}`;
}

export default function AdminStrategyEodPage() {
  const [filterDate, setFilterDate] = useState("");
  const [filterStrategyId, setFilterStrategyId] = useState("");
  const [limit, setLimit] = useState(90);
  const [rows, setRows] = useState<EodReportRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [recomputeDate, setRecomputeDate] = useState(todayIstYmd);
  const [recomputeBusy, setRecomputeBusy] = useState(false);
  const [recomputeMsg, setRecomputeMsg] = useState("");

  const loadReports = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const q: Record<string, string | number | undefined> = { limit };
      if (filterDate.trim()) q.report_date = filterDate.trim();
      if (filterStrategyId.trim()) q.strategy_id = filterStrategyId.trim();
      const data = await apiJson<EodReportRow[]>("/api/admin/strategy-eod-reports", "GET", undefined, q);
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      setRows([]);
      setError(e instanceof Error ? e.message : "Failed to load reports.");
    } finally {
      setLoading(false);
    }
  }, [filterDate, filterStrategyId, limit]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setError("");
      setLoading(true);
      try {
        const data = await apiJson<EodReportRow[]>("/api/admin/strategy-eod-reports", "GET", undefined, {
          limit: 90,
        });
        if (!cancelled) setRows(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) {
          setRows([]);
          setError(e instanceof Error ? e.message : "Failed to load reports.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = useMemo(() => {
    if (!selectedKey) return null;
    return rows.find((r) => rowKey(r) === selectedKey) ?? null;
  }, [rows, selectedKey]);

  const selectedPayload = selected ? parsePayload(selected.payload) : null;

  const runRecompute = async () => {
    if (!recomputeDate) return;
    setRecomputeMsg("");
    setRecomputeBusy(true);
    try {
      const res = await apiJson<{ report_date_ist: string; rows_upserted: number }>(
        "/api/admin/strategy-eod-reports/run",
        "POST",
        undefined,
        { report_date: recomputeDate },
      );
      setRecomputeMsg(`Upserted ${res.rows_upserted} row(s) for ${res.report_date_ist} (IST).`);
      await loadReports();
    } catch (e) {
      setRecomputeMsg(e instanceof Error ? e.message : "Recompute failed.");
    } finally {
      setRecomputeBusy(false);
    }
  };

  return (
    <AdminGuard>
      <AppFrame
        title="Strategy EOD reports"
        subtitle="Per-day aggregates for closed trades by strategy version (IST calendar date). Stored in s004_strategy_eod_reports."
      >
        <div className="page-section">
          <p className="muted">
            Automatic upserts after the cash session use{" "}
            <code className="strategy-eod-code">S004_STRATEGY_EOD_REPORT_ENABLED</code> on the backend. Use{" "}
            <strong>Recompute</strong> below to build or refresh rows for any IST date regardless of that flag.
          </p>
          {error && <div className="notice warning">{error}</div>}
          {recomputeMsg && (
            <div className={`notice ${recomputeMsg.includes("failed") || recomputeMsg.includes("Failed") ? "warning" : "info"}`}>
              {recomputeMsg}
            </div>
          )}
        </div>

        <div className="page-section panel evolution-panel">
          <h2>Filters</h2>
          <div className="evolution-toolbar strategy-eod-toolbar">
            <label className="evolution-field">
              <span>Report date (IST)</span>
              <input
                type="date"
                className="control-input"
                value={filterDate}
                onChange={(e) => setFilterDate(e.target.value)}
                aria-label="Filter by report date"
              />
            </label>
            <label className="evolution-field">
              <span>Strategy ID</span>
              <input
                type="text"
                className="control-input"
                value={filterStrategyId}
                onChange={(e) => setFilterStrategyId(e.target.value)}
                placeholder="Optional"
              />
            </label>
            <label className="evolution-field">
              <span>Limit</span>
              <input
                type="number"
                className="control-input strategy-eod-limit"
                min={1}
                max={500}
                value={limit}
                onChange={(e) => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 90)))}
              />
            </label>
            <button type="button" className="primary-button" disabled={loading} onClick={() => void loadReports()}>
              {loading ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>

        <div className="page-section panel evolution-panel">
          <h2>Recompute for IST date</h2>
          <p className="muted">Rebuilds aggregates from closed trades on that calendar day in Asia/Kolkata.</p>
          <div className="evolution-toolbar strategy-eod-toolbar">
            <label className="evolution-field">
              <span>Date</span>
              <input
                type="date"
                className="control-input"
                value={recomputeDate}
                onChange={(e) => setRecomputeDate(e.target.value)}
              />
            </label>
            <button type="button" className="toggle-button" disabled={recomputeBusy || !recomputeDate} onClick={() => void runRecompute()}>
              {recomputeBusy ? "Running…" : "Run EOD aggregation"}
            </button>
          </div>
        </div>

        <div className="page-section panel evolution-panel">
          <h2>Stored reports ({rows.length})</h2>
          {!loading && rows.length === 0 && <p className="muted">No rows yet. Run recompute for a day with closed trades, or widen filters.</p>}
          {rows.length > 0 && (
            <div className="strategy-eod-table-wrap">
              <table className="strategy-eod-table">
                <thead>
                  <tr>
                    <th>Date (IST)</th>
                    <th>Strategy</th>
                    <th>Version</th>
                    <th className="num">Closed</th>
                    <th className="num">P&amp;L</th>
                    <th className="num">Win %</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const p = parsePayload(r.payload);
                    const a = p?.aggregates;
                    const k = rowKey(r);
                    const active = selectedKey === k;
                    return (
                      <tr key={k} className={active ? "strategy-eod-row-active" : undefined}>
                        <td>{r.report_date_ist}</td>
                        <td>{r.strategy_id}</td>
                        <td>{r.strategy_version}</td>
                        <td className="num">{a?.closed_trades ?? "—"}</td>
                        <td className="num">{a != null ? `₹${a.total_realized_pnl.toLocaleString("en-IN")}` : "—"}</td>
                        <td className="num">{a != null ? `${a.win_rate_pct}%` : "—"}</td>
                        <td>
                          <button type="button" className="toggle-button strategy-eod-detail-btn" onClick={() => setSelectedKey(active ? null : k)}>
                            {active ? "Hide detail" : "Detail"}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {selected && selectedPayload && (
            <div className="strategy-eod-detail">
              <h3>
                Detail · {selected.strategy_id} {selected.strategy_version} · {selected.report_date_ist}
              </h3>
              {selected.created_at && (
                <p className="muted">Row updated {selected.created_at}</p>
              )}
              <div className="strategy-eod-detail-grid">
                <div>
                  <h4>Aggregates</h4>
                  <ul className="strategy-eod-kv">
                    <li>
                      <span>Closed trades</span> <strong>{selectedPayload.aggregates.closed_trades}</strong>
                    </li>
                    <li>
                      <span>Total realized P&amp;L</span>{" "}
                      <strong>₹{selectedPayload.aggregates.total_realized_pnl.toLocaleString("en-IN")}</strong>
                    </li>
                    <li>
                      <span>Wins / losses</span>{" "}
                      <strong>
                        {selectedPayload.aggregates.wins} / {selectedPayload.aggregates.losses}
                      </strong>
                    </li>
                    <li>
                      <span>Paper / live</span>{" "}
                      <strong>
                        {selectedPayload.aggregates.paper_trades} / {selectedPayload.aggregates.live_trades}
                      </strong>
                    </li>
                    <li>
                      <span>Avg entry VIX</span>{" "}
                      <strong>
                        {selectedPayload.aggregates.avg_entry_vix != null
                          ? selectedPayload.aggregates.avg_entry_vix
                          : "—"}
                      </strong>
                    </li>
                  </ul>
                </div>
                <div>
                  <h4>Exit reasons</h4>
                  {Object.keys(selectedPayload.aggregates.exit_reasons || {}).length === 0 ? (
                    <p className="muted">—</p>
                  ) : (
                    <ul className="strategy-eod-exits">
                      {Object.entries(selectedPayload.aggregates.exit_reasons).map(([code, n]) => (
                        <li key={code}>
                          <code>{code}</code> <span className="muted">×{n}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
              <h4>Suggestions</h4>
              {selectedPayload.suggestions.length === 0 ? (
                <p className="muted">
                  {selectedPayload.aggregates.closed_trades < 5 ? (
                    <>
                      None yet — rule-based hints need at least <strong>5 closed trades</strong> on this IST day for
                      this strategy version (this row shows {selectedPayload.aggregates.closed_trades}). Re-run EOD
                      after more exits, or check you are viewing the correct date and version.
                    </>
                  ) : (
                    <>
                      No suggestions in stored payload (try <strong>Run EOD aggregation</strong> again for this date
                      to refresh). If you still see this after refresh, contact support — data may be from an older
                      backend build.
                    </>
                  )}
                </p>
              ) : (
                <ul className="strategy-eod-suggestions">
                  {selectedPayload.suggestions.map((s, i) => (
                    <li key={`${s.hint_key}-${i}`}>
                      <span className="strategy-eod-sug-meta">
                        {s.kind} · <code>{s.hint_key}</code>
                      </span>
                      <p>{s.message}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          {selected && !selectedPayload && (
            <div className="notice warning">Could not parse payload JSON for this row.</div>
          )}
        </div>
      </AppFrame>
    </AdminGuard>
  );
}
