"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { apiJson, getCurrentUserId } from "@/lib/api_client";

type MarketOverview = {
  nifty: { spot: number; changePct: number };
  pcr: number | null;
  sentimentLabel: string;
  intradayTrendLabel: string;
};

type OverviewPayload = {
  reportDate: string;
  reportTimezone: string;
  generatedAt: string;
  platform: { trading_paused: boolean; pause_reason: string | null };
  activity: {
    users_with_engine_running: number;
    users_with_master_settings_rows: number;
    users_with_active_strategy_subscription: number;
  };
  market: MarketOverview;
  sentiment: {
    directionLabel?: string | null;
    directionScore?: number | null;
    confidence?: number | null;
    regimeLabel?: string | null;
    drivers?: unknown;
  };
  broker_connected_for_snapshot: boolean;
  auto_execute_criteria_summary: string;
  recommendation_counts_note?: string;
};

type StrategyOutcome = {
  strategy_id: string;
  strategy_version: string;
  display_name: string;
  recommendations: {
    generated: number;
    accepted: number;
    rejected: number;
    skipped: number;
    expired: number;
    avg_confidence_generated: number | null;
    avg_score_generated: number | null;
  };
  trades: {
    trades_total?: number;
    closed_n?: number;
    open_n?: number;
    realized_pnl?: number;
    open_unrealized_pnl?: number;
  };
  commentary: string;
  why_no_trade_hints: string[];
};

type DecisionLogRow = {
  id: number;
  user_id: number;
  username: string;
  occurred_at: string;
  mode: string;
  strategy_id: string;
  strategy_version: string;
  gate_blocked: boolean;
  gate_reason: string | null;
  cycle_summary: string;
  thresholds: {
    auto_trade_score: number | null;
    score_display: number | null;
    min_confidence: number | null;
  };
  gates: Record<string, unknown>;
  market_context: Record<string, unknown>;
  evaluations: unknown[];
  executed_recommendation_ids: unknown[];
};

type OpenTradeRow = {
  trade_ref: string;
  user_id: number;
  username: string;
  strategy_id: string;
  symbol: string;
  mode: string;
  current_state: string;
  unrealized_pnl: number;
  opened_at: string;
  recommendation_id: string;
  reason_code: string | null;
  score_at_entry: number | null;
  confidence_at_entry: number | null;
  entry_market_snapshot: Record<string, unknown>;
};

type HeatmapCell = {
  strategy_id: string;
  bucket: string;
  wins: number;
  total: number;
  win_rate_pct: number | null;
};

type HeatmapBlock = {
  strategies: string[];
  buckets: string[];
  cells: HeatmapCell[];
  bucket_label?: string;
  note?: string;
};

type TodaysAnalysisResponse = {
  overview: OverviewPayload;
  strategies_outcome: StrategyOutcome[];
  improvement_suggestions: string[];
  historical_14d_exit_stats_by_strategy: {
    strategy_id: string;
    wins: number;
    losses: number;
    breakeven: number;
    total: number;
  }[];
  decision_log?: DecisionLogRow[];
  decision_log_note?: string;
  open_trades?: OpenTradeRow[];
  heatmaps?: {
    time_of_day_ist: HeatmapBlock;
    pcr_bucket: HeatmapBlock;
    regime: HeatmapBlock;
    india_vix: HeatmapBlock;
    lookback_days: number;
  };
};

function cellBackground(wr: number | null, n: number): string {
  if (n <= 0) return "var(--surface-2)";
  if (wr == null) return "var(--surface-2)";
  const t = Math.max(0, Math.min(100, wr)) / 100;
  const g = Math.round(40 + t * 120);
  const r = Math.round(180 - t * 140);
  return `rgba(${r},${g},90,0.35)`;
}

function sortedBuckets(buckets: string[], label?: string): string[] {
  if (label?.includes("Hour")) {
    return [...buckets].sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
  }
  const pcrOrder = ["very_low", "low", "neutral", "high", "very_high", "unknown"];
  if (label?.includes("PCR")) {
    return [...buckets].sort((a, b) => pcrOrder.indexOf(a) - pcrOrder.indexOf(b));
  }
  const vixOrder = ["vix_lt_12", "vix_12_18", "vix_18_25", "vix_ge_25", "unknown"];
  if (label?.includes("VIX")) {
    return [...buckets].sort((a, b) => vixOrder.indexOf(a) - vixOrder.indexOf(b));
  }
  return [...buckets].sort();
}

function HeatmapTable({ title, data }: { title: string; data: HeatmapBlock }) {
  const buckets = useMemo(() => sortedBuckets(data.buckets || [], data.bucket_label), [data.buckets, data.bucket_label]);
  const strategies = data.strategies || [];
  if (strategies.length === 0 || buckets.length === 0) {
    return (
      <div className="heatmap-block">
        <h4 className="analysis-h4">{title}</h4>
        <p className="muted" style={{ fontSize: "0.85rem" }}>
          No closed trades in lookback for this slice.
        </p>
        {data.note ? <p className="muted" style={{ fontSize: "0.8rem" }}>{data.note}</p> : null}
      </div>
    );
  }
  const lookup = new Map<string, HeatmapCell>();
  for (const c of data.cells || []) {
    lookup.set(`${c.strategy_id}\t${c.bucket}`, c);
  }
  return (
    <div className="heatmap-block">
      <h4 className="analysis-h4">{title}</h4>
      {data.note ? <p className="muted" style={{ fontSize: "0.78rem", marginBottom: "0.5rem" }}>{data.note}</p> : null}
      <div className="table-wrap heatmap-scroll">
        <table className="market-table heatmap-table">
          <thead>
            <tr>
              <th className="heatmap-corner">{data.bucket_label ?? "Bucket"}</th>
              {buckets.map((b) => (
                <th key={b} className="heatmap-th-num">
                  {b}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {strategies.map((sid) => (
              <tr key={sid}>
                <td className="mono heatmap-row-label">{sid}</td>
                {buckets.map((b) => {
                  const c = lookup.get(`${sid}\t${b}`);
                  const n = c?.total ?? 0;
                  const wr = c?.win_rate_pct ?? null;
                  return (
                    <td
                      key={b}
                      className="heatmap-td"
                      style={{ background: cellBackground(wr, n) }}
                      title={n ? `${c?.wins ?? 0}/${n} wins (${wr ?? "—"}%)` : "—"}
                    >
                      {n > 0 ? (
                        <span className="heatmap-cell-inner">
                          <strong>{wr != null ? `${wr}%` : "—"}</strong>
                          <span className="heatmap-n">n={n}</span>
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

async function downloadAnalysisExport(format: "csv" | "pdf"): Promise<void> {
  const uid = getCurrentUserId();
  const res = await fetch(`/api/admin/todays-analysis/export?format=${format}`, {
    headers: { "X-User-Id": String(uid) },
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      if (j?.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = format === "csv" ? "s004-todays-analysis.csv" : "s004-todays-analysis.pdf";
  a.click();
  URL.revokeObjectURL(url);
}

export default function TodaysAnalysisPage() {
  const [data, setData] = useState<TodaysAnalysisResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [exportErr, setExportErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiJson<TodaysAnalysisResponse>("/api/admin/todays-analysis", "GET");
      setData(res);
    } catch (e) {
      setData(null);
      setError(e instanceof Error ? e.message : "Failed to load today’s analysis.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ov = data?.overview;
  const heat = data?.heatmaps;
  const lookback = heat?.lookback_days ?? 90;

  return (
    <AdminGuard>
      <AppFrame
        title="Today’s Analysis"
        subtitle="IST snapshot, factual auto-execute decision log, open-trade ↔ recommendation linkage, and win-rate heatmaps."
      >
        <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap", marginBottom: "1rem", alignItems: "center" }}>
          <button type="button" className="action-button resume" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </button>
          <button
            type="button"
            className="action-button"
            onClick={() => {
              setExportErr(null);
              void downloadAnalysisExport("csv").catch((e) => setExportErr(e instanceof Error ? e.message : "CSV export failed"));
            }}
          >
            Download CSV
          </button>
          <button
            type="button"
            className="action-button"
            onClick={() => {
              setExportErr(null);
              void downloadAnalysisExport("pdf").catch((e) => setExportErr(e instanceof Error ? e.message : "PDF export failed"));
            }}
          >
            Download PDF
          </button>
        </div>
        {exportErr && <div className="notice warning">{exportErr}</div>}
        {error && <div className="notice warning">{error}</div>}

        {loading && !data ? (
          <div className="empty-state">Loading…</div>
        ) : ov ? (
          <>
            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Overview — how was the day</div>
              <p className="muted" style={{ margin: "0 0 0.75rem", fontSize: "0.9rem" }}>
                Report date <strong>{ov.reportDate}</strong> ({ov.reportTimezone}). Snapshot{" "}
                <span className="mono">{ov.generatedAt}</span>
                {ov.broker_connected_for_snapshot ? "" : " — NIFTY/PCR need a connected broker (shared API)."}
              </p>
              {ov.recommendation_counts_note ? (
                <p className="muted" style={{ fontSize: "0.82rem", marginBottom: "0.65rem" }}>
                  {ov.recommendation_counts_note}
                </p>
              ) : null}
              <div className="analysis-grid">
                <div className="analysis-stat">
                  <span className="analysis-stat-label">NIFTY</span>
                  <span className="analysis-stat-value">
                    {ov.market.nifty.spot.toFixed(2)}{" "}
                    <span className={ov.market.nifty.changePct >= 0 ? "analysis-pos" : "analysis-neg"}>
                      ({ov.market.nifty.changePct >= 0 ? "+" : ""}
                      {ov.market.nifty.changePct.toFixed(2)}%)
                    </span>
                  </span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">PCR</span>
                  <span className="analysis-stat-value">{ov.market.pcr != null ? ov.market.pcr.toFixed(2) : "—"}</span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">Sentiment (label)</span>
                  <span className="analysis-stat-value">{ov.market.sentimentLabel}</span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">Intraday trend</span>
                  <span className="analysis-stat-value">{ov.market.intradayTrendLabel}</span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">Direction</span>
                  <span className="analysis-stat-value">{ov.sentiment.directionLabel ?? "—"}</span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">Regime</span>
                  <span className="analysis-stat-value">{ov.sentiment.regimeLabel ?? "—"}</span>
                </div>
                <div className="analysis-stat">
                  <span className="analysis-stat-label">Confidence</span>
                  <span className="analysis-stat-value">
                    {ov.sentiment.confidence != null ? `${ov.sentiment.confidence}` : "—"}
                  </span>
                </div>
              </div>
              {ov.sentiment.drivers != null && (
                <details style={{ marginTop: "0.75rem" }}>
                  <summary style={{ cursor: "pointer", fontSize: "0.9rem" }}>Sentiment drivers (detail)</summary>
                  <pre className="analysis-pre">{JSON.stringify(ov.sentiment.drivers, null, 2)}</pre>
                </details>
              )}
              <div className="analysis-subsection">
                <h4 className="analysis-h4">Platform &amp; activity</h4>
                <ul className="analysis-list">
                  <li>
                    Trading paused:{" "}
                    <strong className={ov.platform.trading_paused ? "analysis-neg" : "analysis-pos"}>
                      {ov.platform.trading_paused ? "Yes" : "No"}
                    </strong>
                    {ov.platform.pause_reason ? ` — ${ov.platform.pause_reason}` : ""}
                  </li>
                  <li>
                    Users with engine running: <strong>{ov.activity.users_with_engine_running}</strong> (of{" "}
                    {ov.activity.users_with_master_settings_rows} with settings rows)
                  </li>
                  <li>
                    Users with an active strategy subscription:{" "}
                    <strong>{ov.activity.users_with_active_strategy_subscription}</strong>
                  </li>
                </ul>
                <p className="muted" style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
                  {ov.auto_execute_criteria_summary}
                </p>
              </div>
            </section>

            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Open positions — recommendation at entry</div>
              <p className="muted" style={{ margin: "0 0 0.75rem", fontSize: "0.88rem" }}>
                Joined to <span className="mono">s004_trade_recommendations</span> for <code>reason_code</code>, score, and confidence.
                Entry snapshot (PCR / regime / VIX) appears for trades opened after snapshot logging.
              </p>
              <div className="table-wrap">
                <table className="market-table">
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Symbol</th>
                      <th>Strategy</th>
                      <th>State</th>
                      <th>u.PnL</th>
                      <th>reason_code</th>
                      <th>Score</th>
                      <th>Conf</th>
                      <th>Rec ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.open_trades ?? []).length === 0 ? (
                      <tr>
                        <td colSpan={9} className="empty-state">
                          No open trades.
                        </td>
                      </tr>
                    ) : (
                      (data.open_trades ?? []).map((t) => (
                        <tr key={t.trade_ref}>
                          <td>{t.username}</td>
                          <td className="mono">{t.symbol}</td>
                          <td className="mono">{t.strategy_id}</td>
                          <td>{t.current_state}</td>
                          <td>{t.unrealized_pnl.toFixed(2)}</td>
                          <td className="mono">{t.reason_code ?? "—"}</td>
                          <td>{t.score_at_entry ?? "—"}</td>
                          <td>{t.confidence_at_entry != null ? t.confidence_at_entry.toFixed(1) : "—"}</td>
                          <td className="mono muted" style={{ fontSize: "0.75rem" }}>
                            {t.recommendation_id
                              ? `${t.recommendation_id.slice(0, 12)}…`
                              : "—"}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Auto-execute decision log (today, IST)</div>
              <p className="muted" style={{ margin: "0 0 0.75rem", fontSize: "0.88rem" }}>
                {data.decision_log_note ??
                  "Rows include gate reasons, numeric thresholds, and per-recommendation eligibility (≈ one sample per user per 50s when the engine runs)."}
              </p>
              <div className="table-wrap decision-log-scroll">
                <table className="market-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>User</th>
                      <th>Strategy</th>
                      <th>Summary</th>
                      <th>Gate</th>
                      <th>Thresholds</th>
                      <th>Evaluations</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(data.decision_log ?? []).length === 0 ? (
                      <tr>
                        <td colSpan={7} className="empty-state">
                          No rows yet. Run DB migration <span className="mono">trade_decision_log_schema.sql</span> and keep engines on
                          during market hours.
                        </td>
                      </tr>
                    ) : (
                      (data.decision_log ?? []).map((row) => (
                        <tr key={row.id}>
                          <td className="mono" style={{ fontSize: "0.78rem", whiteSpace: "nowrap" }}>
                            {row.occurred_at.replace("T", " ").replace("+00:00", "Z").slice(0, 19)}
                          </td>
                          <td>{row.username}</td>
                          <td className="mono">
                            {row.strategy_id}
                            <br />
                            <span className="muted">{row.strategy_version}</span>
                          </td>
                          <td>{row.cycle_summary}</td>
                          <td>{row.gate_blocked ? row.gate_reason ?? "blocked" : "—"}</td>
                          <td className="mono" style={{ fontSize: "0.75rem" }}>
                            auto≥{row.thresholds.auto_trade_score ?? "—"} disp≥{row.thresholds.score_display ?? "—"} conf≥
                            {row.thresholds.min_confidence ?? "—"}
                          </td>
                          <td>
                            <details>
                              <summary style={{ cursor: "pointer", fontSize: "0.8rem" }}>
                                {(row.evaluations as { eligible_for_auto_execute?: boolean }[]).length} legs
                              </summary>
                              <pre className="analysis-pre" style={{ maxHeight: 160, marginTop: 6 }}>
                                {JSON.stringify(row.evaluations, null, 2)}
                              </pre>
                            </details>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Win-rate heatmaps (closed trades, last {lookback} days)</div>
              <p className="muted" style={{ margin: "0 0 1rem", fontSize: "0.88rem" }}>
                Cell = win rate % and sample size. Greener = higher win rate. PCR / regime / VIX buckets use{" "}
                <span className="mono">entry_market_snapshot</span> when present; older trades may show only time-of-day.
              </p>
              <div className="heatmap-grid">
                {heat ? (
                  <>
                    <HeatmapTable title="Time of day (IST hour opened)" data={heat.time_of_day_ist} />
                    <HeatmapTable title="PCR bucket at entry" data={heat.pcr_bucket} />
                    <HeatmapTable title="Regime at entry" data={heat.regime} />
                    <HeatmapTable title="India VIX at entry" data={heat.india_vix} />
                  </>
                ) : (
                  <p className="muted">Heatmaps unavailable.</p>
                )}
              </div>
            </section>

            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Strategies outcome</div>
              <p className="muted" style={{ margin: "0 0 1rem", fontSize: "0.9rem" }}>
                Trades use <span className="mono">opened_at</span> in today’s IST window. Recommendation status counts use{" "}
                <span className="mono">updated_at</span> in that window. “Why missing” lines prefer the decision log when available.
              </p>
              {data.strategies_outcome.length === 0 ? (
                <div className="empty-state">No catalog strategies and no activity recorded for today.</div>
              ) : (
                <div className="strategy-outcome-stack">
                  {data.strategies_outcome.map((s) => (
                    <article key={`${s.strategy_id}-${s.strategy_version}`} className="strategy-outcome-card">
                      <header className="strategy-outcome-head">
                        <h3>{s.display_name}</h3>
                        <span className="mono muted">
                          {s.strategy_id} · {s.strategy_version}
                        </span>
                      </header>
                      <p className="strategy-outcome-commentary">{s.commentary}</p>
                      <div className="analysis-grid compact">
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">Recs GENERATED</span>
                          <span className="analysis-stat-value">{s.recommendations.generated}</span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">ACCEPTED</span>
                          <span className="analysis-stat-value">{s.recommendations.accepted}</span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">REJECTED / SKIPPED / EXPIRED</span>
                          <span className="analysis-stat-value">
                            {s.recommendations.rejected} / {s.recommendations.skipped} / {s.recommendations.expired}
                          </span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">Avg conf (generated)</span>
                          <span className="analysis-stat-value">
                            {s.recommendations.avg_confidence_generated != null
                              ? Number(s.recommendations.avg_confidence_generated).toFixed(1)
                              : "—"}
                          </span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">Avg score (generated)</span>
                          <span className="analysis-stat-value">
                            {s.recommendations.avg_score_generated != null
                              ? Number(s.recommendations.avg_score_generated).toFixed(2)
                              : "—"}
                          </span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">Trades opened today</span>
                          <span className="analysis-stat-value">{s.trades.trades_total ?? 0}</span>
                        </div>
                        <div className="analysis-stat">
                          <span className="analysis-stat-label">Realized / open u.PnL</span>
                          <span className="analysis-stat-value">
                            {(s.trades.realized_pnl ?? 0).toFixed(2)} / {(s.trades.open_unrealized_pnl ?? 0).toFixed(2)}
                          </span>
                        </div>
                      </div>
                      {s.why_no_trade_hints.length > 0 && (
                        <div className="why-no-trade">
                          <span className="why-no-trade-title">Why trades might be missing</span>
                          <ul>
                            {s.why_no_trade_hints.map((h, i) => (
                              <li key={i}>{h}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </section>

            <section className="table-card" style={{ marginBottom: "1.25rem" }}>
              <div className="panel-title settings-panel-title">Improvement (from recent history)</div>
              <ul className="analysis-list">
                {data.improvement_suggestions.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
              {data.historical_14d_exit_stats_by_strategy.length > 0 && (
                <div className="table-wrap" style={{ marginTop: "1rem" }}>
                  <table className="market-table">
                    <thead>
                      <tr>
                        <th>Strategy</th>
                        <th>14d closed</th>
                        <th>Wins</th>
                        <th>Losses</th>
                        <th>Flat</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.historical_14d_exit_stats_by_strategy.map((r) => (
                        <tr key={r.strategy_id}>
                          <td className="mono">{r.strategy_id}</td>
                          <td>{r.total}</td>
                          <td>{r.wins}</td>
                          <td>{r.losses}</td>
                          <td>{r.breakeven}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        ) : null}
      </AppFrame>
    </AdminGuard>
  );
}
