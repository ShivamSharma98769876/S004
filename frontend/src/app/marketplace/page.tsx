"use client";

import { useEffect, useMemo, useState } from "react";
import AppFrame from "@/components/AppFrame";
import { apiJson, isAdmin } from "@/lib/api_client";

type StrategyItem = {
  strategy_id: string;
  version: string;
  display_name: string;
  description: string;
  strategy_details?: Record<string, unknown> | null;
  strategy_explainer: string;
  risk_profile: "LOW" | "MEDIUM" | "HIGH";
  status: "ACTIVE" | "PAUSED" | "NOT_SUBSCRIBED";
  pnl_30d: number;
  win_rate: number;
};

/** Template for admin creating a new strategy. Admin customizes JSON; runtime uses saved strategy_details from catalog. */
const DEFAULT_STRATEGY_DETAILS = {
  displayName: "",
  description: "",
  indicators: {
    ema: { fast: 9, slow: 21, description: "" },
    emaCrossover: { bonus: 1, description: "Fast EMA crossed above slow EMA from lower to upper = +1 score bonus" },
    rsi: { period: 14, min: 50, max: 75, description: "" },
    vwap: { description: "" },
    volumeSpike: { minRatio: 1.1, description: "" },
    ivr: { maxThreshold: 20, bonus: 1, description: "IVR < 20 = low IV (cheap options) = +1 score bonus" },
  },
  scoreThreshold: 3,
  scoreMax: 6,
  autoTradeScoreThreshold: 4,
  scoreDescription: "",
};

function CreateStrategyModal({
  onClose,
  onCreate,
  error,
  setError,
}: {
  onClose: () => void;
  onCreate: (p: {
    strategy_id: string;
    version: string;
    display_name: string;
    description: string;
    risk_profile: "LOW" | "MEDIUM" | "HIGH";
    details: Record<string, unknown>;
  }) => Promise<void>;
  error: string;
  setError: (s: string) => void;
}) {
  const [strategyId, setStrategyId] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [riskProfile, setRiskProfile] = useState<"LOW" | "MEDIUM" | "HIGH">("MEDIUM");
  const [detailsJson, setDetailsJson] = useState(() => JSON.stringify(DEFAULT_STRATEGY_DETAILS, null, 2));

  const submit = async () => {
    setError("");
    if (!strategyId.trim() || !displayName.trim()) {
      setError("Strategy ID and Display Name are required.");
      return;
    }
    let details: Record<string, unknown>;
    try {
      details = JSON.parse(detailsJson);
    } catch (e) {
      setError(e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : "Invalid JSON in details.");
      return;
    }
    if (typeof details !== "object" || details === null || Array.isArray(details)) {
      setError("Strategy details must be a JSON object.");
      return;
    }
    await onCreate({
      strategy_id: strategyId.trim().toLowerCase().replace(/\s+/g, "-"),
      version: version.trim() || "1.0.0",
      display_name: displayName.trim(),
      description: description.trim(),
      risk_profile: riskProfile,
      details,
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content strategy-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Create New Strategy</h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {error && <div className="notice warning">{error}</div>}
        <div className="form-grid create-strategy-form">
          <label className="field">
            <span>Strategy ID</span>
            <input
              className="control-input"
              value={strategyId}
              onChange={(e) => setStrategyId(e.target.value)}
              placeholder="strat-my-strategy"
            />
          </label>
          <label className="field">
            <span>Version</span>
            <input className="control-input" value={version} onChange={(e) => setVersion(e.target.value)} />
          </label>
          <label className="field field-span-2">
            <span>Display Name</span>
            <input
              className="control-input"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="My Strategy"
            />
          </label>
          <label className="field field-span-2">
            <span>Description</span>
            <input
              className="control-input"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description"
            />
          </label>
          <label className="field">
            <span>Risk Profile</span>
            <select
              className="control-select"
              value={riskProfile}
              onChange={(e) => setRiskProfile(e.target.value as "LOW" | "MEDIUM" | "HIGH")}
            >
              <option value="LOW">Low</option>
              <option value="MEDIUM">Medium</option>
              <option value="HIGH">High</option>
            </select>
          </label>
          <label className="field field-span-2">
            <span>Details (JSON)</span>
            <textarea
              className="control-input strategy-json-input"
              value={detailsJson}
              onChange={(e) => setDetailsJson(e.target.value)}
              rows={12}
              spellCheck={false}
            />
          </label>
        </div>
        <div className="modal-footer">
          <button className="action-button pause" onClick={onClose}>
            Cancel
          </button>
          <button className="action-button resume" onClick={submit}>
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

function formatCurrency(value: number): string {
  return value.toLocaleString("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 2,
  });
}

function nextStatus(current: StrategyItem["status"]): StrategyItem["status"] {
  if (current === "NOT_SUBSCRIBED") {
    return "ACTIVE";
  }
  if (current === "ACTIVE") {
    return "PAUSED";
  }
  return "ACTIVE";
}

export default function MarketplacePage() {
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<"ALL" | "LOW" | "MEDIUM" | "HIGH">("ALL");
  const [rows, setRows] = useState<StrategyItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(20);
  const [sortBy, setSortBy] = useState<"updated_at" | "pnl_30d" | "win_rate">("updated_at");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [editingRow, setEditingRow] = useState<StrategyItem | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [detailsJson, setDetailsJson] = useState("");
  const [detailsError, setDetailsError] = useState("");
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" | "info" } | null>(null);

  const showToast = (message: string, type: "success" | "error" | "info" = "success") => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadRows = async () => {
    setLoading(true);
    setError(null);
    try {
      const json = await apiJson<StrategyItem[]>("/api/marketplace/strategies", "GET", undefined, {
        risk: riskFilter === "ALL" ? undefined : riskFilter,
        sort_by: sortBy,
        sort_dir: sortDir,
        limit,
        offset,
      });
      setRows(json);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load strategies");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRows();
  }, [riskFilter, sortBy, sortDir, limit, offset]);

  const filtered = useMemo(() => {
    return rows.filter((x) => {
      const q = query.trim().toLowerCase();
      const qOk = !q || x.display_name.toLowerCase().includes(q) || x.strategy_id.toLowerCase().includes(q);
      return qOk;
    });
  }, [query, rows]);

  const [tableSortCol, setTableSortCol] = useState<string>("");
  const [tableSortDir, setTableSortDir] = useState<"asc" | "desc">("asc");

  const sortedFiltered = useMemo(() => {
    if (!tableSortCol) return filtered;
    const arr = [...filtered];
    const mult = tableSortDir === "asc" ? 1 : -1;
    arr.sort((a, b) => {
      const av = (a as Record<string, unknown>)[tableSortCol];
      const bv = (b as Record<string, unknown>)[tableSortCol];
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "string" && typeof bv === "string") return mult * av.localeCompare(bv);
      return mult * (String(av ?? "").localeCompare(String(bv ?? "")));
    });
    return arr;
  }, [filtered, tableSortCol, tableSortDir]);

  const handleTableSort = (col: string) => {
    if (tableSortCol === col) setTableSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setTableSortCol(col);
      setTableSortDir("asc");
    }
  };

  const summary = useMemo(() => {
    const active = rows.filter((x) => x.status === "ACTIVE").length;
    const subscribed = rows.filter((x) => x.status !== "NOT_SUBSCRIBED").length;
    const avgWin = rows.reduce((acc, row) => acc + row.win_rate, 0) / Math.max(rows.length, 1);
    const totalPnl = rows.reduce((acc, row) => acc + row.pnl_30d, 0);
    return {
      active,
      subscribed,
      avgWin,
      totalPnl,
    };
  }, [rows]);

  const applyAction = async (row: StrategyItem) => {
    const next = nextStatus(row.status);
    const action = next === "ACTIVE" && row.status === "NOT_SUBSCRIBED" ? "SUBSCRIBE" : next === "PAUSED" ? "PAUSE" : "RESUME";
    try {
      await apiJson("/api/marketplace/subscriptions", "POST", {
        strategy_id: row.strategy_id,
        strategy_version: row.version,
        mode: "PAPER",
        action,
      });
      await loadRows();
    } catch {
      setError("Unable to update strategy subscription.");
    }
  };

  const openEdit = (row: StrategyItem) => {
    setEditingRow(row);
    setDetailsJson(
      row.strategy_details && Object.keys(row.strategy_details).length > 0
        ? JSON.stringify(row.strategy_details, null, 2)
        : JSON.stringify(DEFAULT_STRATEGY_DETAILS, null, 2)
    );
    setDetailsError("");
  };

  const saveDetails = async () => {
    if (!editingRow) return;
    setDetailsError("");
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(detailsJson);
    } catch (e) {
      const msg = e instanceof SyntaxError ? `Invalid JSON: ${e.message}` : "Invalid JSON.";
      setDetailsError(msg);
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setDetailsError("Strategy details must be a JSON object.");
      return;
    }
    showToast("Validating and saving...", "info");
    try {
      await apiJson(
        `/api/marketplace/strategies/${encodeURIComponent(editingRow.strategy_id)}/${encodeURIComponent(editingRow.version)}/details`,
        "PUT",
        { details: parsed }
      );
      setEditingRow(null);
      await loadRows();
      showToast("Strategy details saved successfully.", "success");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to save.";
      setDetailsError(msg);
      showToast(msg, "error");
    }
  };

  const handleCreate = async (payload: {
    strategy_id: string;
    version: string;
    display_name: string;
    description: string;
    risk_profile: "LOW" | "MEDIUM" | "HIGH";
    details: Record<string, unknown>;
  }) => {
    setDetailsError("");
    showToast("Validating and creating...", "info");
    try {
      await apiJson("/api/marketplace/strategies", "POST", payload);
      setCreateOpen(false);
      await loadRows();
      showToast("Strategy created successfully.", "success");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Failed to create strategy.";
      setDetailsError(msg);
      showToast(msg, "error");
    }
  };

  return (
    <AppFrame title="Strategy Marketplace" subtitle="Subscribe to strategies, review risk posture, and track performance.">
      {toast && (
        <div className={`settings-toast settings-toast-${toast.type}`} role="status">
          {toast.message}
        </div>
      )}
        <section className="summary-grid">
        <div className="summary-card">
          <div className="summary-label">Total 30D P&L</div>
          <div className="summary-value">{formatCurrency(summary.totalPnl)}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Average Win Rate</div>
          <div className="summary-value">{summary.avgWin.toFixed(1)}%</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Subscribed Strategies</div>
          <div className="summary-value">{summary.subscribed}</div>
        </div>
        <div className="summary-card">
          <div className="summary-label">Active Strategies</div>
          <div className="summary-value">{summary.active}</div>
        </div>
      </section>

      <section className="controls">
        {isAdmin() && (
          <button className="action-button resume" onClick={() => setCreateOpen(true)}>
            + Create Strategy
          </button>
        )}
        <input
          className="control-input"
          placeholder="Search by strategy name or id"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search strategy"
        />
        <select
          className="control-select"
          value={riskFilter}
          onChange={(e) => {
            setOffset(0);
            setRiskFilter(e.target.value as "ALL" | "LOW" | "MEDIUM" | "HIGH");
          }}
          aria-label="Filter by risk"
        >
          <option value="ALL">All Risk</option>
          <option value="LOW">Low Risk</option>
          <option value="MEDIUM">Medium Risk</option>
          <option value="HIGH">High Risk</option>
        </select>
        <select className="control-select" value={sortBy} onChange={(e) => setSortBy(e.target.value as typeof sortBy)}>
          <option value="updated_at">Latest Updated</option>
          <option value="pnl_30d">P&amp;L 30D</option>
          <option value="win_rate">Win Rate</option>
        </select>
        <select className="control-select" value={sortDir} onChange={(e) => setSortDir(e.target.value as typeof sortDir)}>
          <option value="desc">Desc</option>
          <option value="asc">Asc</option>
        </select>
        <select
          className="control-select"
          value={limit}
          onChange={(e) => {
            setOffset(0);
            setLimit(Number(e.target.value));
          }}
        >
          <option value={10}>10 rows</option>
          <option value={20}>20 rows</option>
          <option value={50}>50 rows</option>
        </select>
      </section>

      <section className="table-card">
        {loading && <div className="empty-state">Loading strategies...</div>}
        {error && <div className="notice error">{error}</div>}
        <div className="table-wrap">
          <table className="market-table">
            <thead>
              <tr>
                <th className="sortable-th" onClick={() => handleTableSort("display_name")}>Strategy {tableSortCol === "display_name" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("strategy_explainer")}>About Strategy {tableSortCol === "strategy_explainer" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("version")}>Version {tableSortCol === "version" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("risk_profile")}>Risk {tableSortCol === "risk_profile" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("pnl_30d")}>P&amp;L (30d) {tableSortCol === "pnl_30d" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("win_rate")}>Win Rate {tableSortCol === "win_rate" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th className="sortable-th" onClick={() => handleTableSort("status")}>Status {tableSortCol === "status" && (tableSortDir === "asc" ? "↑" : "↓")}</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {sortedFiltered.map((row) => (
                <tr key={`${row.strategy_id}:${row.version}`}>
                  <td>
                    <div className="strategy-name">{row.display_name}</div>
                    <div className="strategy-id">{row.strategy_id}</div>
                    {row.description ? <div className="summary-label">{row.description}</div> : null}
                  </td>
                  <td className="metric-neutral">{row.strategy_explainer}</td>
                  <td>{row.version}</td>
                  <td>
                    <span className={`chip chip-risk-${row.risk_profile.toLowerCase()}`}>{row.risk_profile}</span>
                  </td>
                  <td className={row.pnl_30d >= 0 ? "metric-positive" : "metric-neutral"}>
                    {formatCurrency(row.pnl_30d)}
                  </td>
                  <td className="metric-neutral">{row.win_rate.toFixed(1)}%</td>
                  <td>
                    <span className={`chip chip-status-${row.status.toLowerCase()}`}>{row.status}</span>
                  </td>
                  <td>
                    <div className="marketplace-actions">
                      {isAdmin() && (
                        <button className="action-button mini" onClick={() => openEdit(row)} title="Edit strategy details (JSON)">
                          Edit
                        </button>
                      )}
                      {row.status === "NOT_SUBSCRIBED" ? (
                        <button className="action-button" onClick={() => applyAction(row)}>
                          Subscribe
                        </button>
                      ) : row.status === "ACTIVE" ? (
                        <button className="action-button pause" onClick={() => applyAction(row)}>
                          Pause
                        </button>
                      ) : (
                        <button className="action-button resume" onClick={() => applyAction(row)}>
                          Resume
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {sortedFiltered.length === 0 && <div className="empty-state">No strategies match your filter criteria.</div>}
        </div>
        <div className="controls">
          <button className="action-button pause" disabled={offset === 0} onClick={() => setOffset((v) => Math.max(0, v - limit))}>
            Previous
          </button>
          <span className="summary-label">Offset {offset}</span>
          <button className="action-button" disabled={rows.length < limit} onClick={() => setOffset((v) => v + limit)}>
            Next
          </button>
        </div>
      </section>

      {editingRow && (
        <div className="modal-overlay" onClick={() => setEditingRow(null)}>
          <div className="modal-content strategy-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Edit Strategy: {editingRow.display_name}</h3>
              <button className="modal-close" onClick={() => setEditingRow(null)} aria-label="Close">
                ×
              </button>
            </div>
            <p className="strategy-details-hint">
              Timeframe, Target, and SL are managed in Settings → Trading Parameters.
            </p>
            {detailsError && <div className="notice warning">{detailsError}</div>}
            <textarea
              className="control-input strategy-json-input"
              value={detailsJson}
              onChange={(e) => setDetailsJson(e.target.value)}
              spellCheck={false}
              rows={18}
            />
            <div className="modal-footer">
              <button className="action-button pause" onClick={() => setEditingRow(null)}>
                Cancel
              </button>
              <button className="action-button resume" onClick={saveDetails}>
                Save
              </button>
            </div>
          </div>
        </div>
      )}

      {createOpen && (
        <CreateStrategyModal
          onClose={() => setCreateOpen(false)}
          onCreate={handleCreate}
          error={detailsError}
          setError={setDetailsError}
        />
      )}
    </AppFrame>
  );
}

