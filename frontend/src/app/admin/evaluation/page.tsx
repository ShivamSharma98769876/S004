"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
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

type CatalogMeta = {
  strategy_id: string;
  version: string | null;
  display_name: string | null;
  description: string | null;
  risk_profile: string | null;
  publish_status: string | null;
  strategy_family: string;
};

type Analytics = {
  equity_dates: string[];
  equity_values: number[];
  drawdown_dates: string[];
  drawdown_values: number[];
  max_drawdown_abs: number;
  max_drawdown_pct: number;
  sharpe_daily_pnl_proxy: number | null;
  data_quality: {
    calendar_days: number;
    days_with_any_row: number;
    closed_trades_window: number;
    sharpe_reliable: boolean;
  };
};

type RegimeBundle = {
  regime_label: string;
  volatility_bucket: string;
  pnl_tone: string;
  strategy_fit: string;
  hint: string;
};

type RecRow = {
  id: number;
  strategy_id: string;
  from_version: string;
  recommendation_code: string;
  proposed_title: string;
  status: string;
  created_at?: string;
  rationale_json?: Record<string, unknown>;
};

type RepoRow = {
  strategy_id: string;
  version: string;
  display_name: string;
  description: string | null;
  risk_profile: string;
  publish_status: string;
};

type WorkbenchPayload = {
  strategy_id: string;
  strategy_version: string | null;
  resolved_catalog_version?: string;
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
  catalog_meta: CatalogMeta;
  analytics: Analytics;
  regime: RegimeBundle;
  recent_recommendations: RecRow[];
};

type Phase = "overview" | "performance" | "regime" | "optimize";

function buildLinePath(values: number[], width = 100, height = 100): string {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const pad = Math.max((max - min) * 0.12, 1e-6);
  const lo = min - pad;
  const hi = max + pad;
  const span = hi - lo;
  const n = values.length;
  const xn = (i: number) => (i / (n - 1)) * width;
  const yn = (v: number) => height - ((v - lo) / span) * height;
  return values.map((v, i) => `${i === 0 ? "M" : "L"} ${xn(i).toFixed(2)} ${yn(v).toFixed(2)}`).join(" ");
}

function EvalLineChart({ title, values, stroke }: { title: string; values: number[]; stroke: string }) {
  const gid = useId().replace(/:/g, "");
  if (values.length < 2) {
    return (
      <div className="eval-chart-box">
        <h3>{title}</h3>
        <p className="muted" style={{ margin: "0.5rem 0", fontSize: 13 }}>
          Need at least two calendar days in range.
        </p>
      </div>
    );
  }
  const line = buildLinePath(values, 100, 100);
  const area = `${line} L 100 100 L 0 100 Z`;
  const gradId = `evalFill-${gid}`;
  return (
    <div className="eval-chart-box">
      <h3>{title}</h3>
      <svg className="eval-chart-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.25" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradId})`} />
        <path d={line} fill="none" stroke={stroke} strokeWidth="1.4" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

export default function AdminEvaluationPage() {
  const [repoRows, setRepoRows] = useState<RepoRow[]>([]);
  const [repoSearch, setRepoSearch] = useState("");
  const [strategyIds, setStrategyIds] = useState<string[]>([]);
  const [strategyId, setStrategyId] = useState("strat-trendpulse-z");
  const [versionFilter, setVersionFilter] = useState<string>("");
  const [versions, setVersions] = useState<{ version: string; publish_status: string }[]>([]);
  const [days, setDays] = useState(30);
  const [wb, setWb] = useState<WorkbenchPayload | null>(null);
  const [phase, setPhase] = useState<Phase>("overview");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [optBusy, setOptBusy] = useState(false);

  const loadRepository = useCallback(async () => {
    const r = await apiJson<{ strategies: RepoRow[] }>("/api/admin/evolution/strategy-repository", "GET");
    setRepoRows(r.strategies || []);
    const ids = await apiJson<{ strategy_ids: string[] }>("/api/admin/evolution/strategies", "GET");
    setStrategyIds(ids.strategy_ids || []);
    setStrategyId((prev) => (ids.strategy_ids?.includes(prev) ? prev : ids.strategy_ids?.[0] || prev));
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

  const loadWorkbench = useCallback(async () => {
    if (!strategyId) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiJson<WorkbenchPayload>("/api/admin/evolution/evaluation-workbench", "GET", undefined, {
        strategy_id: strategyId,
        strategy_version: versionFilter || undefined,
        days: String(days),
      });
      setWb(r);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Failed to load evaluation workbench");
      setWb(null);
    } finally {
      setBusy(false);
    }
  }, [strategyId, versionFilter, days]);

  useEffect(() => {
    loadRepository().catch(() => {});
  }, [loadRepository]);

  useEffect(() => {
    loadVersions().catch(() => {});
  }, [loadVersions]);

  useEffect(() => {
    loadWorkbench().catch(() => {});
  }, [loadWorkbench]);

  const filteredRepo = useMemo(() => {
    const q = repoSearch.trim().toLowerCase();
    if (!q) return repoRows;
    return repoRows.filter(
      (r) =>
        r.strategy_id.toLowerCase().includes(q) ||
        (r.display_name || "").toLowerCase().includes(q) ||
        (r.risk_profile || "").toLowerCase().includes(q),
    );
  }, [repoRows, repoSearch]);

  const pfHint = useMemo(() => {
    const pf = wb?.avg_daily_profit_factor;
    if (pf == null) return "Profit factor appears when daily metrics include gross win/loss (recompute after closed trades).";
    if (pf >= 1.2) return "Trailing window profit factor looks healthy — document regime assumptions before tightening.";
    if (pf < 1.0) return "Profit factor below 1 — review gates, stops, and session filters before scaling size.";
    return "Marginal edge — finetune thresholds using daily breakdown and Evolution recommendations.";
  }, [wb]);

  const runOptimization = async () => {
    if (!strategyId) return;
    setOptBusy(true);
    setMsg(null);
    try {
      const res = await apiJson<{ new_recommendation_ids: number[] }>(
        "/api/admin/evolution/recommendations/generate",
        "POST",
        undefined,
        { strategy_id: strategyId },
      );
      const n = res.new_recommendation_ids?.length ?? 0;
      setMsg(n ? `Generated ${n} new recommendation(s). Review below or on Evolution.` : "No new rules fired (thresholds or duplicates).");
      await loadWorkbench();
      setPhase("optimize");
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Optimization run failed");
    } finally {
      setOptBusy(false);
    }
  };

  const fitClass =
    wb?.regime?.strategy_fit === "aligned"
      ? "eval-regime-pill--fit-aligned"
      : wb?.regime?.strategy_fit === "caution"
        ? "eval-regime-pill--fit-caution"
        : "eval-regime-pill--fit-neutral";

  return (
    <AdminGuard>
      <AppFrame
        title="Strategy evaluation"
        subtitle="Phase 1–2 workbench: repository, performance analytics, regime fit, and rule-based optimization (single API round-trip)."
      >
        <div className="page-section">
          {msg && <div className="notice info">{msg}</div>}

          <div className="eval-repo-panel">
            <strong style={{ fontSize: "0.9rem" }}>Strategy repository</strong>
            <p className="muted" style={{ margin: "0.35rem 0 0.5rem", fontSize: 12 }}>
              Search by id, display name, or risk. Selecting a chip updates the workbench (same data as catalog).
            </p>
            <input
              type="search"
              className="eval-repo-search"
              placeholder="Filter strategies…"
              value={repoSearch}
              onChange={(e) => setRepoSearch(e.target.value)}
              aria-label="Filter strategy repository"
            />
            <div className="eval-repo-list" role="listbox" aria-label="Strategies">
              {filteredRepo.map((r) => (
                <button
                  key={r.strategy_id}
                  type="button"
                  role="option"
                  data-active={strategyId === r.strategy_id ? "true" : "false"}
                  className="eval-repo-chip"
                  title={r.description || r.strategy_id}
                  onClick={() => {
                    setStrategyId(r.strategy_id);
                    setVersionFilter("");
                  }}
                >
                  {r.display_name || r.strategy_id}
                  <span className="muted" style={{ fontWeight: 500 }}>
                    {" "}
                    · {r.risk_profile}
                  </span>
                </button>
              ))}
            </div>
          </div>

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
            <button type="button" className="primary-button" disabled={busy || !strategyId} onClick={() => loadWorkbench()}>
              {busy ? "Loading…" : "Refresh"}
            </button>
          </div>
        </div>

        <div className="eval-workbench-phase-tabs" role="tablist" aria-label="Evaluation sections">
          {(
            [
              ["overview", "Overview"],
              ["performance", "Performance"],
              ["regime", "Regime & fit"],
              ["optimize", "Optimize"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              data-active={phase === key ? "true" : "false"}
              aria-selected={phase === key}
              onClick={() => setPhase(key)}
            >
              {label}
            </button>
          ))}
        </div>

        {phase === "overview" && (
          <div className="page-section evolution-grid">
            <section className="panel evolution-panel">
              <h2>Catalog snapshot</h2>
              {wb?.catalog_meta ? (
                <div className="eval-catalog-card">
                  <div>
                    <strong>{wb.catalog_meta.display_name || wb.catalog_meta.strategy_id}</strong>
                    <span className="muted"> · v{wb.catalog_meta.version ?? "—"}</span>
                  </div>
                  <div className="muted" style={{ marginTop: 6, fontSize: 12 }}>
                    Family: <strong>{wb.catalog_meta.strategy_family}</strong> · Risk: {wb.catalog_meta.risk_profile ?? "—"} ·{" "}
                    {wb.catalog_meta.publish_status ?? "—"}
                  </div>
                  {wb.catalog_meta.description ? (
                    <p style={{ margin: "0.5rem 0 0", fontSize: 13 }}>{wb.catalog_meta.description}</p>
                  ) : null}
                </div>
              ) : (
                <p className="muted">Load workbench to see catalog metadata.</p>
              )}
              <h2 style={{ marginTop: "1.25rem" }}>
                Aggregate ({wb?.from_date_ist} → {wb?.to_date_ist})
              </h2>
              {wb ? (
                <div className="evaluation-kpi-grid">
                  <div className="evaluation-kpi">
                    <div className="summary-label">Closed trades</div>
                    <div className="summary-value">{wb.closed_trades}</div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">Win rate</div>
                    <div className="summary-value">
                      {wb.aggregate_win_rate_pct != null ? `${wb.aggregate_win_rate_pct.toFixed(1)}%` : "—"}
                    </div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">Realized P&amp;L</div>
                    <div
                      className={
                        wb.total_realized_pnl >= 0 ? "summary-value metric-positive" : "summary-value chip-risk-high"
                      }
                    >
                      ₹{wb.total_realized_pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                    </div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">Avg daily PF</div>
                    <div className="summary-value">{wb.avg_daily_profit_factor ?? "—"}</div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">Sharpe (proxy)</div>
                    <div className="summary-value">
                      {wb.analytics?.sharpe_daily_pnl_proxy != null
                        ? wb.analytics.sharpe_daily_pnl_proxy.toFixed(2)
                        : "—"}
                    </div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">Max DD %</div>
                    <div className="summary-value">{wb.analytics?.max_drawdown_pct ?? "—"}</div>
                  </div>
                  <div className="evaluation-kpi">
                    <div className="summary-label">IST days w/ trades</div>
                    <div className="summary-value">{wb.days_with_closed_trades}</div>
                  </div>
                </div>
              ) : (
                <p className="muted">No data yet. Recompute daily metrics on Evolution after exits.</p>
              )}
              <p className="evaluation-hint muted" style={{ marginTop: 12 }}>
                {pfHint}
              </p>
            </section>

            <section className="panel evolution-panel">
              <h2>Daily breakdown</h2>
              {wb && wb.daily.length > 0 ? (
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
                      {wb.daily.map((row) => (
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
                <p className="muted">No daily rows in range.</p>
              )}
            </section>
          </div>
        )}

        {phase === "performance" && !wb && (
          <div className="page-section panel evolution-panel">
            <p className="muted">Load or refresh the workbench to see charts.</p>
          </div>
        )}

        {phase === "performance" && wb && (
          <div className="page-section panel evolution-panel">
            <h2>Equity &amp; drawdown</h2>
            <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
              Built from cumulative daily realized P&amp;L (IST). Sharpe uses daily P&amp;L as a same-scale proxy — interpret
              with sample size; reliable flag requires ≥15 closed trades.
            </p>
            <div className="eval-chart-grid">
              <EvalLineChart title="Equity (cumulative ₹)" values={wb.analytics.equity_values} stroke="#4f7cff" />
              <EvalLineChart title="Drawdown (₹)" values={wb.analytics.drawdown_values} stroke="#fb7185" />
            </div>
            {wb.analytics.data_quality ? (
              <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
                Window: {wb.analytics.data_quality.calendar_days} days · Closed trades:{" "}
                {wb.analytics.data_quality.closed_trades_window} · Sharpe reliable:{" "}
                {wb.analytics.data_quality.sharpe_reliable ? "yes" : "no"}
              </p>
            ) : null}
          </div>
        )}

        {phase === "regime" && !wb && (
          <div className="page-section">
            <p className="muted">Load or refresh the workbench to see regime fit.</p>
          </div>
        )}

        {phase === "regime" && wb && (
          <div className="page-section">
            <div className="eval-regime-panel">
              <h2 style={{ marginTop: 0 }}>Regime &amp; strategy fit</h2>
              <p className="muted" style={{ fontSize: 13 }}>
                Rule-based read from daily P&amp;L dispersion and tone — not a live market classifier. Use with catalog
                family ({wb.catalog_meta.strategy_family}).
              </p>
              <div className="eval-regime-pills">
                <span className="eval-regime-pill">Regime: {wb.regime.regime_label}</span>
                <span className="eval-regime-pill">Vol: {wb.regime.volatility_bucket}</span>
                <span className="eval-regime-pill">P&amp;L tone: {wb.regime.pnl_tone}</span>
                <span className={`eval-regime-pill ${fitClass}`}>Fit: {wb.regime.strategy_fit}</span>
              </div>
              <p style={{ fontSize: 14, lineHeight: 1.5 }}>{wb.regime.hint}</p>
            </div>
          </div>
        )}

        {phase === "optimize" && (
          <div className="page-section panel evolution-panel">
            <h2>Optimization (rule engine)</h2>
            <p className="muted" style={{ fontSize: 13 }}>
              Runs the same generator as Evolution (trailing 14d rules). Approve or reject patches on the{" "}
              <a href="/admin/evolution">Evolution</a> page. Phase 3 can add walk-forward search and stored run history.
            </p>
            <div className="eval-opt-actions">
              <button type="button" className="primary-button" disabled={optBusy || !strategyId} onClick={() => runOptimization()}>
                {optBusy ? "Running…" : "Run optimization rules"}
              </button>
              <a
                className="primary-button"
                href="/admin/evolution"
                style={{ display: "inline-flex", alignItems: "center", textDecoration: "none", opacity: 0.92 }}
              >
                Open Evolution
              </a>
            </div>
            {wb && wb.recent_recommendations.length > 0 ? (
              <ul className="eval-rec-list">
                {wb.recent_recommendations.map((r) => (
                  <li key={r.id} className="eval-rec-card">
                    <strong>{r.proposed_title}</strong>
                    <div className="eval-rec-meta">
                      #{r.id} · {r.recommendation_code} · {r.status} · v{r.from_version}
                      {r.created_at ? ` · ${r.created_at.slice(0, 19)}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No recommendations yet for this strategy — run rules or trade more to cross thresholds.</p>
            )}
          </div>
        )}

        <div className="page-section">
          <p className="muted" style={{ fontSize: 13 }}>
            Data: <code>s004_strategy_daily_metrics</code> + <code>s004_strategy_catalog</code>. Bundle:{" "}
            <code>GET /api/admin/evolution/evaluation-workbench</code>.
          </p>
        </div>
      </AppFrame>
    </AdminGuard>
  );
}
