"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { apiJson } from "@/lib/api_client";

type DailyEvalRow = {
  trade_date_ist: string;
  closed_trades: number;
  realized_pnl: number;
  win_rate_pct: number | null;
  metrics_json: { profit_factor?: number; largest_win?: number; largest_loss?: number };
};

type EvalSummary = {
  strategy_id: string;
  strategy_version: string | null;
  window_days: number;
  from_date_ist: string;
  to_date_ist: string;
  closed_trades: number;
  winning_trades: number;
  losing_trades: number;
  aggregate_win_rate_pct: number | null;
  total_realized_pnl: number;
  avg_daily_profit_factor: number | null;
  days_with_closed_trades: number;
  daily: DailyEvalRow[];
};

export default function AdminEvaluationPage() {
  const [strategyIds, setStrategyIds] = useState<string[]>([]);
  const [strategyId, setStrategyId] = useState("strat-trendpulse-z");
  const [versionFilter, setVersionFilter] = useState<string>("");
  const [versions, setVersions] = useState<{ version: string; publish_status: string }[]>([]);
  const [days, setDays] = useState(30);
  const [summary, setSummary] = useState<EvalSummary | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadStrategies = useCallback(async () => {
    const r = await apiJson<{ strategy_ids: string[] }>("/api/admin/evolution/strategies", "GET");
    setStrategyIds(r.strategy_ids || []);
    setStrategyId((prev) => (r.strategy_ids?.includes(prev) ? prev : r.strategy_ids?.[0] || "strat-trendpulse-z"));
  }, []);

  const loadVersions = useCallback(async () => {
    if (!strategyId) {
      setVersions([]);
      return;
    }
    const r = await apiJson<{ versions: { version: string; publish_status: string }[] }>(
      `/api/admin/evolution/strategies/${encodeURIComponent(strategyId)}/versions`,
      "GET"
    );
    setVersions(r.versions || []);
  }, [strategyId]);

  const loadSummary = useCallback(async () => {
    if (!strategyId) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiJson<EvalSummary>("/api/admin/evolution/evaluation-summary", "GET", undefined, {
        strategy_id: strategyId,
        strategy_version: versionFilter || undefined,
        days: String(days),
      });
      setSummary(r);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed to load evaluation summary");
      setSummary(null);
    } finally {
      setBusy(false);
    }
  }, [strategyId, versionFilter, days]);

  useEffect(() => {
    loadStrategies().catch(() => {});
  }, [loadStrategies]);

  useEffect(() => {
    loadVersions().catch(() => {});
  }, [loadVersions]);

  useEffect(() => {
    loadSummary().catch(() => {});
  }, [loadSummary]);

  const pfHint = useMemo(() => {
    const pf = summary?.avg_daily_profit_factor;
    if (pf == null) return "Profit factor appears when daily metrics include gross win/loss (recompute after closed trades).";
    if (pf >= 1.2) return "Trailing window profit factor looks healthy — document regime assumptions before tightening.";
    if (pf < 1.0) return "Profit factor below 1 — review Tier-1/Tier-2 gates, stops, and session filters before scaling size.";
    return "Marginal edge — finetune delta band, extrinsic share, or liquidity thresholds using daily breakdown below.";
  }, [summary]);

  return (
    <AdminGuard>
      <AppFrame
        title="Strategy evaluation"
        subtitle="Daily performance rollups from closed LIVE trades — use with Evolution metrics to finetune TrendPulse Z and other strategies."
      >
        <div className="page-section">
          {msg && <div className="notice warning">{msg}</div>}
          <div className="evolution-toolbar">
            <label className="evolution-field">
              <span>Strategy</span>
              <select
                value={strategyId}
                onChange={(e) => {
                  setStrategyId(e.target.value);
                  setVersionFilter("");
                }}
              >
                {strategyIds.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            <label className="evolution-field">
              <span>Version</span>
              <select value={versionFilter} onChange={(e) => setVersionFilter(e.target.value)}>
                <option value="">All versions (combined)</option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version} ({v.publish_status})
                  </option>
                ))}
              </select>
            </label>
            <label className="evolution-field">
              <span>Days</span>
              <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                {[7, 14, 30, 60, 90].map((d) => (
                  <option key={d} value={d}>
                    {d}d
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="primary-button" disabled={busy || !strategyId} onClick={() => loadSummary()}>
              {busy ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>

        <div className="page-section evolution-grid">
          <section className="panel evolution-panel">
            <h2>Aggregate ({summary?.from_date_ist} → {summary?.to_date_ist})</h2>
            {summary ? (
              <div className="evaluation-kpi-grid">
                <div className="evaluation-kpi">
                  <div className="summary-label">Closed trades</div>
                  <div className="summary-value">{summary.closed_trades}</div>
                </div>
                <div className="evaluation-kpi">
                  <div className="summary-label">Win rate</div>
                  <div className="summary-value">
                    {summary.aggregate_win_rate_pct != null ? `${summary.aggregate_win_rate_pct.toFixed(1)}%` : "—"}
                  </div>
                </div>
                <div className="evaluation-kpi">
                  <div className="summary-label">Realized P&amp;L</div>
                  <div className={summary.total_realized_pnl >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"}>
                    ₹{summary.total_realized_pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                  </div>
                </div>
                <div className="evaluation-kpi">
                  <div className="summary-label">Avg daily PF</div>
                  <div className="summary-value">{summary.avg_daily_profit_factor ?? "—"}</div>
                </div>
                <div className="evaluation-kpi">
                  <div className="summary-label">IST days with trades</div>
                  <div className="summary-value">{summary.days_with_closed_trades}</div>
                </div>
              </div>
            ) : (
              <p className="muted">No data yet. Run <strong>Recompute daily metrics</strong> on the Evolution page after exits hit the book.</p>
            )}
            <p className="evaluation-hint muted" style={{ marginTop: 12 }}>
              {pfHint}
            </p>
          </section>

          <section className="panel evolution-panel">
            <h2>Daily breakdown</h2>
            {summary && summary.daily.length > 0 ? (
              <div className="table-wrap">
                <table className="market-table">
                  <thead>
                    <tr>
                      <th>Date (IST)</th>
                      <th>Closed</th>
                      <th>Win %</th>
                      <th>Realized</th>
                      <th>PF</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summary.daily.map((row) => (
                      <tr key={row.trade_date_ist}>
                        <td>{row.trade_date_ist}</td>
                        <td>{row.closed_trades}</td>
                        <td>{row.win_rate_pct != null ? `${row.win_rate_pct.toFixed(1)}%` : "—"}</td>
                        <td className={row.realized_pnl >= 0 ? "metric-positive" : "chip-risk-high"}>
                          ₹{row.realized_pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                        </td>
                        <td>
                          {row.metrics_json?.profit_factor != null
                            ? Number(row.metrics_json.profit_factor).toFixed(3)
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No daily rows in range. Confirm strategy_id matches trades and recompute metrics.</p>
            )}
          </section>
        </div>

        <div className="page-section">
          <p className="muted" style={{ fontSize: 13 }}>
            Data source: <code>s004_strategy_daily_metrics</code> (LIVE exits, IST day). Pair with{" "}
            <a href="/admin/evolution">Evolution</a> for version charts and rule-based recommendations.
          </p>
        </div>
      </AppFrame>
    </AdminGuard>
  );
}
