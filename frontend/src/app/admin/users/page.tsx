"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { apiJson } from "@/lib/api_client";
import { formatDateTimeIST } from "@/lib/datetime_ist";

type ActiveStrategySub = {
  strategy_id: string;
  strategy_version: string;
  display_name: string;
};

type UserRow = {
  id: number;
  username: string;
  email: string;
  full_name: string;
  role: string;
  status: string;
  approved_paper: boolean;
  approved_live: boolean;
  engine_running?: boolean;
  engine_mode?: string;
  created_at: string | null;
  active_strategies?: ActiveStrategySub[];
};

function CreateUserModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!email.trim()) {
      setError("Email is required.");
      return;
    }
    if (!password || password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setLoading(true);
    try {
      await apiJson("/api/admin/users", "POST", {
        email: email.trim().toLowerCase(),
        password,
        full_name: fullName.trim(),
      });
      onCreated();
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to create user.";
      setError(msg === "Failed to fetch" ? "Cannot reach backend. Ensure the backend is running." : msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Create User</h3>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {error && <div className="notice warning">{error}</div>}
        <form onSubmit={submit} className="form-grid">
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              className="control-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
              required
              disabled={loading}
            />
          </label>
          <label className="field">
            <span>Password</span>
            <input
              type="password"
              className="control-input"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Min 6 characters"
              minLength={6}
              required
              disabled={loading}
            />
          </label>
          <label className="field">
            <span>Full Name</span>
            <input
              type="text"
              className="control-input"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Optional"
              disabled={loading}
            />
          </label>
          <div className="field modal-actions">
            <button type="button" className="action-button pause" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button type="submit" className="action-button resume" disabled={loading}>
              {loading ? "Creating…" : "Create User"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

type PlatformRisk = {
  trading_paused: boolean;
  pause_reason: string | null;
  updated_at: string | null;
  schema_ready?: boolean;
};

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [platform, setPlatform] = useState<PlatformRisk | null>(null);
  const [platformReason, setPlatformReason] = useState("");
  const [platformSaving, setPlatformSaving] = useState(false);
  const [platformErr, setPlatformErr] = useState("");

  const loadUsers = useCallback(async () => {
    setError("");
    try {
      const rows = await apiJson<UserRow[]>("/api/admin/users");
      setUsers(rows ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshUsersSilent = useCallback(async () => {
    try {
      const rows = await apiJson<UserRow[]>("/api/admin/users");
      setUsers(rows ?? []);
    } catch {
      /* keep last good list */
    }
  }, []);

  useEffect(() => {
    void loadUsers();
  }, [loadUsers]);

  useEffect(() => {
    const t = setInterval(() => void refreshUsersSilent(), 12000);
    return () => clearInterval(t);
  }, [refreshUsersSilent]);

  const loadPlatform = async () => {
    try {
      const p = await apiJson<PlatformRisk>("/api/admin/platform");
      setPlatform(p);
      setPlatformReason(p.pause_reason || "");
      setPlatformErr("");
    } catch {
      setPlatform(null);
    }
  };

  useEffect(() => {
    loadPlatform();
  }, []);

  const savePlatformPause = async (paused: boolean) => {
    setPlatformSaving(true);
    setPlatformErr("");
    try {
      const p = await apiJson<PlatformRisk>("/api/admin/platform", "PUT", {
        trading_paused: paused,
        pause_reason: paused ? platformReason.trim() || "Paused by admin" : null,
      });
      setPlatform(p);
      setPlatformReason(p.pause_reason || "");
    } catch (e) {
      setPlatformErr(e instanceof Error ? e.message : "Failed to update platform risk.");
    } finally {
      setPlatformSaving(false);
    }
  };

  const [sortCol, setSortCol] = useState<string>("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const sortedUsers = useMemo(() => {
    if (!sortCol) return users;
    const arr = [...users];
    const mult = sortDir === "asc" ? 1 : -1;
    const strategiesLabel = (u: UserRow) =>
      (u.active_strategies ?? [])
        .map((s) => s.display_name || `${s.strategy_id} ${s.strategy_version}`)
        .join(", ");
    arr.sort((a, b) => {
      if (sortCol === "active_strategies") {
        return mult * strategiesLabel(a).localeCompare(strategiesLabel(b));
      }
      const av = (a as Record<string, unknown>)[sortCol];
      const bv = (b as Record<string, unknown>)[sortCol];
      if (typeof av === "number" && typeof bv === "number") return mult * (av - bv);
      if (typeof av === "boolean" && typeof bv === "boolean") return mult * (av === bv ? 0 : av ? 1 : -1);
      if (typeof av === "string" && typeof bv === "string") return mult * (av || "").localeCompare(bv || "");
      return mult * (String(av ?? "").localeCompare(String(bv ?? "")));
    });
    return arr;
  }, [users, sortCol, sortDir]);

  const handleSort = (col: string) => {
    if (sortCol === col) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  const toggleApproval = async (userId: number, field: "approved_paper" | "approved_live", current: boolean) => {
    try {
      await apiJson(`/api/admin/users/${userId}/approval`, "PUT", { [field]: !current });
      setUsers((prev) =>
        prev.map((u) => (u.id === userId ? { ...u, [field]: !current } : u))
      );
    } catch {
      setError("Failed to update approval.");
    }
  };

  return (
    <AdminGuard>
      <AppFrame
        title="User Management"
        subtitle="Create users, approve Paper/Live, and monitor each user’s trading engine (refreshes every ~12s)."
      >
        {error && <div className="notice warning">{error}</div>}
        <section className="table-card">
          <div className="panel-title settings-panel-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>All Users</span>
            <button className="action-button resume" onClick={() => setCreateOpen(true)}>
              Create User
            </button>
          </div>
          {loading ? (
            <div className="empty-state">Loading…</div>
          ) : (
            <div className="table-wrap">
              <table className="market-table">
                <thead>
                  <tr>
                    <th className="sortable-th" onClick={() => handleSort("id")}>ID {sortCol === "id" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("username")}>Username {sortCol === "username" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("email")}>Email {sortCol === "email" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("full_name")}>Full Name {sortCol === "full_name" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("role")}>Role {sortCol === "role" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("status")}>Status {sortCol === "status" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("approved_paper")}>Paper {sortCol === "approved_paper" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("approved_live")}>Live {sortCol === "approved_live" && (sortDir === "asc" ? "↑" : "↓")}</th>
                    <th className="sortable-th" onClick={() => handleSort("engine_running")}>
                      Engine {sortCol === "engine_running" && (sortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th className="sortable-th" onClick={() => handleSort("active_strategies")}>
                      Strategies {sortCol === "active_strategies" && (sortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUsers.length === 0 ? (
                    <tr>
                      <td colSpan={11} className="empty-state">
                        No users yet. Create one to get started.
                      </td>
                    </tr>
                  ) : (
                    sortedUsers.map((u) => (
                      <tr key={u.id}>
                        <td>{u.id}</td>
                        <td>{u.username}</td>
                        <td>{u.email || "—"}</td>
                        <td>{u.full_name || "—"}</td>
                        <td>
                          <span className={`chip ${u.role === "ADMIN" ? "chip-status-active" : "chip-status-paused"}`}>
                            {u.role}
                          </span>
                        </td>
                        <td>{u.status}</td>
                        <td>
                          <button
                            className={`chip ${u.approved_paper ? "chip-status-active" : "chip-status-paused"}`}
                            onClick={() => toggleApproval(u.id, "approved_paper", u.approved_paper)}
                            title="Click to toggle Paper approval"
                          >
                            {u.approved_paper ? "Yes" : "No"}
                          </button>
                        </td>
                        <td>
                          <button
                            className={`chip ${u.approved_live ? "chip-status-active" : "chip-status-paused"}`}
                            onClick={() => toggleApproval(u.id, "approved_live", u.approved_live)}
                            title="Click to toggle Live approval"
                          >
                            {u.approved_live ? "Yes" : "No"}
                          </button>
                        </td>
                        <td>
                          <span
                            className={`chip ${u.engine_running ? "chip-status-active" : "chip-status-paused"}`}
                            title={`Trade mode: ${(u.engine_mode || "PAPER").toUpperCase()}`}
                          >
                            {u.engine_running ? "Running" : "Stopped"}
                          </span>
                          <div className="settings-hint" style={{ marginTop: 4, fontSize: "0.75rem", opacity: 0.85 }}>
                            {(u.engine_mode || "PAPER").toUpperCase()}
                          </div>
                        </td>
                        <td className="admin-user-strategies-cell">
                          {!u.active_strategies?.length ? (
                            <span className="chip chip-strategy-none" title="No ACTIVE marketplace subscription">
                              None
                            </span>
                          ) : (
                            <div className="admin-user-strategies-chips">
                              {u.active_strategies.map((s) => (
                                <span
                                  key={`${u.id}-${s.strategy_id}-${s.strategy_version}`}
                                  className="chip chip-strategy-sub"
                                  title={`${s.strategy_id} ${s.strategy_version}`}
                                >
                                  {s.display_name || `${s.strategy_id} v${s.strategy_version}`}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                        <td>—</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
        <section className="table-card" style={{ marginTop: 20 }}>
          <div className="panel-title settings-panel-title">Platform risk (kill switch)</div>
          {platform?.schema_ready === false && (
            <div className="notice warning" style={{ margin: "0 1rem 1rem" }}>
              Run DB migration <code>platform_risk_schema.sql</code> (or <code>apply_db_schema.py</code>) so the trading pause table exists.
            </div>
          )}
          {platformErr && <div className="notice error" style={{ margin: "0 1rem 1rem" }}>{platformErr}</div>}
          <div style={{ padding: "0 1rem 1.25rem", display: "flex", flexDirection: "column", gap: 12, maxWidth: 520 }}>
            <p className="settings-hint" style={{ margin: 0 }}>
              When <b>paused</b>, no new trades execute (manual or auto) for any user until resumed.
            </p>
            <label className="field" style={{ margin: 0 }}>
              <span>Optional reason (shown to users)</span>
              <input
                type="text"
                className="control-input"
                value={platformReason}
                onChange={(e) => setPlatformReason(e.target.value)}
                placeholder="e.g. Maintenance / volatility event"
                disabled={platformSaving}
              />
            </label>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <button
                type="button"
                className="action-button pause"
                disabled={platformSaving || platform?.trading_paused}
                onClick={() => savePlatformPause(true)}
              >
                {platformSaving ? "…" : "Pause all new trades"}
              </button>
              <button
                type="button"
                className="action-button resume"
                disabled={platformSaving || !platform?.trading_paused}
                onClick={() => savePlatformPause(false)}
              >
                Resume trading
              </button>
              {platform?.updated_at && (
                <span className="settings-hint">Last change: {formatDateTimeIST(platform.updated_at)}</span>
              )}
            </div>
          </div>
        </section>

        <p className="settings-hint" style={{ marginTop: 12 }}>
          Approve <b>Paper</b> and/or <b>Live</b> for each user. Users can only select Trade Type in Settings if they have approval for that mode.
          <br />
          <b>Engine</b> shows whether Start Trading is on (<b>Running</b> / <b>Stopped</b>) and their <b>PAPER</b> or <b>LIVE</b> mode from the server.
          <br />
          <b>Strategies</b> lists Marketplace subscriptions with status <b>ACTIVE</b> (a user may have more than one).
        </p>
        {createOpen && (
          <CreateUserModal onClose={() => setCreateOpen(false)} onCreated={loadUsers} />
        )}
      </AppFrame>
    </AdminGuard>
  );
}
