"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { apiJson, getAuthHeaders } from "@/lib/api_client";

type DailyRow = {
  strategy_id: string;
  strategy_version: string;
  trade_date_ist: string;
  closed_trades: number;
  winning_trades: number;
  losing_trades: number;
  realized_pnl: number;
  win_rate_pct: number | null;
  cumulative_realized_pnl: number;
  metrics_json: Record<string, unknown>;
};

type RecRow = {
  id: number;
  strategy_id: string;
  from_version: string;
  recommendation_code: string;
  proposed_title: string;
  rationale_json: Record<string, unknown>;
  proposed_details_patch: Record<string, unknown>;
  status: string;
  created_at: string;
  implemented_version: string | null;
};

type ChangelogRow = {
  id: number;
  strategy_id: string;
  from_version: string;
  to_version: string;
  summary: string | null;
  changelog_md: string | null;
  created_at: string;
};

function CumulativeChart({ series }: { series: DailyRow[] }) {
  const { pathD, minY, maxY } = useMemo(() => {
    if (!series.length) return { pathD: "", minY: 0, maxY: 0 };
    const vals = series.map((r) => r.cumulative_realized_pnl);
    const min = Math.min(0, ...vals);
    const max = Math.max(0, ...vals);
    const pad = Math.max(Math.abs(max - min) * 0.08, 1);
    const lo = min - pad;
    const hi = max + pad;
    const w = 640;
    const h = 200;
    const n = series.length;
    const pts = series.map((r, i) => {
      const x = (i / Math.max(n - 1, 1)) * w;
      const y = h - ((r.cumulative_realized_pnl - lo) / (hi - lo || 1)) * h;
      return `${x},${y}`;
    });
    return { pathD: `M ${pts.join(" L ")}`, minY: lo, maxY: hi, w, h };
  }, [series]);

  if (!series.length) {
    return <p className="muted">No daily metrics yet — run recompute after closed trades exist.</p>;
  }

  const w = 640;
  const h = 200;
  return (
    <div className="evolution-chart-wrap">
      <svg
        className="evolution-chart-svg"
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Cumulative realized PnL by day"
      >
        <line x1="0" y1={h / 2} x2={w} y2={h / 2} stroke="var(--border)" strokeWidth="1" strokeDasharray="4 4" />
        <path d={pathD} fill="none" stroke="var(--accent)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="evolution-chart-axis muted">
        <span>Cumulative P&amp;L (₹): min {minY.toFixed(0)} → max {maxY.toFixed(0)}</span>
      </div>
    </div>
  );
}

function DailyBars({ series }: { series: DailyRow[] }) {
  if (!series.length) return null;
  const mx = Math.max(...series.map((s) => Math.abs(Number(s.realized_pnl) || 0)), 1);
  return (
    <div className="evolution-bars" role="list">
      {series.map((r) => {
        const pnl = Number(r.realized_pnl) || 0;
        const pct = (Math.abs(pnl) / mx) * 100;
        const neg = pnl < 0;
        return (
          <div key={`${r.trade_date_ist}-${r.strategy_version}`} className="evolution-bar-row" role="listitem">
            <span className="evolution-bar-date">{r.trade_date_ist}</span>
            <div className="evolution-bar-track">
              <div
                className={`evolution-bar-fill${neg ? " evolution-bar-fill--neg" : ""}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className={`evolution-bar-val${neg ? " neg" : " pos"}`}>{pnl.toFixed(2)}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function AdminEvolutionPage() {
  const [strategyIds, setStrategyIds] = useState<string[]>([]);
  const [strategyId, setStrategyId] = useState("");
  const [versions, setVersions] = useState<{ version: string; publish_status: string }[]>([]);
  const [versionFilter, setVersionFilter] = useState<string>("");
  const [series, setSeries] = useState<DailyRow[]>([]);
  const [recs, setRecs] = useState<RecRow[]>([]);
  const [changelog, setChangelog] = useState<ChangelogRow[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadStrategies = useCallback(async () => {
    const r = await apiJson<{ strategy_ids: string[] }>("/api/admin/evolution/strategies", "GET");
    setStrategyIds(r.strategy_ids || []);
    setStrategyId((prev) => prev || r.strategy_ids?.[0] || "");
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

  const loadSeries = useCallback(async () => {
    if (!strategyId) {
      setSeries([]);
      return;
    }
    const r = await apiJson<{ series: DailyRow[] }>("/api/admin/evolution/daily-metrics", "GET", undefined, {
      strategy_id: strategyId,
      strategy_version: versionFilter || undefined,
    });
    setSeries(r.series || []);
  }, [strategyId, versionFilter]);

  const loadRecs = useCallback(async () => {
    const r = await apiJson<{ recommendations: RecRow[] }>("/api/admin/evolution/recommendations", "GET", undefined, {
      strategy_id: strategyId || undefined,
      limit: 40,
    });
    setRecs(r.recommendations || []);
  }, [strategyId]);

  const loadChangelog = useCallback(async () => {
    const r = await apiJson<{ changelog: ChangelogRow[] }>("/api/admin/evolution/changelog", "GET", undefined, {
      strategy_id: strategyId || undefined,
      limit: 30,
    });
    setChangelog(r.changelog || []);
  }, [strategyId]);

  useEffect(() => {
    loadStrategies().catch(() => setMsg("Could not load strategies."));
  }, [loadStrategies]);

  useEffect(() => {
    loadVersions().catch(() => {});
  }, [loadVersions]);

  useEffect(() => {
    loadSeries().catch(() => setMsg("Could not load daily metrics."));
  }, [loadSeries]);

  useEffect(() => {
    loadRecs().catch(() => {});
    loadChangelog().catch(() => {});
  }, [loadRecs, loadChangelog]);

  const recompute = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiJson<{ rows_touched: number }>("/api/admin/evolution/recompute-daily-metrics", "POST", {
        strategy_id: strategyId || null,
        from_date: null,
        to_date: null,
      });
      setMsg(`Recomputed daily metrics (${r.rows_touched} row(s) touched).`);
      await loadSeries();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Recompute failed.");
    } finally {
      setBusy(false);
    }
  };

  const generateRecs = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiJson<{ new_recommendation_ids: number[] }>(
        "/api/admin/evolution/recommendations/generate",
        "POST",
        undefined,
        { strategy_id: strategyId || undefined }
      );
      setMsg(`Generated ${r.new_recommendation_ids.length} new recommendation(s).`);
      await loadRecs();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Generate failed.");
    } finally {
      setBusy(false);
    }
  };

  const rejectRec = async (id: number) => {
    setBusy(true);
    try {
      await apiJson(`/api/admin/evolution/recommendations/${id}/reject`, "POST");
      await loadRecs();
    } finally {
      setBusy(false);
    }
  };

  const approveRec = async (id: number) => {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(`/api/admin/evolution/recommendations/${id}/approve`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({}),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || res.statusText);
      }
      const body = (await res.json()) as { new_version: string };
      setMsg(`Created new catalog version ${body.new_version} (DRAFT). Publish from Strategies when ready.`);
      await loadRecs();
      await loadChangelog();
      await loadVersions();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Approve failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AdminGuard>
      <AppFrame
        title="Strategy Evolution"
        subtitle="Daily performance by version, rule-based recommendations, and approved version bumps with changelog."
      >
        <div className="page-section">
          {msg && <div className="notice info evolution-banner">{msg}</div>}

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
              <span>Version filter</span>
              <select value={versionFilter} onChange={(e) => setVersionFilter(e.target.value)}>
                <option value="">All versions</option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version} ({v.publish_status})
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="primary-button" disabled={busy || !strategyId} onClick={() => recompute()}>
              Recompute daily metrics
            </button>
            <button type="button" className="toggle-button" disabled={busy} onClick={() => generateRecs()}>
              Generate recommendations
            </button>
          </div>
        </div>

        <div className="page-section evolution-grid">
          <section className="panel evolution-panel">
            <h2>Performance (IST days)</h2>
            <p className="muted">
              Aggregated from closed live trades. Cumulative line resets when filtering to a single version.
            </p>
            <CumulativeChart series={series} />
            <h3 className="evolution-subhead">Daily realized P&amp;L</h3>
            <DailyBars series={series} />
          </section>

          <section className="panel evolution-panel">
            <h2>Recommendations</h2>
            <p className="muted">Rule engine uses trailing 14d metrics. Approve creates a new DRAFT catalog row (merged patch).</p>
            <ul className="evolution-rec-list">
              {recs.length === 0 && <li className="muted">No rows yet.</li>}
              {recs.map((r) => (
                <li key={r.id} className="evolution-rec-card">
                  <div className="evolution-rec-head">
                    <strong>{r.proposed_title}</strong>
                    <span className="evolution-rec-meta">
                      {r.strategy_id} · {r.from_version} · {r.status}
                    </span>
                  </div>
                  <pre className="evolution-rec-pre">{JSON.stringify(r.rationale_json, null, 2)}</pre>
                  {Object.keys(r.proposed_details_patch || {}).length > 0 && (
                    <pre className="evolution-rec-pre evolution-rec-pre--patch">
                      patch: {JSON.stringify(r.proposed_details_patch, null, 2)}
                    </pre>
                  )}
                  {r.status === "PENDING_REVIEW" && (
                    <div className="evolution-rec-actions">
                      <button type="button" className="primary-button" disabled={busy} onClick={() => approveRec(r.id)}>
                        Approve → new version
                      </button>
                      <button type="button" className="toggle-button" disabled={busy} onClick={() => rejectRec(r.id)}>
                        Reject
                      </button>
                    </div>
                  )}
                  {r.implemented_version && (
                    <p className="muted">Implemented as {r.implemented_version}</p>
                  )}
                </li>
              ))}
            </ul>
          </section>
        </div>

        <section className="page-section panel evolution-panel">
          <h2>Version changelog</h2>
          <ul className="evolution-changelog">
            {changelog.length === 0 && <li className="muted">No entries yet.</li>}
            {changelog.map((c) => (
              <li key={c.id}>
                <strong>
                  {c.strategy_id}: {c.from_version} → {c.to_version}
                </strong>
                <span className="muted"> · {c.created_at}</span>
                <p>{c.summary}</p>
                {c.changelog_md && c.changelog_md !== c.summary && (
                  <pre className="evolution-rec-pre">{c.changelog_md}</pre>
                )}
              </li>
            ))}
          </ul>
        </section>
      </AppFrame>
    </AdminGuard>
  );
}
