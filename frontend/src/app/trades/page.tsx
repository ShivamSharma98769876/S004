"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { API_TIMEOUT_EXECUTE_MS, apiJson, isAdmin, postTradesRefreshCycle } from "@/lib/api_client";

type Recommendation = {
  recommendation_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  target_price: number;
  stop_loss_price: number;
  confidence_score: number;
  rank_value: number;
  vwap?: number | null;
  ema9?: number | null;
  ema21?: number | null;
  rsi?: number | null;
  ivr?: number | null;
  volume?: number | null;
  avg_volume?: number | null;
  volume_spike_ratio?: number | null;
  score?: number | null;
  score_max?: number | null;
  primary_ok?: boolean | null;
  ema_ok?: boolean | null;
  rsi_ok?: boolean | null;
  volume_ok?: boolean | null;
  signal_eligible?: boolean | null;
  failed_conditions?: string | null;
  heuristic_reasons?: string[] | null;
  strategy_name?: string | null;
  strategy_id?: string | null;
  strategy_version?: string | null;
  spot_price?: number | null;
  atm_distance?: number | null;
  timeframe?: string | null;
  refresh_interval_sec?: number | null;
  status: string;
};

function compactSymbol(symbol: string): string {
  return symbol.replace(/\s+/g, "").toUpperCase();
}

function formatStrategyWithVersion(row: Recommendation): string {
  const name = (row.strategy_name || "").trim();
  const base = name || row.strategy_id?.trim() || "—";
  const ver = (row.strategy_version || "").trim();
  if (ver) {
    return `${base} · v${ver}`;
  }
  return base;
}

function recommendationReasonsFull(row: Recommendation): string {
  if (row.heuristic_reasons && row.heuristic_reasons.length > 0) {
    return row.heuristic_reasons.join("; ");
  }
  return (row.failed_conditions || "").trim();
}

function formatTradeExecuteError(raw: string): string {
  if (/recommendation\s+not\s+found/i.test(raw) || /refresh the trades/i.test(raw)) {
    return "That row may have been replaced by a chain refresh. Reload the page, then execute again.";
  }
  return raw;
}

type OpenTrade = {
  trade_ref: string;
  symbol: string;
  strategy_name?: string | null;
  mode: string;
  side: string;
  qty?: number;
  quantity: number;
  entry_price: number;
  current_price: number;
  target_price?: number;
  stop_loss_price?: number;
  unrealized_pnl: number;
  manual_execute?: boolean | null;
};

export default function TradesPage() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [openTrades, setOpenTrades] = useState<OpenTrade[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(10);
  const [sortBy, setSortBy] = useState<"rank" | "confidence" | "created_at">("rank");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [minConfidence, setMinConfidence] = useState(0);
  const [recSortCol, setRecSortCol] = useState<string>("");
  const [recSortDir, setRecSortDir] = useState<"asc" | "desc">("asc");
  const [openSortCol, setOpenSortCol] = useState<string>("");
  const [openSortDir, setOpenSortDir] = useState<"asc" | "desc">("asc");
  const inFlightRef = useRef(false);
  const listsInFlightRef = useRef(false);

  /** Fast list read (~10s): recommendations + open positions only (no refresh-cycle, no funds/summary). */
  const TRADES_LIST_POLL_MS = 10_000;
  /** Engine refresh (~20s): POST refresh-cycle + lists, aligned with recommendation engine + auto-execute loop. */
  const TRADES_ENGINE_POLL_MS = 20_000;

  const loadLists = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (listsInFlightRef.current) return;
      listsInFlightRef.current = true;
      const silent = opts?.silent ?? false;
      try {
        const [recs, open] = await Promise.all([
          apiJson<Recommendation[]>("/api/trades/recommendations", "GET", undefined, {
            status: "GENERATED",
            sort_by: sortBy,
            sort_dir: sortDir,
            min_confidence: minConfidence,
            limit,
            offset,
            ensure_refresh: "false",
            ...(isAdmin() && { all_strategies: "true" }),
          }),
          apiJson<OpenTrade[]>("/api/trades/open"),
        ]);
        setRecommendations(recs);
        setOpenTrades(open);
      } catch (e) {
        if (!silent) {
          setError(e instanceof Error ? e.message : "Failed to load trades");
        }
      } finally {
        listsInFlightRef.current = false;
      }
    },
    [limit, minConfidence, offset, sortBy, sortDir],
  );

  const loadAll = useCallback(
    async (opts?: { silent?: boolean }) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      const silent = opts?.silent ?? false;
      if (!silent) {
        setLoading(true);
        setError(null);
      }
      try {
        // Keep page snappy: trigger refresh-cycle, but don't block list rendering on a slow broker call.
        const refreshCycle = postTradesRefreshCycle().catch(() => null);
        try {
          await Promise.race([refreshCycle, new Promise((resolve) => window.setTimeout(resolve, 1200))]);
        } catch {
          /* still load table from stored rows */
        }
        await loadLists({ silent: true });
      } catch (e) {
        if (!silent) {
          setError(e instanceof Error ? e.message : "Failed to load trades");
        }
      } finally {
        inFlightRef.current = false;
        if (!silent) {
          setLoading(false);
        }
      }
    },
    [loadLists],
  );

  useEffect(() => {
    if (isAdmin()) setLimit((n) => (n <= 10 ? 24 : n));
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  useEffect(() => {
    const fast = window.setInterval(() => void loadLists({ silent: true }), TRADES_LIST_POLL_MS);
    const engine = window.setInterval(() => void loadAll({ silent: true }), TRADES_ENGINE_POLL_MS);
    return () => {
      window.clearInterval(fast);
      window.clearInterval(engine);
    };
  }, [loadAll, loadLists]);

  const execute = async (recommendationId: string, mode: "PAPER" | "LIVE") => {
    setError(null);
    try {
      await apiJson(
        "/api/trades/execute",
        "POST",
        {
          recommendation_id: recommendationId,
          mode,
          quantity: 1,
        },
        undefined,
        { timeoutMs: API_TIMEOUT_EXECUTE_MS },
      );
      await loadAll();
    } catch (e) {
      const raw = e instanceof Error ? e.message : "Unable to execute selected recommendation.";
      setError(formatTradeExecuteError(raw));
    }
  };

  const closeTrade = async (tradeRef: string) => {
    try {
      await apiJson(`/api/trades/simulate-close/${tradeRef}`, "POST");
      await loadAll();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to close trade.");
    }
  };

  const grossExposure = useMemo(
    () => openTrades.reduce((acc, t) => acc + Number(t.entry_price || 0) * Number(t.quantity || 0), 0),
    [openTrades]
  );
  const unrealized = useMemo(() => openTrades.reduce((acc, t) => acc + Number(t.unrealized_pnl || 0), 0), [openTrades]);

  const sortedRecs = useMemo(() => {
    if (!recSortCol) return recommendations;
    const arr = [...recommendations];
    const mult = recSortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: unknown = (a as Record<string, unknown>)[recSortCol];
      let bv: unknown = (b as Record<string, unknown>)[recSortCol];
      if (recSortCol === "symbol") {
        av = String(av ?? "").toUpperCase();
        bv = String(bv ?? "").toUpperCase();
      }
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * av.localeCompare(bv);
      if (typeof av === "boolean" && typeof bv === "boolean") return mult * (av === bv ? 0 : av ? 1 : -1);
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [recommendations, recSortCol, recSortDir]);

  const sortedOpenTrades = useMemo(() => {
    if (!openSortCol) return openTrades;
    const arr = [...openTrades];
    const mult = openSortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      let av: unknown = (a as Record<string, unknown>)[openSortCol];
      let bv: unknown = (b as Record<string, unknown>)[openSortCol];
      if (openSortCol === "manual_execute") {
        av = av === false ? 0 : av === true ? 1 : -1;
        bv = bv === false ? 0 : bv === true ? 1 : -1;
      }
      if (openSortCol === "qty") {
        av = a.qty ?? a.quantity ?? 0;
        bv = b.qty ?? b.quantity ?? 0;
      }
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * av.localeCompare(bv);
      return mult * (Number(av ?? 0) - Number(bv ?? 0));
    });
    return arr;
  }, [openTrades, openSortCol, openSortDir]);

  const handleRecSort = (col: string) => {
    if (recSortCol === col) setRecSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setRecSortCol(col);
      setRecSortDir("asc");
    }
  };
  const handleOpenSort = (col: string) => {
    if (openSortCol === col) setOpenSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setOpenSortCol(col);
      setOpenSortDir("asc");
    }
  };

  return (
    <AppFrame title="Trades" subtitle="Ranked candidates for manual paper/live execution and active trade monitoring.">
      <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-label">Open Positions</div>
          <div className="summary-value">{openTrades.length}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Gross Exposure</div>
          <div className="summary-value">INR {grossExposure.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Unrealized P&L</div>
          <div className="summary-value">INR {unrealized.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Eligible Recommendations</div>
          <div className="summary-value">{recommendations.length}</div>
        </div>
      </section>

      {loading && <div className="empty-state">Loading trade data...</div>}
      {error && <div className="notice error">{error}</div>}

      <section className="table-card">
        <div className="panel-title">Eligible Trades (Ranked)</div>
        <section className="controls">
          <select className="control-select" value={sortBy} onChange={(e) => setSortBy(e.target.value as typeof sortBy)}>
            <option value="rank">Sort: Rank</option>
            <option value="confidence">Sort: Confidence</option>
            <option value="created_at">Sort: Created</option>
          </select>
          <select className="control-select" value={sortDir} onChange={(e) => setSortDir(e.target.value as typeof sortDir)}>
            <option value="asc">Asc</option>
            <option value="desc">Desc</option>
          </select>
          <input
            className="control-input"
            type="number"
            min={0}
            max={100}
            value={minConfidence}
            onChange={(e) => {
              setOffset(0);
              setMinConfidence(Number(e.target.value));
            }}
            placeholder="Min confidence"
          />
          <select
            className="control-select"
            value={limit}
            onChange={(e) => {
              setOffset(0);
              setLimit(Number(e.target.value));
            }}
          >
            <option value={5}>5 rows</option>
            <option value={10}>10 rows</option>
            <option value={20}>20 rows</option>
          </select>
        </section>
        <div className="table-wrap">
          <table className="market-table trades-eligible-table">
            <colgroup>
              <col className="col-rec-symbol" />
              <col className="col-rec-strategy" />
              <col span={10} />
              <col className="col-rec-reasons" />
              <col className="col-rec-action" />
            </colgroup>
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleRecSort("symbol")}>Symbol {recSortCol === "symbol" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th>Strategy</th>
                <th className="sortable-th" onClick={() => handleRecSort("entry_price")}>LTP {recSortCol === "entry_price" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("ema9")}>E9 {recSortCol === "ema9" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("ema21")}>E21 {recSortCol === "ema21" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("rsi")}>RSI {recSortCol === "rsi" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("vwap")}>VWAP {recSortCol === "vwap" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th
                  className="sortable-th"
                  title="IV rank proxy within this expiry chain (0–100)"
                  onClick={() => handleRecSort("ivr")}
                >
                  IVR {recSortCol === "ivr" && (recSortDir === "asc" ? "↑" : "↓")}
                </th>
                <th
                  className="sortable-th"
                  title="Model confidence 0–99 from strike score and volume spike (not refresh cadence)"
                  onClick={() => handleRecSort("confidence_score")}
                >
                  Confidence {recSortCol === "confidence_score" && (recSortDir === "asc" ? "↑" : "↓")}
                </th>
                <th
                  className="sortable-th"
                  title="TrendSnap: points on this option’s premium series (LTP vs VWAP, EMA9>EMA21, RSI band, volume). Not NIFTY spot."
                  onClick={() => handleRecSort("score")}
                >
                  Score {recSortCol === "score" && (recSortDir === "asc" ? "↑" : "↓")}
                </th>
                <th className="sortable-th" onClick={() => handleRecSort("signal_eligible")}>Eligible {recSortCol === "signal_eligible" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("atm_distance")}>ATM Dist {recSortCol === "atm_distance" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleRecSort("failed_conditions")}>Reasons {recSortCol === "failed_conditions" && (recSortDir === "asc" ? "↑" : "↓")}</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {sortedRecs.length === 0 ? (
                <tr>
                  <td colSpan={14} className="empty-state">
                    No recommendations available.
                  </td>
                </tr>
              ) : (
                sortedRecs.map((row) => (
                  <tr key={row.recommendation_id}>
                    <td className="strategy-name">{compactSymbol(row.symbol)}</td>
                    <td className="cell-strategy-rec" title={formatStrategyWithVersion(row)}>
                      {formatStrategyWithVersion(row)}
                    </td>
                    <td>{Number(row.entry_price).toFixed(2)}</td>
                    <td>{row.ema9 != null ? Number(row.ema9).toFixed(2) : "—"}</td>
                    <td>{row.ema21 != null ? Number(row.ema21).toFixed(2) : "—"}</td>
                    <td>{row.rsi != null ? Number(row.rsi).toFixed(2) : "—"}</td>
                    <td>{row.vwap != null ? Number(row.vwap).toFixed(2) : "—"}</td>
                    <td title="IV rank proxy within this expiry chain (0–100)">
                      {row.ivr != null ? Number(row.ivr).toFixed(1) : "—"}
                    </td>
                    <td title="Derived from leg score ÷ max and volume bonus; low when strike rules fail">
                      <span className="chip chip-status-active">{Number(row.confidence_score ?? 0).toFixed(2)}</span>
                    </td>
                    <td title="Rule points on this strike’s premium (VWAP/EMA/RSI/vol). 0/4 means none of the four passed — see Reasons.">
                      {row.score != null ? (row.score_max != null ? `${row.score}/${row.score_max}` : String(row.score)) : "—"}
                    </td>
                    <td>
                      <span className={`chip ${row.signal_eligible ? "chip-status-active" : "chip-status-paused"}`}>
                        {row.signal_eligible ? "YES" : "NO"}
                      </span>
                    </td>
                    <td>{row.atm_distance != null ? `${row.atm_distance >= 0 ? "+" : ""}${row.atm_distance}` : "—"}</td>
                    <td className="cell-failed-conditions">
                      {(() => {
                        const full = recommendationReasonsFull(row);
                        if (!full) {
                          return "—";
                        }
                        return (
                          <span className="reasons-preview" title={full}>
                            {full}
                          </span>
                        );
                      })()}
                    </td>
                    <td>
                      <button
                        className="icon-action icon-action-paper"
                        title="Execute Paper"
                        aria-label="Execute Paper"
                        onClick={() => execute(row.recommendation_id, "PAPER")}
                      >
                        <svg viewBox="0 0 24 24" className="icon-action-svg" aria-hidden="true">
                          <path d="M7 3h7l5 5v13a1 1 0 0 1-1 1H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" />
                          <path d="M14 3v5h5" />
                          <path d="M9 13h6M9 16h6M9 19h4" />
                        </svg>
                      </button>{" "}
                      <button
                        className="icon-action icon-action-live"
                        title="Execute Live"
                        aria-label="Execute Live"
                        onClick={() => execute(row.recommendation_id, "LIVE")}
                      >
                        <svg viewBox="0 0 24 24" className="icon-action-svg" aria-hidden="true">
                          <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
                        </svg>
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <div className="controls">
          <button className="action-button pause" disabled={offset === 0} onClick={() => setOffset((v) => Math.max(0, v - limit))}>
            Previous
          </button>
          <span className="summary-label">Offset {offset}</span>
          <button className="action-button" disabled={recommendations.length < limit} onClick={() => setOffset((v) => v + limit)}>
            Next
          </button>
        </div>
      </section>

      <section className="table-card">
        <div className="panel-title">Open Trades</div>
        <div className="table-wrap">
          <table className="market-table">
            <colgroup>
              <col />
              <col className="col-symbol" />
            </colgroup>
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleOpenSort("trade_ref")}>Trade Ref {openSortCol === "trade_ref" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("symbol")}>Symbol {openSortCol === "symbol" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th>Strategy</th>
                <th className="sortable-th" onClick={() => handleOpenSort("mode")}>Mode {openSortCol === "mode" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("manual_execute")}>Taken By {openSortCol === "manual_execute" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("side")}>Side {openSortCol === "side" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("qty")}>Qty {openSortCol === "qty" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("entry_price")}>Entry {openSortCol === "entry_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("current_price")}>Current {openSortCol === "current_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("target_price")}>Target {openSortCol === "target_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("stop_loss_price")}>SL {openSortCol === "stop_loss_price" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleOpenSort("unrealized_pnl")}>Unrealized P&L {openSortCol === "unrealized_pnl" && (openSortDir === "asc" ? "↑" : "↓")}</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {sortedOpenTrades.length === 0 ? (
                <tr>
                  <td colSpan={13} className="empty-state">
                    No open trades.
                  </td>
                </tr>
              ) : (
                sortedOpenTrades.map((t) => (
                  <tr key={t.trade_ref}>
                    <td>{t.trade_ref}</td>
                    <td>{t.symbol}</td>
                    <td className="summary-label">{t.strategy_name || "—"}</td>
                    <td>{t.mode}</td>
                    <td>
                      <span className={`chip ${t.manual_execute === false ? "chip-status-active" : "chip-status-paused"}`}>
                        {t.manual_execute === false ? "Auto" : t.manual_execute === true ? "Manual" : "—"}
                      </span>
                    </td>
                    <td>{t.side}</td>
                    <td>{t.qty ?? t.quantity ?? 0}</td>
                    <td>{Number(t.entry_price).toFixed(2)}</td>
                    <td>{Number(t.current_price).toFixed(2)}</td>
                    <td>{t.target_price != null ? Number(t.target_price).toFixed(2) : "—"}</td>
                    <td>{t.stop_loss_price != null ? Number(t.stop_loss_price).toFixed(2) : "—"}</td>
                    <td className={Number(t.unrealized_pnl || 0) >= 0 ? "metric-positive" : "chip-risk-high"}>
                      {Number(t.unrealized_pnl || 0) >= 0 ? "+" : ""}
                      {Number(t.unrealized_pnl).toFixed(2)}
                    </td>
                    <td>
                      <button className="action-button pause" onClick={() => closeTrade(t.trade_ref)}>
                        Close
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </AppFrame>
  );
}
