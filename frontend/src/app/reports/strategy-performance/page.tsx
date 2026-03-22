"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";

type StrategyRow = {
  strategy_id: string;
  strategy_version: string;
  display_name: string;
  trade_count: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate_pct: number;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  avg_net_pnl?: number;
  best_trade_net?: number;
  worst_trade_net?: number;
};

type Overview = {
  total_trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate_pct: number;
  total_net_pnl: number;
  total_gross_pnl: number;
  total_charges: number;
  avg_net_pnl_per_trade: number;
  best_trade_net: number;
  worst_trade_net: number;
  profit_factor: number | null;
  avg_duration_min: number | null;
  max_drawdown: number;
  current_streak: { type: string | null; count: number };
};

type MonthlyRow = { month: string; net_pnl: number };
type HourlyRow = {
  hour_start: number;
  label: string;
  trade_count: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  net_pnl: number;
};
type WeekdayRow = {
  day_index: number;
  day: string;
  trade_count: number;
  wins: number;
  losses: number;
  win_rate_pct: number | null;
  net_pnl: number;
};
type IndexRow = {
  index: string;
  trade_count: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  net_pnl: number;
};
type CePeSide = { trade_count: number; wins: number; losses: number; win_rate_pct: number; net_pnl: number };
type ExitReasonRow = { code: string; label: string; count: number };
type WeeklySplitRow = {
  week_start: string;
  trade_count: number;
  wins: number;
  losses: number;
  win_rate_pct: number;
  net_pnl: number;
};

type ActiveStrategyRow = {
  strategy_id: string;
  strategy_version: string;
  display_name: string;
  subscriber_count?: number;
};

type StrategyPerformanceResponse = {
  strategies: StrategyRow[];
  summary: { strategy_count: number; total_trades: number; total_net_pnl: number };
  overview?: Overview;
  monthly_net_pnl?: MonthlyRow[];
  hourly_performance?: HourlyRow[];
  weekday_performance?: WeekdayRow[];
  by_index?: IndexRow[];
  ce_pe?: { CE: CePeSide; PE: CePeSide };
  exit_reasons?: ExitReasonRow[];
  strategy_weekly_splits?: Record<string, WeeklySplitRow[]>;
  filters?: { index: string; from_date: string; to_date: string };
  active_subscriptions_scope?: "user" | "platform";
  active_strategies?: ActiveStrategyRow[];
};

type UserOption = { id: number; username: string };

function toYMD(d: Date): string {
  return d.toISOString().slice(0, 10);
}

type DatePreset = "7" | "30" | "90" | "all" | "custom";

function formatInr(n: number, opts?: { showSign?: boolean }): string {
  const v = Math.abs(n);
  const body = `₹${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (n < 0) return `−${body}`;
  const sign = opts?.showSign !== false && n > 0 ? "+" : "";
  return `${sign}${body}`;
}

function monthLabel(ym: string): string {
  const [y, m] = ym.split("-").map(Number);
  if (!y || !m) return ym;
  return new Date(y, m - 1, 1).toLocaleDateString("en-IN", { month: "short", year: "numeric" });
}

function winRateClass(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) return "";
  if (pct > 50) return "metric-positive";
  if (pct < 50) return "chip-risk-high";
  return "sp-win-mid";
}

function exitReasonClass(code: string): string {
  const u = code.toUpperCase();
  if (u.includes("SL") || u.includes("STOP")) return "chip-risk-high";
  if (u.includes("TARGET")) return "metric-positive";
  if (u.includes("ADMIN")) return "sp-exit-muted";
  if (u.includes("MANUAL") || u === "UNKNOWN") return "sp-exit-manual";
  return "sp-exit-muted";
}

function WinLossDonut({ wins, losses }: { wins: number; losses: number }) {
  const total = wins + losses;
  if (total === 0) {
    return (
      <div className="sp-donut-empty">
        <span className="empty-state">No trades</span>
      </div>
    );
  }
  const frac = wins / total;
  const r = 52;
  const c = 2 * Math.PI * r;
  const dash = frac * c;
  const gap = c - dash;
  return (
    <div className="sp-donut-wrap">
      <svg viewBox="0 0 120 120" className="sp-donut-svg">
        <circle cx="60" cy="60" r={r} fill="none" stroke="rgba(248,113,113,0.35)" strokeWidth="18" />
        <circle
          cx="60"
          cy="60"
          r={r}
          fill="none"
          stroke="rgba(74,222,128,0.85)"
          strokeWidth="18"
          strokeDasharray={`${dash} ${gap}`}
          strokeLinecap="butt"
          transform="rotate(-90 60 60)"
        />
        <text x="60" y="64" textAnchor="middle" className="sp-donut-center">
          {Math.round(frac * 100)}%
        </text>
      </svg>
      <div className="sp-donut-legend">
        <span>
          <i className="sp-dot sp-dot-win" /> Wins: {wins} ({Math.round(frac * 100)}%)
        </span>
        <span>
          <i className="sp-dot sp-dot-loss" /> Losses: {losses} ({Math.round((1 - frac) * 100)}%)
        </span>
      </div>
    </div>
  );
}

function MonthlyNetPnlBarChart({ data }: { data: MonthlyRow[] }) {
  if (!data.length) {
    return (
      <div className="sp-chart-placeholder">
        <span className="empty-state">No monthly data</span>
      </div>
    );
  }
  const w = 420;
  const h = 200;
  const padL = 52;
  const padB = 36;
  const padT = 16;
  const plotW = w - padL - 16;
  const plotH = h - padB - padT;
  const maxAbs = Math.max(2000, ...data.map((d) => Math.abs(d.net_pnl)), 1);
  const y0 = padT + plotH / 2;
  const barW = Math.min(48, plotW / data.length - 8);
  const step = plotW / Math.max(data.length, 1);

  const yScale = (v: number) => y0 - (v / maxAbs) * (plotH / 2) * (v >= 0 ? 1 : -1);
  const ticks: number[] = [];
  const stepTick = maxAbs <= 8000 ? 2000 : maxAbs <= 16000 ? 2000 : 5000;
  for (let t = 0; t <= maxAbs; t += stepTick) ticks.push(t);

  return (
    <div className="sp-monthly-chart">
      <svg viewBox={`0 0 ${w} ${h}`} className="dash-chart">
        {ticks.map((tk) => (
          <g key={tk}>
            <line
              x1={padL}
              y1={yScale(tk)}
              x2={w - 16}
              y2={yScale(tk)}
              stroke="rgba(255,255,255,0.06)"
              strokeWidth="1"
            />
            <text x={padL - 6} y={yScale(tk) + 4} className="dash-chart-label" textAnchor="end">
              ₹{(tk / 1000).toFixed(0)}k
            </text>
          </g>
        ))}
        <line x1={padL} y1={y0} x2={w - 16} y2={y0} stroke="rgba(255,255,255,0.2)" strokeWidth="1" />
        {data.map((d, i) => {
          const cx = padL + i * step + step / 2;
          const top = d.net_pnl >= 0 ? yScale(d.net_pnl) : y0;
          const bottom = d.net_pnl >= 0 ? y0 : yScale(d.net_pnl);
          const bh = Math.max(2, Math.abs(bottom - top));
          const x = cx - barW / 2;
          const y = d.net_pnl >= 0 ? top : top;
          return (
            <g key={d.month}>
              <rect
                x={x}
                y={y}
                width={barW}
                height={bh}
                rx={4}
                fill={d.net_pnl >= 0 ? "rgba(74,222,128,0.75)" : "rgba(248,113,113,0.65)"}
              >
                <title>
                  {monthLabel(d.month)}: {formatInr(d.net_pnl)}
                </title>
              </rect>
              <text x={cx} y={h - 10} className="dash-chart-label dash-chart-xlabel" textAnchor="middle">
                {monthLabel(d.month)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default function StrategyPerformancePage() {
  const admin = isAdmin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<StrategyPerformanceResponse>({
    strategies: [],
    summary: { strategy_count: 0, total_trades: 0, total_net_pnl: 0 },
    active_strategies: [],
    active_subscriptions_scope: "user",
  });
  const [users, setUsers] = useState<UserOption[]>([]);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const [datePreset, setDatePreset] = useState<DatePreset>("30");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [filterUserId, setFilterUserId] = useState<number | "all">("all");
  const [tradeType, setTradeType] = useState<"BOTH" | "PAPER" | "LIVE">("PAPER");
  const [indexFilter, setIndexFilter] = useState<string>("ALL");
  const [sortCol, setSortCol] = useState<string>("net_pnl");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const today = useMemo(() => toYMD(new Date()), []);
  const presetRanges = useMemo(() => {
    const t = new Date();
    return {
      "7": { from: toYMD(new Date(t.getTime() - 6 * 24 * 60 * 60 * 1000)), to: today },
      "30": { from: toYMD(new Date(t.getTime() - 29 * 24 * 60 * 60 * 1000)), to: today },
      "90": { from: toYMD(new Date(t.getTime() - 89 * 24 * 60 * 60 * 1000)), to: today },
      all: { from: "2020-01-01", to: today },
    };
  }, [today]);

  useEffect(() => {
    if (datePreset !== "custom") {
      const r = presetRanges[datePreset as keyof typeof presetRanges];
      if (r) {
        setFromDate(r.from);
        setToDate(r.to);
      }
    }
  }, [datePreset, presetRanges]);

  useEffect(() => {
    if (admin) {
      apiJson<{ id: number; username: string }[]>("/api/admin/users")
        .then((rows) => setUsers(rows.map((r) => ({ id: r.id, username: r.username || `user${r.id}` }))))
        .catch(() => setUsers([]));
    }
  }, [admin]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    const from = datePreset === "custom" ? fromDate : presetRanges[datePreset as keyof typeof presetRanges]?.from ?? fromDate;
    const to = datePreset === "custom" ? toDate : presetRanges[datePreset as keyof typeof presetRanges]?.to ?? toDate;
    try {
      const params: Record<string, string> = { from_date: from, to_date: to, mode: tradeType };
      if (indexFilter && indexFilter !== "ALL") params.index = indexFilter;
      if (admin && filterUserId !== "all") params.userId = String(filterUserId);
      const qs = new URLSearchParams(params).toString();
      const result = await apiJson<StrategyPerformanceResponse>(`/api/trades/strategy-performance?${qs}`);
      setData({
        strategies: Array.isArray(result?.strategies) ? result.strategies : [],
        summary: result?.summary ?? { strategy_count: 0, total_trades: 0, total_net_pnl: 0 },
        overview: result?.overview,
        monthly_net_pnl: result?.monthly_net_pnl,
        hourly_performance: result?.hourly_performance,
        weekday_performance: result?.weekday_performance,
        by_index: result?.by_index,
        ce_pe: result?.ce_pe,
        exit_reasons: result?.exit_reasons,
        strategy_weekly_splits: result?.strategy_weekly_splits,
        filters: result?.filters,
        active_subscriptions_scope: result?.active_subscriptions_scope ?? "user",
        active_strategies: Array.isArray(result?.active_strategies) ? result.active_strategies : [],
      });
      setUpdatedAt(new Date());
    } catch (e) {
      setData({
        strategies: [],
        summary: { strategy_count: 0, total_trades: 0, total_net_pnl: 0 },
        active_strategies: [],
        active_subscriptions_scope: "user",
      });
      setError(e instanceof Error ? e.message : "Failed to load strategy performance");
    } finally {
      setLoading(false);
    }
  }, [datePreset, fromDate, toDate, filterUserId, tradeType, indexFilter, admin, presetRanges]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const activeSubscriptionsTitle = useMemo(() => {
    if (data.active_subscriptions_scope === "platform") {
      return "Active strategy subscriptions (all users)";
    }
    if (admin && filterUserId !== "all") {
      const u = users.find((x) => x.id === filterUserId);
      return u ? `Active strategies for ${u.username}` : "Active strategies (selected user)";
    }
    return "Your active strategies";
  }, [data.active_subscriptions_scope, admin, filterUserId, users]);

  const sortedRows = useMemo(() => {
    const arr = [...data.strategies];
    const mult = sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      const av = a[sortCol as keyof StrategyRow];
      const bv = b[sortCol as keyof StrategyRow];
      if (sortCol === "display_name" || sortCol === "strategy_id") {
        return mult * String(av ?? "").localeCompare(String(bv ?? ""));
      }
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [data.strategies, sortCol, sortDir]);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("desc");
    }
  };

  const o = data.overview;
  const s = data.summary;
  const streak = o?.current_streak;
  const streakLabel =
    streak?.type && streak.count
      ? streak.type === "W"
        ? `${streak.count}W`
        : `−${streak.count}L`
      : "—";

  return (
    <AppFrame
      title="Strategy Performance"
      subtitle="Strategy-wise P&L, win rate & trade analytics — same charge estimates as Performance Analytics."
    >
      <section className="sp-filter-bar panel-accent-chain">
        <div className="sp-filter-groups">
          <div className="sp-filter-group">
            <span className="sp-filter-label">Range</span>
            <div className="sp-pills">
              {(["7", "30", "90", "all"] as const).map((p) => (
                <button
                  key={p}
                  type="button"
                  className={`sp-pill ${datePreset === p ? "sp-pill-active" : ""}`}
                  onClick={() => setDatePreset(p)}
                >
                  {p === "all" ? "All" : p === "7" ? "7D" : p === "30" ? "30D" : "3M"}
                </button>
              ))}
              <button
                type="button"
                className={`sp-pill ${datePreset === "custom" ? "sp-pill-active" : ""}`}
                onClick={() => setDatePreset("custom")}
              >
                Custom
              </button>
            </div>
          </div>
          <div className="sp-filter-group">
            <span className="sp-filter-label">Mode</span>
            <div className="sp-pills">
              {(["PAPER", "LIVE", "BOTH"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  className={`sp-pill ${tradeType === m ? "sp-pill-active" : ""}`}
                  onClick={() => setTradeType(m)}
                >
                  {m === "BOTH" ? "Both" : m}
                </button>
              ))}
            </div>
          </div>
          <div className="sp-filter-group">
            <span className="sp-filter-label">Index</span>
            <select className="control-input sp-index-select" value={indexFilter} onChange={(e) => setIndexFilter(e.target.value)}>
              <option value="ALL">All</option>
              <option value="NIFTY">NIFTY</option>
              <option value="BANKNIFTY">BANKNIFTY</option>
              <option value="FINNIFTY">FINNIFTY</option>
              <option value="MIDCPNIFTY">MIDCPNIFTY</option>
              <option value="OTHER">Other</option>
            </select>
          </div>
          {datePreset === "custom" && (
            <div className="sp-filter-group sp-filter-dates">
              <input type="date" className="control-input" value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
              <span className="sp-filter-to">to</span>
              <input type="date" className="control-input" value={toDate} onChange={(e) => setToDate(e.target.value)} />
            </div>
          )}
          {admin && (
            <div className="sp-filter-group">
              <span className="sp-filter-label">User</span>
              <select
                className="control-input"
                value={filterUserId === "all" ? "all" : filterUserId}
                onChange={(e) => setFilterUserId(e.target.value === "all" ? "all" : Number(e.target.value))}
              >
                <option value="all">All users</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                  </option>
                ))}
              </select>
            </div>
          )}
          <button className="action-button sp-apply" onClick={fetchData} disabled={loading}>
            {loading ? "…" : "Apply"}
          </button>
        </div>
        <div className="sp-updated">
          Updated{" "}
          {updatedAt
            ? updatedAt.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: true })
            : "—"}
        </div>
      </section>

      {error && (
        <div className="empty-state chip-risk-high sp-error-banner">
          {error}
          <button type="button" className="action-button" onClick={fetchData}>
            Retry
          </button>
        </div>
      )}

      <section className="sp-active-panel panel-accent-chain" aria-label="Active strategy subscriptions">
        <div className="sp-active-head">
          <span className="sp-active-title">{activeSubscriptionsTitle}</span>
          <span className="sp-active-hint">ACTIVE subscription in Marketplace — independent of the date range above.</span>
        </div>
        <div className="sp-active-chips">
          {!data.active_strategies?.length ? (
            <span className="empty-state">No active strategy subscriptions.</span>
          ) : (
            data.active_strategies.map((a) => (
              <span
                key={`${a.strategy_id}|${a.strategy_version}`}
                className="sp-active-chip"
                title={`${a.strategy_id} ${a.strategy_version}`}
              >
                <span>{a.display_name || `${a.strategy_id} ${a.strategy_version}`}</span>
                {data.active_subscriptions_scope === "platform" &&
                a.subscriber_count != null &&
                a.subscriber_count > 0 ? (
                  <span className="sp-active-chip-count">
                    {a.subscriber_count}&nbsp;user{a.subscriber_count === 1 ? "" : "s"}
                  </span>
                ) : null}
              </span>
            ))
          )}
        </div>
      </section>

      {/* Row 1–2: KPI tiles */}
      <section className="sp-metric-grid sp-metric-grid-6">
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-grid" />
          <div className="sp-metric-label">Total Trades</div>
          <div className="sp-metric-value">{o?.total_trades ?? s.total_trades}</div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-trophy" />
          <div className="sp-metric-label">Win Rate</div>
          <div className={`sp-metric-value ${(o?.win_rate_pct ?? 0) >= 50 ? "metric-positive" : "chip-risk-high"}`}>
            {o ? `${o.win_rate_pct}%` : "—"}
          </div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-inr" />
          <div className="sp-metric-label">Net P&L</div>
          <div className={`sp-metric-value ${(o?.total_net_pnl ?? s.total_net_pnl) >= 0 ? "metric-positive" : "chip-risk-high"}`}>
            {formatInr(o?.total_net_pnl ?? s.total_net_pnl)}
          </div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-chart" />
          <div className="sp-metric-label">Avg P&L / Trade</div>
          <div className={`sp-metric-value ${(o?.avg_net_pnl_per_trade ?? 0) >= 0 ? "metric-positive" : "chip-risk-high"}`}>
            {o ? formatInr(o.avg_net_pnl_per_trade) : "—"}
          </div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-charges" />
          <div className="sp-metric-label">Total Charges</div>
          <div className="sp-metric-value">₹{(o?.total_charges ?? 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-ribbon" />
          <div className="sp-metric-label">Best Trade</div>
          <div className="sp-metric-value metric-positive">{o ? formatInr(o.best_trade_net) : "—"}</div>
        </div>
      </section>

      <section className="sp-metric-grid sp-metric-grid-4">
        <div className="sp-metric-tile sp-metric-wide">
          <div className="sp-metric-icon sp-ico-pulse" />
          <div className="sp-metric-label">Profit Factor</div>
          <div className={`sp-metric-value ${(o?.profit_factor ?? 0) >= 1.5 ? "metric-positive" : ""}`}>
            {o?.profit_factor != null ? o.profit_factor : "—"}
          </div>
          <div className="sp-metric-hint">≥1.5 excellent</div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-clock" />
          <div className="sp-metric-label">Avg Duration</div>
          <div className="sp-metric-value">{o?.avg_duration_min != null ? `${o.avg_duration_min} min` : "—"}</div>
          <div className="sp-metric-hint">per trade</div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-dd" />
          <div className="sp-metric-label">Max Drawdown</div>
          <div className="sp-metric-value chip-risk-high">₹{(o?.max_drawdown ?? 0).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</div>
          <div className="sp-metric-hint">peak-to-trough</div>
        </div>
        <div className="sp-metric-tile">
          <div className="sp-metric-icon sp-ico-streak" />
          <div className="sp-metric-label">Current Streak</div>
          <div className={`sp-metric-value ${streak?.type === "L" ? "chip-risk-high" : streak?.type === "W" ? "metric-positive" : ""}`}>
            {streakLabel}
          </div>
          <div className="sp-metric-hint">consecutive W/L</div>
        </div>
      </section>

      {/* Strategy breakdown + donut */}
      <section className="sp-widgets-row">
        <div className="sp-widget-card sp-widget-grow">
          <header className="sp-widget-head sp-accent-blue">
            <h2>Strategy breakdown</h2>
            <p>Click a row to expand week split (IST)</p>
          </header>
          <div className="table-wrap">
            <table className="market-table sp-breakdown-table">
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th className="num">Trades</th>
                  <th className="num">Wins</th>
                  <th className="num">Losses</th>
                  <th className="num">Win %</th>
                  <th className="num">Total P&L</th>
                  <th className="num">Avg P&L</th>
                  <th className="num">Best</th>
                  <th className="num">Worst</th>
                </tr>
              </thead>
              <tbody>
                {sortedRows.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="empty-state">
                      {loading ? "Loading…" : "No closed trades for filters"}
                    </td>
                  </tr>
                ) : (
                  sortedRows.map((row) => {
                    const rk = `${row.strategy_id}|${row.strategy_version}`;
                    const exp = expandedKey === rk;
                    const weeks = data.strategy_weekly_splits?.[rk] ?? [];
                    return (
                      <Fragment key={rk}>
                        <tr
                          className={`sp-strat-row ${exp ? "sp-strat-row-open" : ""}`}
                          onClick={() => setExpandedKey(exp ? null : rk)}
                          style={{ cursor: "pointer" }}
                        >
                          <td>
                            <div className="sp-strat-name">{row.display_name}</div>
                            <div className="sp-strat-id">
                              {row.strategy_id} · {row.strategy_version}
                            </div>
                          </td>
                          <td className="num">{row.trade_count}</td>
                          <td className="num metric-positive">{row.wins}</td>
                          <td className="num chip-risk-high">{row.losses}</td>
                          <td className={`num ${winRateClass(row.win_rate_pct)}`}>{row.win_rate_pct}%</td>
                          <td className={`num ${row.net_pnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>
                            {formatInr(row.net_pnl)}
                          </td>
                          <td className={`num ${(row.avg_net_pnl ?? 0) >= 0 ? "metric-positive" : "chip-risk-high"}`}>
                            {row.avg_net_pnl != null ? formatInr(row.avg_net_pnl) : "—"}
                          </td>
                          <td className="num metric-positive">
                            {row.best_trade_net != null ? formatInr(row.best_trade_net) : "—"}
                          </td>
                          <td className="num chip-risk-high">
                            {row.worst_trade_net != null ? formatInr(row.worst_trade_net, { showSign: false }) : "—"}
                          </td>
                        </tr>
                        {exp && (
                          <tr className="sp-week-subrow">
                            <td colSpan={9}>
                              <div className="sp-week-table-wrap">
                                <table className="market-table sp-mini-table">
                                  <thead>
                                    <tr>
                                      <th>Week starting</th>
                                      <th className="num">Trades</th>
                                      <th className="num">Wins</th>
                                      <th className="num">Losses</th>
                                      <th className="num">Win %</th>
                                      <th className="num">Net P&L</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {weeks.length === 0 ? (
                                      <tr>
                                        <td colSpan={6} className="empty-state">
                                          No weekly buckets
                                        </td>
                                      </tr>
                                    ) : (
                                      weeks.map((w) => (
                                        <tr key={w.week_start}>
                                          <td>{w.week_start}</td>
                                          <td className="num">{w.trade_count}</td>
                                          <td className="num metric-positive">{w.wins}</td>
                                          <td className="num chip-risk-high">{w.losses}</td>
                                          <td className={`num ${winRateClass(w.win_rate_pct)}`}>{w.win_rate_pct}%</td>
                                          <td className={`num ${w.net_pnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>
                                            {formatInr(w.net_pnl)}
                                          </td>
                                        </tr>
                                      ))
                                    )}
                                  </tbody>
                                </table>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="sp-widget-card sp-widget-donut">
          <header className="sp-widget-head sp-accent-green">
            <h2>Win / Loss split</h2>
          </header>
          <WinLossDonut wins={o?.wins ?? 0} losses={o?.losses ?? 0} />
        </div>
      </section>

      {/* Monthly chart */}
      <section className="sp-widget-card sp-monthly-card">
        <header className="sp-widget-head sp-accent-gold">
          <h2>Monthly net P&L</h2>
        </header>
        <MonthlyNetPnlBarChart data={data.monthly_net_pnl ?? []} />
      </section>

      {/* By index + CE/PE + exits */}
      <section className="sp-widgets-row sp-widgets-row-tight">
        <div className="sp-widget-card">
          <header className="sp-widget-head sp-accent-blue">
            <h2>By index</h2>
          </header>
          <div className="table-wrap">
            <table className="market-table sp-mini-table">
              <thead>
                <tr>
                  <th>Index</th>
                  <th className="num">Trades</th>
                  <th className="num">Win %</th>
                  <th className="num">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {(data.by_index ?? []).length === 0 ? (
                  <tr>
                    <td colSpan={4} className="empty-state">
                      No data
                    </td>
                  </tr>
                ) : (
                  data.by_index!.map((r) => (
                    <tr key={r.index}>
                      <td className="sp-strat-name">{r.index}</td>
                      <td className="num">{r.trade_count}</td>
                      <td className={`num ${winRateClass(r.win_rate_pct)}`}>{r.win_rate_pct}%</td>
                      <td className={`num ${r.net_pnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>{formatInr(r.net_pnl)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="sp-widget-card sp-cepe-card">
          <header className="sp-widget-head sp-accent-gold">
            <h2>CE vs PE</h2>
          </header>
          <div className="sp-cepe-grid">
            <div className="sp-cepe-box">
              <div className="sp-cepe-label metric-positive">CE</div>
              <div className="sp-cepe-meta">
                {data.ce_pe?.CE.trade_count ?? 0} trades ({data.ce_pe?.CE.wins ?? 0}W / {data.ce_pe?.CE.losses ?? 0}L)
              </div>
              <div className="sp-cepe-meta">{data.ce_pe?.CE.win_rate_pct ?? 0}% win rate</div>
              <div className={`sp-cepe-pnl ${(data.ce_pe?.CE.net_pnl ?? 0) >= 0 ? "metric-positive" : "chip-risk-high"}`}>
                {formatInr(data.ce_pe?.CE.net_pnl ?? 0)}
              </div>
            </div>
            <div className="sp-cepe-box">
              <div className="sp-cepe-label chip-risk-high">PE</div>
              <div className="sp-cepe-meta">
                {data.ce_pe?.PE.trade_count ?? 0} trades ({data.ce_pe?.PE.wins ?? 0}W / {data.ce_pe?.PE.losses ?? 0}L)
              </div>
              <div className="sp-cepe-meta">{data.ce_pe?.PE.win_rate_pct ?? 0}% win rate</div>
              <div className={`sp-cepe-pnl ${(data.ce_pe?.PE.net_pnl ?? 0) >= 0 ? "metric-positive" : "chip-risk-high"}`}>
                {formatInr(data.ce_pe?.PE.net_pnl ?? 0)}
              </div>
            </div>
          </div>
          <div className="sp-exit-section">
            <div className="sp-exit-head">
              <span className="sp-exit-ico" />
              Exit reasons
            </div>
            <div className="sp-exit-chips">
              {(data.exit_reasons ?? []).length === 0 ? (
                <span className="empty-state">—</span>
              ) : (
                data.exit_reasons!.map((er) => (
                  <span key={er.code} className={`sp-exit-chip ${exitReasonClass(er.code)}`}>
                    <strong>{er.code}</strong> {er.count}
                  </span>
                ))
              )}
            </div>
          </div>
        </div>
      </section>

      {/* Hourly + Weekday */}
      <section className="sp-widgets-row sp-widgets-row-tight">
        <div className="sp-widget-card">
          <header className="sp-widget-head sp-accent-blue">
            <h2>Hourly performance</h2>
          </header>
          <div className="table-wrap">
            <table className="market-table sp-mini-table">
              <thead>
                <tr>
                  <th>Hour</th>
                  <th className="num">Trades</th>
                  <th className="num">Win %</th>
                  <th className="num">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {(data.hourly_performance ?? []).map((h) => (
                  <tr key={h.hour_start}>
                    <td>{h.label}</td>
                    <td className="num">{h.trade_count}</td>
                    <td className={`num ${winRateClass(h.win_rate_pct ?? undefined)}`}>
                      {h.win_rate_pct != null ? `${h.win_rate_pct}%` : "—"}
                    </td>
                    <td className={`num ${h.net_pnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>{formatInr(h.net_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="sp-widget-card">
          <header className="sp-widget-head sp-accent-gold">
            <h2>Weekday performance</h2>
          </header>
          <div className="table-wrap">
            <table className="market-table sp-mini-table">
              <thead>
                <tr>
                  <th>Day</th>
                  <th className="num">Trades</th>
                  <th className="num">Win %</th>
                  <th className="num">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {(data.weekday_performance ?? []).map((d) => (
                  <tr key={d.day}>
                    <td>{d.day}</td>
                    <td className="num">{d.trade_count}</td>
                    <td className={`num ${winRateClass(d.win_rate_pct ?? undefined)}`}>
                      {d.win_rate_pct != null ? `${d.win_rate_pct}%` : "—"}
                    </td>
                    <td className={`num ${d.net_pnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>{formatInr(d.net_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </AppFrame>
  );
}
