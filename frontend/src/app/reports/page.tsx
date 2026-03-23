"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";
import { formatDateYmdIST, formatTimeIST, toYmdIST } from "@/lib/datetime_ist";

type ReportTrade = {
  trade_ref: string;
  symbol: string;
  strategy_name?: string | null;
  mode: string;
  side: string;
  quantity: number;
  qty?: number;
  entry_price: number;
  current_price: number;
  realized_pnl?: number | null;
  opened_at?: string | null;
  closed_at?: string | null;
  reason?: string | null;
  username?: string | null;
  manual_execute?: boolean | null;
};

type StrategyOption = {
  strategy_id: string;
  strategy_version: string;
  display_name: string;
};

const STRAT_SEP = "\u0001";

function buildReportsQuery(params: {
  fromDate: string;
  toDate: string;
  tradeType: "BOTH" | "PAPER" | "LIVE";
  strategyId: string;
  strategyVersion: string;
  filterUserId: number | "all";
  takenBy: "ALL" | "AUTO" | "MANUAL";
  admin: boolean;
}): string {
  const q = new URLSearchParams();
  if (params.fromDate.trim()) q.set("from_date", params.fromDate.trim());
  if (params.toDate.trim()) q.set("to_date", params.toDate.trim());
  if (params.tradeType !== "BOTH") q.set("mode", params.tradeType);
  if (params.strategyId.trim()) {
    q.set("strategy_id", params.strategyId.trim());
    if (params.strategyVersion.trim()) q.set("strategy_version", params.strategyVersion.trim());
  }
  if (params.admin && params.filterUserId !== "all") q.set("userId", String(params.filterUserId));
  if (params.takenBy !== "ALL") q.set("taken_by", params.takenBy);
  const s = q.toString();
  return s ? `/api/trades/reports?${s}` : "/api/trades/reports";
}

function formatTime(iso: string | null | undefined): string {
  return formatTimeIST(iso, { seconds: true, fallback: "--:--:--" });
}

function formatDate(iso: string | null | undefined): string {
  return formatDateYmdIST(iso, "--");
}

function escapeCsv(val: string): string {
  if (val.includes(",") || val.includes('"') || val.includes("\n")) return `"${val.replace(/"/g, '""')}"`;
  return val;
}

export default function ReportsPage() {
  const [trades, setTrades] = useState<ReportTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const admin = isAdmin();

  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [tradeType, setTradeType] = useState<"BOTH" | "PAPER" | "LIVE">("BOTH");
  const [strategyKey, setStrategyKey] = useState("");
  const [takenBy, setTakenBy] = useState<"ALL" | "AUTO" | "MANUAL">("ALL");
  const [filterUserId, setFilterUserId] = useState<number | "all">("all");
  const [strategyOptions, setStrategyOptions] = useState<StrategyOption[]>([]);
  const [userOptions, setUserOptions] = useState<{ id: number; username: string }[]>([]);

  const loadReportsUrl = useCallback(async (url: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiJson<ReportTrade[]>(url);
      setTrades(Array.isArray(data) ? data : []);
    } catch (e) {
      setTrades([]);
      setError(e instanceof Error ? e.message : "Failed to load reports. Check backend connection and auth.");
    } finally {
      setLoading(false);
    }
  }, []);

  const applyFilters = useCallback(() => {
    let sid = "";
    let sver = "";
    if (strategyKey.includes(STRAT_SEP)) {
      const [a, b] = strategyKey.split(STRAT_SEP);
      sid = a || "";
      sver = b || "";
    }
    const url = buildReportsQuery({
      fromDate,
      toDate,
      tradeType,
      strategyId: sid,
      strategyVersion: sver,
      filterUserId,
      takenBy,
      admin,
    });
    return loadReportsUrl(url);
  }, [admin, fromDate, toDate, tradeType, strategyKey, takenBy, filterUserId, loadReportsUrl]);

  useEffect(() => {
    loadReportsUrl("/api/trades/reports");
  }, [loadReportsUrl]);

  useEffect(() => {
    apiJson<{ strategies: StrategyOption[] }>("/api/trades/reports/strategies")
      .then((r) => setStrategyOptions(Array.isArray(r?.strategies) ? r.strategies : []))
      .catch(() => setStrategyOptions([]));
  }, []);

  useEffect(() => {
    if (!admin) return;
    apiJson<{ id: number; username: string }[]>("/api/admin/users")
      .then((rows) =>
        setUserOptions(Array.isArray(rows) ? rows.map((u) => ({ id: u.id, username: u.username || `user${u.id}` })) : [])
      )
      .catch(() => setUserOptions([]));
  }, [admin]);

  const resetFilters = () => {
    setFromDate("");
    setToDate("");
    setTradeType("BOTH");
    setStrategyKey("");
    setTakenBy("ALL");
    setFilterUserId("all");
    void loadReportsUrl("/api/trades/reports");
  };

  const [sortCol, setSortCol] = useState<string>("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sortedTrades = useMemo(() => {
    if (!sortCol) return trades;
    const arr = [...trades];
    const mult = sortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: string | number | undefined = (a as Record<string, unknown>)[sortCol] as string | number | undefined;
      let bv: string | number | undefined = (b as Record<string, unknown>)[sortCol] as string | number | undefined;
      if (sortCol === "realized_pnl") {
        av = Number(a.realized_pnl ?? 0);
        bv = Number(b.realized_pnl ?? 0);
      } else if (sortCol === "opened_at" || sortCol === "closed_at" || sortCol === "trade_date") {
        av = new Date(av ?? 0).getTime();
        bv = new Date(bv ?? 0).getTime();
        if (sortCol === "trade_date") {
          av = new Date(a.opened_at ?? 0).getTime();
          bv = new Date(b.opened_at ?? 0).getTime();
        }
      } else if (sortCol === "manual_execute") {
        av = a.manual_execute === false ? 0 : a.manual_execute === true ? 1 : -1;
        bv = b.manual_execute === false ? 0 : b.manual_execute === true ? 1 : -1;
      } else if (sortCol === "qty") {
        av = Number(a.qty ?? a.quantity ?? 0);
        bv = Number(b.qty ?? b.quantity ?? 0);
      }
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * (av || "").localeCompare(bv || "");
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [trades, sortCol, sortDir]);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  const downloadCsv = () => {
    const headers = admin
      ? ["Trade ID", "Trade Date", "User", "Symbol", "Strategy", "Type", "Mode", "Taken By", "Buy Time", "Entry", "Sell Time", "Exit", "Qty", "P&L", "Reason"]
      : ["Trade ID", "Trade Date", "Symbol", "Strategy", "Type", "Mode", "Taken By", "Buy Time", "Entry", "Sell Time", "Exit", "Qty", "P&L", "Reason"];
    const rows = sortedTrades.map((t) => {
      const optType = t.symbol?.includes("CE") ? "CE" : t.symbol?.includes("PE") ? "PE" : t.side === "BUY" ? "PE" : "CE";
      const takenBy = t.manual_execute === false ? "Auto" : t.manual_execute === true ? "Manual" : "—";
      const pnl = Number(t.realized_pnl ?? 0);
      const cells = admin
        ? [t.trade_ref, formatDate(t.opened_at), String(t.username ?? "—"), t.symbol, String(t.strategy_name ?? "—"), optType, t.mode || "PAPER", takenBy, formatTime(t.opened_at), Number(t.entry_price).toFixed(2), formatTime(t.closed_at), Number(t.current_price).toFixed(2), String(t.qty ?? t.quantity ?? 0), (pnl >= 0 ? "+" : "") + pnl.toFixed(2), t.reason || "Manual"]
        : [t.trade_ref, formatDate(t.opened_at), t.symbol, String(t.strategy_name ?? "—"), optType, t.mode || "PAPER", takenBy, formatTime(t.opened_at), Number(t.entry_price).toFixed(2), formatTime(t.closed_at), Number(t.current_price).toFixed(2), String(t.qty ?? t.quantity ?? 0), (pnl >= 0 ? "+" : "") + pnl.toFixed(2), t.reason || "Manual"];
      return cells.map(escapeCsv).join(",");
    });
    const csv = [headers.join(","), ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `performance-snapshot-${toYmdIST()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const totalPnl = trades.reduce((sum, t) => sum + Number(t.realized_pnl ?? 0), 0);
  const winners = trades.filter((t) => Number(t.realized_pnl ?? 0) > 0).length;
  const winRate = trades.length ? Math.round((winners / trades.length) * 100) : 0;
  const colSpan = admin ? 15 : 14;

  return (
    <AppFrame title="Reports" subtitle="Review performance history and strategy consistency.">
      <section
        className="panel-accent-chain"
        style={{ padding: "1rem 1.25rem", marginBottom: "1rem", borderRadius: 8 }}
      >
        <div className="panel-title" style={{ marginBottom: "0.75rem", fontSize: "0.95rem" }}>
          Filters
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "flex-end" }}>
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
              Trade Date – From
            </span>
            <input
              type="date"
              className="control-input reports-filter-date"
              value={fromDate}
              onChange={(e) => setFromDate(e.target.value)}
              title="Choose start of trade date range (calendar)"
              autoComplete="off"
            />
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
              Trade Date – To
            </span>
            <input
              type="date"
              className="control-input reports-filter-date"
              value={toDate}
              onChange={(e) => setToDate(e.target.value)}
              title="Choose end of trade date range (calendar)"
              autoComplete="off"
            />
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
              Trade type
            </span>
            <select
              className="control-input"
              value={tradeType}
              onChange={(e) => setTradeType(e.target.value as "BOTH" | "PAPER" | "LIVE")}
              style={{ minWidth: 110 }}
            >
              <option value="BOTH">Both</option>
              <option value="PAPER">Paper</option>
              <option value="LIVE">Live</option>
            </select>
          </label>
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
              Strategy
            </span>
            <select
              className="control-input"
              value={strategyKey}
              onChange={(e) => setStrategyKey(e.target.value)}
              style={{ minWidth: 220 }}
            >
              <option value="">All strategies</option>
              {strategyOptions.map((s) => (
                <option key={`${s.strategy_id}-${s.strategy_version}`} value={`${s.strategy_id}${STRAT_SEP}${s.strategy_version}`}>
                  {s.display_name} ({s.strategy_version})
                </option>
              ))}
            </select>
          </label>
          {admin && (
            <label className="field" style={{ marginBottom: 0 }}>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
                User
              </span>
              <select
                className="control-input"
                value={filterUserId === "all" ? "all" : String(filterUserId)}
                onChange={(e) => setFilterUserId(e.target.value === "all" ? "all" : Number(e.target.value))}
                style={{ minWidth: 160 }}
              >
                <option value="all">All users</option>
                {userOptions.map((u) => (
                  <option key={u.id} value={String(u.id)}>
                    {u.username}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="field" style={{ marginBottom: 0 }}>
            <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginBottom: 4, display: "block" }}>
              Taken by
            </span>
            <select
              className="control-input"
              value={takenBy}
              onChange={(e) => setTakenBy(e.target.value as "ALL" | "AUTO" | "MANUAL")}
              style={{ minWidth: 120 }}
            >
              <option value="ALL">All</option>
              <option value="AUTO">Auto</option>
              <option value="MANUAL">Manual</option>
            </select>
          </label>
          <button type="button" className="action-button" onClick={() => void applyFilters()} disabled={loading}>
            {loading ? "Loading..." : "Apply"}
          </button>
          <button type="button" className="action-button resume" onClick={resetFilters} disabled={loading}>
            Reset
          </button>
        </div>
        <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.75rem", marginBottom: 0 }}>
          <strong>Trade Date</strong> range uses each trade’s <strong>exit (sell) date</strong>. Use the calendar in the fields above to
          pick dates. No dates = latest rows (up to 500); with any filter = up to 2000 rows.
          {admin ? " User filter is available for Admin only." : ""}
        </p>
      </section>

      {loading && <div className="empty-state">Loading reports...</div>}
      {error && (
        <div className="empty-state chip-risk-high" style={{ marginBottom: "1rem" }}>
          {error}
          <button className="action-button" onClick={() => void applyFilters()} style={{ marginLeft: "0.5rem" }}>
            Retry
          </button>
        </div>
      )}
      <div className="reports-page-content">
      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-label">Trades</div>
          <div className="summary-value">{trades.length}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Win Rate</div>
          <div className="summary-value">{winRate}%</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">P&L</div>
          <div className={`summary-value ${totalPnl >= 0 ? "metric-positive" : "chip-risk-high"}`}>
            INR {totalPnl.toFixed(2)}
          </div>
        </div>
      </section>

      <section className="table-card reports-snapshot-full">
        <div className="panel-title reports-panel-header">
          <span>Performance Snapshot</span>
          <button type="button" className="action-button resume" onClick={downloadCsv} disabled={trades.length === 0} title="Download CSV">
            Download CSV
          </button>
        </div>
        <div className="table-wrap reports-table-wrap">
          <table className="market-table reports-snapshot-table">
            <colgroup>
              <col className="col-trade-id" />
              <col />
              {admin && <col className="col-username" />}
              <col className="col-symbol-full" />
              <col />
              <col />
              <col />
              <col />
              <col />
              <col />
              <col />
              <col />
              <col />
            </colgroup>
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleSort("trade_ref")}>TRADE ID {sortCol === "trade_ref" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("trade_date")}>TRADE DATE {sortCol === "trade_date" && (sortDir === "asc" ? "↑" : "↓")}</th>
                {admin && <th className="sortable-th" onClick={() => handleSort("username")}>USER {sortCol === "username" && (sortDir === "asc" ? "↑" : "↓")}</th>}
                <th className="sortable-th" onClick={() => handleSort("symbol")}>SYMBOL {sortCol === "symbol" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th>STRATEGY</th>
                <th>TYPE</th>
                <th className="sortable-th" onClick={() => handleSort("mode")}>MODE {sortCol === "mode" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("manual_execute")}>TAKEN BY {sortCol === "manual_execute" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("opened_at")}>BUY TIME {sortCol === "opened_at" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("entry_price")}>ENTRY {sortCol === "entry_price" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("closed_at")}>SELL TIME {sortCol === "closed_at" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("current_price")}>EXIT {sortCol === "current_price" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("qty")}>QTY {sortCol === "qty" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("realized_pnl")}>P&L {sortCol === "realized_pnl" && (sortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleSort("reason")}>REASON {sortCol === "reason" && (sortDir === "asc" ? "↑" : "↓")}</th>
              </tr>
            </thead>
            <tbody>
              {trades.length === 0 ? (
                <tr>
                  <td colSpan={colSpan} className="empty-state">
                    No closed trades
                  </td>
                </tr>
              ) : (
                sortedTrades.map((t, i) => {
                  const pnl = Number(t.realized_pnl ?? 0);
                  const isProfit = pnl >= 0;
                  const optType = t.symbol?.includes("CE") ? "CE" : t.symbol?.includes("PE") ? "PE" : t.side === "BUY" ? "PE" : "CE";
                  const takenBy = t.manual_execute === false ? "Auto" : t.manual_execute === true ? "Manual" : "—";
                  return (
                    <tr key={`${t.trade_ref}-${i}`}>
                      <td className="cell-trade-id">{t.trade_ref}</td>
                      <td>{formatDate(t.opened_at)}</td>
                      {admin && <td>{t.username ?? "—"}</td>}
                      <td className="cell-symbol-full">{t.symbol}</td>
                      <td className="summary-label">{t.strategy_name || "—"}</td>
                      <td>
                        <span className="chip chip-status-paused">{optType}</span>
                      </td>
                      <td>
                        <span className="chip chip-status-active">{t.mode || "PAPER"}</span>
                      </td>
                      <td>
                        <span className={`chip ${t.manual_execute === false ? "chip-status-active" : "chip-status-paused"}`}>
                          {takenBy}
                        </span>
                      </td>
                      <td>{formatTime(t.opened_at)}</td>
                      <td>{Number(t.entry_price).toFixed(2)}</td>
                      <td>{formatTime(t.closed_at)}</td>
                      <td>{Number(t.current_price).toFixed(2)}</td>
                      <td>{t.qty ?? t.quantity ?? 0}</td>
                      <td className={isProfit ? "metric-positive" : "chip-risk-high"}>
                        {isProfit ? "+" : ""}
                        {pnl.toFixed(2)}
                      </td>
                      <td>{t.reason || "Manual"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
      </div>
    </AppFrame>
  );
}
