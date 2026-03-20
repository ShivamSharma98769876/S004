"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";

type DailyPnlPoint = { date: string; pnl: number };
type UserPnlPoint = { userId: number; username: string; pnl: number };
type TradeRow = { trade_date: string; userId: number; username: string; charges: number; pnl: number };
type PerformanceData = { dailyPnl: DailyPnlPoint[]; userPnl: UserPnlPoint[]; tradeRows: TradeRow[] };
type UserOption = { id: number; username: string };

function formatDateLabel(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  } catch {
    return iso;
  }
}

function toYMD(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function CumulativePnlChart({ data }: { data: DailyPnlPoint[] }) {
  if (!data.length) {
    return (
      <div className="dash-chart-container" style={{ minHeight: 230, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span className="empty-state">No trading data yet</span>
      </div>
    );
  }
  const w = 620;
  const h = 230;
  const padLeft = 58;
  const padRight = 20;
  const padTop = 28;
  const padBottom = 40;
  const plotW = w - padLeft - padRight;
  const plotH = h - padTop - padBottom;

  const cumulative = useMemo(() => {
    let sum = 0;
    return data.map((d) => {
      sum += d.pnl;
      return { ...d, cumulative: sum };
    });
  }, [data]);

  const maxPnl = cumulative.length ? Math.max(...cumulative.map((d) => d.cumulative), 0) : 0;
  const minPnl = cumulative.length ? Math.min(...cumulative.map((d) => d.cumulative), 0) : 0;
  const range = Math.max(500, maxPnl - minPnl, Math.abs(maxPnl), Math.abs(minPnl));
  const step = range <= 1000 ? 250 : range <= 5000 ? 1000 : range <= 20000 ? 5000 : 10000;
  const yMax = Math.ceil(Math.max(maxPnl, step) / step) * step;
  const yMin = Math.floor(minPnl / step) * step;
  const yRange = Math.max(step, yMax - yMin);

  const xScale = (i: number) =>
    padLeft + (cumulative.length > 1 ? (i / (cumulative.length - 1)) * plotW : plotW / 2);
  const yScale = (pnl: number) => padTop + plotH - ((pnl - yMin) / yRange) * plotH;

  const yTicks: number[] = [];
  for (let t = Math.ceil(yMin / step) * step; t <= yMax; t += step) yTicks.push(t);
  if (yTicks.length === 0) yTicks.push(0, step);
  if (!yTicks.includes(0) && yMin < 0 && yMax > 0) yTicks.push(0);
  yTicks.sort((a, b) => a - b);

  const linePoints = cumulative.map((d, i) => `${xScale(i)},${yScale(d.cumulative)}`).join(" ");
  const areaPoints = `${padLeft},${padTop + plotH} ${cumulative.map((d, i) => `${xScale(i)},${yScale(d.cumulative)}`).join(" ")} ${padLeft + plotW},${padTop + plotH}`;

  const maxLabels = 8;
  const showLabelEvery = Math.max(1, Math.ceil(cumulative.length / maxLabels));
  const highPnl = cumulative.length ? Math.max(...cumulative.map((d) => d.cumulative)) : 0;
  const lowPnl = cumulative.length ? Math.min(...cumulative.map((d) => d.cumulative)) : 0;

  return (
    <div className="dash-chart-container">
      <svg viewBox={`0 0 ${w} ${h}`} className="dash-chart">
        {yTicks.map((tick) => (
          <text key={tick} x={padLeft - 10} y={yScale(tick) + 5} className="dash-chart-label" textAnchor="end">
            ₹{tick.toLocaleString("en-IN")}
          </text>
        ))}
        {yMin < 0 && yMax > 0 && (
          <line
            x1={padLeft}
            y1={yScale(0)}
            x2={padLeft + plotW}
            y2={yScale(0)}
            stroke="rgba(255,255,255,0.25)"
            strokeWidth="1"
            strokeDasharray="4 3"
          />
        )}
        <polygon points={areaPoints} fill="rgba(21, 70, 130, 0.4)" />
        <polyline fill="none" stroke="#2bc48a" strokeWidth="2.5" points={linePoints} />
        {cumulative.map((d, i) => (
          <circle
            key={`dot-${d.date}-${i}`}
            cx={xScale(i)}
            cy={yScale(d.cumulative)}
            r="5"
            fill="#1f6feb"
            className="dash-chart-dot"
          >
            <title>
              {formatDateLabel(d.date)} — Cumulative: {d.cumulative >= 0 ? "+" : ""}₹
              {d.cumulative.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
            </title>
          </circle>
        ))}
        {cumulative.map((d, i) =>
          i % showLabelEvery === 0 || i === cumulative.length - 1 ? (
            <text
              key={`label-${d.date}-${i}`}
              x={xScale(i)}
              y={h - 12}
              className="dash-chart-label dash-chart-xlabel"
              textAnchor="middle"
            >
              {formatDateLabel(d.date)}
            </text>
          ) : null
        )}
      </svg>
      <div className="dash-chart-footer">
        <span>{cumulative.length} trading days</span>
        <span className={highPnl >= 0 ? "metric-positive" : "chip-risk-high"}>
          High: {highPnl >= 0 ? "+" : ""}₹{highPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
        </span>
        <span className={lowPnl >= 0 ? "metric-positive" : "chip-risk-high"}>
          Low: {lowPnl >= 0 ? "+" : ""}₹{lowPnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
        </span>
      </div>
    </div>
  );
}

function UserPnlBarChart({ data }: { data: UserPnlPoint[] }) {
  if (!data.length) {
    return (
      <div className="dash-chart-container" style={{ minHeight: 180, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <span className="empty-state">No user data for selected filters</span>
      </div>
    );
  }
  const barH = 28;
  const gap = 8;
  const labelW = 100;
  const plotW = 400;
  const padRight = 80;
  const totalH = data.length * (barH + gap) - gap;

  const minPnl = Math.min(...data.map((d) => d.pnl), 0);
  const maxPnl = Math.max(...data.map((d) => d.pnl), 0);
  const rangeMin = Math.min(minPnl, 0);
  const rangeMax = Math.max(maxPnl, 0);
  const range = Math.max(500, rangeMax - rangeMin);
  const zeroX = labelW + ((0 - rangeMin) / range) * plotW;
  const xScale = (pnl: number) => labelW + ((pnl - rangeMin) / range) * plotW;

  return (
    <div className="dash-chart-container">
      <svg viewBox={`0 0 ${labelW + plotW + padRight} ${totalH + 24}`} className="dash-chart" style={{ minHeight: Math.max(180, totalH + 24) }}>
        {minPnl < 0 && maxPnl > 0 && (
          <line
            x1={zeroX}
            y1={0}
            x2={zeroX}
            y2={totalH + 10}
            stroke="rgba(255,255,255,0.35)"
            strokeWidth="1"
            strokeDasharray="4 3"
          />
        )}
        {data.map((d, i) => {
          const y = i * (barH + gap) + 4;
          const isPos = d.pnl >= 0;
          const xEnd = xScale(d.pnl);
          const xStart = xScale(0);
          const width = Math.abs(xEnd - xStart);
          const x = isPos ? xStart : xEnd;
          return (
            <g key={`bar-${d.userId}`}>
              <text x={labelW - 8} y={y + barH / 2 + 4} className="dash-chart-label" textAnchor="end">
                {d.username}
              </text>
              <rect
                x={x}
                y={y}
                width={width}
                height={barH}
                fill={isPos ? "rgba(43, 196, 138, 0.6)" : "rgba(239, 68, 68, 0.6)"}
                rx="4"
              />
              <text
                x={xEnd + (isPos ? 6 : -6)}
                y={y + barH / 2 + 4}
                className="dash-chart-label"
                textAnchor={isPos ? "start" : "end"}
                fill={isPos ? "#2bc48a" : "#ef4444"}
              >
                {d.pnl >= 0 ? "+" : ""}₹{d.pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

type DatePreset = "7" | "30" | "90" | "custom";

export default function PerformanceAnalyticsPage() {
  const admin = isAdmin();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<PerformanceData>({ dailyPnl: [], userPnl: [], tradeRows: [] });
  const [users, setUsers] = useState<UserOption[]>([]);

  const [datePreset, setDatePreset] = useState<DatePreset>("90");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [filterUserId, setFilterUserId] = useState<number | "all">("all");
  const [tradeType, setTradeType] = useState<"BOTH" | "PAPER" | "LIVE">("BOTH");
  const [tradeSortCol, setTradeSortCol] = useState<string>("trade_date");
  const [tradeSortDir, setTradeSortDir] = useState<"asc" | "desc">("desc");

  const today = useMemo(() => toYMD(new Date()), []);
  const presetRanges = useMemo(() => {
    const t = new Date();
    return {
      "7": { from: toYMD(new Date(t.getTime() - 6 * 24 * 60 * 60 * 1000)), to: today },
      "30": { from: toYMD(new Date(t.getTime() - 29 * 24 * 60 * 60 * 1000)), to: today },
      "90": { from: toYMD(new Date(t.getTime() - 89 * 24 * 60 * 60 * 1000)), to: today },
    };
  }, [today]);

  useEffect(() => {
    if (datePreset !== "custom") {
      const r = presetRanges[datePreset as keyof typeof presetRanges];
      setFromDate(r.from);
      setToDate(r.to);
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
      if (admin && filterUserId !== "all") params.userId = String(filterUserId);
      const qs = new URLSearchParams(params).toString();
      const result = await apiJson<PerformanceData>(`/api/trades/performance-analytics?${qs}`);
      setData({
        dailyPnl: result?.dailyPnl ?? [],
        userPnl: result?.userPnl ?? [],
        tradeRows: result?.tradeRows ?? [],
      });
    } catch (e) {
      setData({ dailyPnl: [], userPnl: [], tradeRows: [] });
      setError(e instanceof Error ? e.message : "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  }, [datePreset, fromDate, toDate, filterUserId, tradeType, admin, presetRanges]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const cumulativeData = useMemo(() => data.dailyPnl, [data.dailyPnl]);
  const totalPnl = cumulativeData.reduce((s, d) => s + d.pnl, 0);

  const sortedTradeRows = useMemo(() => {
    const arr = [...(data.tradeRows || [])];
    const mult = tradeSortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: string | number = (a as Record<string, unknown>)[tradeSortCol] as string | number;
      let bv: string | number = (b as Record<string, unknown>)[tradeSortCol] as string | number;
      if (tradeSortCol === "trade_date") {
        av = (av ?? "").toString();
        bv = (bv ?? "").toString();
        return mult * (av as string).localeCompare(bv as string);
      }
      if (tradeSortCol === "charges" || tradeSortCol === "pnl") {
        av = Number(av ?? 0);
        bv = Number(bv ?? 0);
        return mult * (av - bv);
      }
      if (tradeSortCol === "username") {
        av = String(av ?? "");
        bv = String(bv ?? "");
        return mult * (av as string).localeCompare(bv as string);
      }
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [data.tradeRows, tradeSortCol, tradeSortDir]);

  const handleTradeSort = (col: string) => {
    if (tradeSortCol === col) setTradeSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setTradeSortCol(col);
      setTradeSortDir("asc");
    }
  };

  return (
    <AppFrame
      title="Performance Analytics"
      subtitle="Cumulative P&L and performance metrics across trading days. Net P&L (after charges)."
    >
      <section className="panel-accent-chain" style={{ padding: "1rem 1.25rem", marginBottom: "1rem", borderRadius: 8 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "flex-end" }}>
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>Date range</span>
            <select
              className="control-input"
              value={datePreset}
              onChange={(e) => setDatePreset(e.target.value as DatePreset)}
              style={{ minWidth: 120 }}
            >
              <option value="7">Last 7 days</option>
              <option value="30">Last 30 days</option>
              <option value="90">Last 90 days</option>
              <option value="custom">Custom</option>
            </select>
          </label>
          {datePreset === "custom" && (
            <>
              <label className="field" style={{ marginBottom: 0 }}>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>From</span>
                <input type="date" className="control-input" value={fromDate} onChange={(e) => setFromDate(e.target.value)} style={{ minWidth: 140 }} />
              </label>
              <label className="field" style={{ marginBottom: 0 }}>
                <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>To</span>
                <input type="date" className="control-input" value={toDate} onChange={(e) => setToDate(e.target.value)} style={{ minWidth: 140 }} />
              </label>
            </>
          )}
          {admin && (
            <label className="field" style={{ marginBottom: 0 }}>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>User</span>
              <select
                className="control-input"
                value={filterUserId === "all" ? "all" : filterUserId}
                onChange={(e) => setFilterUserId(e.target.value === "all" ? "all" : Number(e.target.value))}
                style={{ minWidth: 140 }}
              >
                <option value="all">All users</option>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.username}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>Trade type</span>
            <select
              className="control-input"
              value={tradeType}
              onChange={(e) => setTradeType(e.target.value as "BOTH" | "PAPER" | "LIVE")}
              style={{ minWidth: 100 }}
            >
              <option value="BOTH">Both</option>
              <option value="PAPER">Paper</option>
              <option value="LIVE">Live</option>
            </select>
          </label>
          <button className="action-button" onClick={fetchData} disabled={loading}>
            {loading ? "Loading..." : "Apply"}
          </button>
        </div>
      </section>

      {error && (
        <div className="empty-state chip-risk-high" style={{ marginBottom: "1rem" }}>
          {error}
          <button className="action-button" onClick={fetchData} style={{ marginLeft: "0.5rem" }}>
            Retry
          </button>
        </div>
      )}

      <section className="dash-chart-grid">
        <div className="table-card panel-accent-chain">
          <div className="dash-chart-header">
            <div className="panel-title">Cumulative P&L</div>
            <div
              className={`dash-chart-pnl-box ${totalPnl >= 0 ? "metric-positive" : totalPnl < 0 ? "chip-risk-high" : "metric-neutral"}`}
            >
              {totalPnl > 0 ? "+" : ""}₹{totalPnl.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </div>
          </div>
          <div className="dash-chart-wrap">
            <CumulativePnlChart data={cumulativeData} />
          </div>
        </div>

        <div className="table-card panel-accent-chain">
          <div className="dash-chart-header">
            <div className="panel-title">P&L by User</div>
          </div>
          <div className="dash-chart-wrap">
            <UserPnlBarChart data={data.userPnl} />
          </div>
          <div style={{ marginTop: "1.25rem", borderTop: "1px solid var(--border)", paddingTop: "1rem" }}>
            <div className="panel-title" style={{ marginBottom: "0.75rem", fontSize: "0.875rem" }}>
              P&L By Date
            </div>
            <div className="table-wrap">
              <table className="market-table analytics-trade-table">
                <thead>
                  <tr>
                    <th className="sortable-th" onClick={() => handleTradeSort("trade_date")}>
                      Trade Date {tradeSortCol === "trade_date" && (tradeSortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th className="sortable-th" onClick={() => handleTradeSort("username")}>
                      User {tradeSortCol === "username" && (tradeSortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th className="sortable-th" onClick={() => handleTradeSort("charges")}>
                      Charges {tradeSortCol === "charges" && (tradeSortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th className="sortable-th" onClick={() => handleTradeSort("pnl")}>
                      P&L {tradeSortCol === "pnl" && (tradeSortDir === "asc" ? "↑" : "↓")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTradeRows.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="empty-state">
                        No trade data for selected filters
                      </td>
                    </tr>
                  ) : (
                    sortedTradeRows.map((row, i) => (
                      <tr key={`${row.trade_date}-${row.userId}-${i}`}>
                        <td>{row.trade_date}</td>
                        <td>{row.username}</td>
                        <td>₹{row.charges.toLocaleString("en-IN", { minimumFractionDigits: 2 })}</td>
                        <td className={row.pnl >= 0 ? "metric-positive" : "chip-risk-high"}>
                          {row.pnl >= 0 ? "+" : ""}₹{row.pnl.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>
    </AppFrame>
  );
}
