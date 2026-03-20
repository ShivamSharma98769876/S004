"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { setAuth, type AuthUser } from "@/lib/api_client";
import { DESIGN_TOKENS } from "@/design/tokens";

// Use relative path so request goes through Next.js proxy (avoids CORS, works when backend port differs)
const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [username, setUsername] = useState("");
  const [useLegacy, setUseLegacy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const body = useLegacy
        ? { username: username.trim() }
        : { email: email.trim().toLowerCase(), password };
      if (useLegacy && !username.trim()) {
        setError("Enter your username.");
        setLoading(false);
        return;
      }
      if (!useLegacy && (!email.trim() || !password)) {
        setError("Email and password are required.");
        setLoading(false);
        return;
      }
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const json = await res.json();
      if (!res.ok) {
        throw new Error(json?.detail || "Login failed.");
      }
      const user = json as AuthUser & { approved_paper?: boolean; approved_live?: boolean };
      setAuth({
        user_id: user.user_id,
        username: user.username,
        role: user.role,
        email: user.email,
        approved_paper: user.approved_paper,
        approved_live: user.approved_live,
      });
      router.push("/dashboard");
      router.refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Login failed.";
      if (msg === "Failed to fetch" || msg.toLowerCase().includes("fetch") || msg.toLowerCase().includes("network")) {
        setError("Cannot reach backend. Ensure the backend is running (e.g. uvicorn --port 8001) and matches NEXT_PUBLIC_API_URL in .env.local.");
      } else {
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div className="login-brand">
          <h1>{DESIGN_TOKENS.appName}</h1>
          <p>{DESIGN_TOKENS.appSubtitle}</p>
        </div>
        <form onSubmit={handleSubmit} className="login-form">
          {useLegacy ? (
            <label className="login-field">
              <span>Username</span>
              <input
                type="text"
                className="control-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter username"
                autoFocus
                autoComplete="username"
                disabled={loading}
              />
            </label>
          ) : (
            <>
              <label className="login-field">
                <span>Email</span>
                <input
                  type="email"
                  className="control-input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Enter email"
                  autoFocus
                  autoComplete="email"
                  disabled={loading}
                />
              </label>
              <label className="login-field">
                <span>Password</span>
                <input
                  type="password"
                  className="control-input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter password"
                  autoComplete="current-password"
                  disabled={loading}
                />
              </label>
            </>
          )}
          {error && <div className="notice warning login-error">{error}</div>}
          <button type="submit" className="action-button login-submit" disabled={loading}>
            {loading ? "Signing in…" : "Sign in"}
          </button>
          <button
            type="button"
            className="login-toggle-mode"
            onClick={() => setUseLegacy(!useLegacy)}
          >
            {useLegacy ? "Use email & password" : "Use username (legacy)"}
          </button>
        </form>
        <p className="login-hint">
          {useLegacy ? "Use your registered username (e.g. admin, trader1)" : "Use your registered email and password"}
        </p>
      </div>
    </div>
  );
}
