"use client";

import { useEffect, useMemo, useState } from "react";
import AdminGuard from "@/components/AdminGuard";
import AppFrame from "@/components/AppFrame";
import { apiJson } from "@/lib/api_client";

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

export default function AdminUsersPage() {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);

  const loadUsers = async () => {
    setError("");
    try {
      const rows = await apiJson<UserRow[]>("/api/admin/users");
      setUsers(rows ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load users.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadUsers();
  }, []);

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
        subtitle="Create users and approve Paper/Live trade types."
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
                    <th className="sortable-th" onClick={() => handleSort("active_strategies")}>
                      Strategies {sortCol === "active_strategies" && (sortDir === "asc" ? "↑" : "↓")}
                    </th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUsers.length === 0 ? (
                    <tr>
                      <td colSpan={10} className="empty-state">
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
        <p className="settings-hint" style={{ marginTop: 12 }}>
          Approve <b>Paper</b> and/or <b>Live</b> for each user. Users can only select Trade Type in Settings if they have approval for that mode.
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
