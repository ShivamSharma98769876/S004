"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";

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

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "--:--:--";
    return d.toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "--:--:--";
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "--";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "--";
    return d.toLocaleDateString("en-CA", { year: "numeric", month: "2-digit", day: "2-digit" });
  } catch {
    return "--";
  }
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

  const fetchReports = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiJson<ReportTrade[]>("/api/trades/reports");
      setTrades(Array.isArray(data) ? data : []);
    } catch (e) {
      setTrades([]);
      setError(e instanceof Error ? e.message : "Failed to load reports. Check backend connection and auth.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchReports();
  }, [fetchReports]);

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
    a.download = `performance-snapshot-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const totalPnl = trades.reduce((sum, t) => sum + Number(t.realized_pnl ?? 0), 0);
  const winners = trades.filter((t) => Number(t.realized_pnl ?? 0) > 0).length;
  const winRate = trades.length ? Math.round((winners / trades.length) * 100) : 0;
  const colSpan = admin ? 15 : 14;

  return (
    <AppFrame title="Reports & Backtesting" subtitle="Review performance history and validate strategy consistency.">
      {loading && <div className="empty-state">Loading reports...</div>}
      {error && (
        <div className="empty-state chip-risk-high" style={{ marginBottom: "1rem" }}>
          {error}
          <button className="action-button" onClick={fetchReports} style={{ marginLeft: "0.5rem" }}>
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
