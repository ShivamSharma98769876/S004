"use client";

// Use relative path in browser so requests go through Next.js proxy (avoids CORS, backend reachable from server)
const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
const AUTH_KEY = "s004.auth";
const DEFAULT_API_TIMEOUT_MS = 20_000;

/** Live/paper execute may wait on broker APIs (e.g. Zerodha); keep above default list timeout. */
export const API_TIMEOUT_EXECUTE_MS = 90_000;

export type AuthUser = {
  user_id: number;
  username: string;
  role: "ADMIN" | "USER";
  email?: string;
  approved_paper?: boolean;
  approved_live?: boolean;
};

export function getAuth(): AuthUser | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(AUTH_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as AuthUser;
    const uid = parsed?.user_id;
  if (
    uid != null &&
    Number.isFinite(Number(uid)) &&
    Number(uid) > 0 &&
    parsed?.username &&
    parsed?.role
  ) {
    return parsed as AuthUser;
  }
  } catch {
    /* ignore */
  }
  return null;
}

export function setAuth(user: AuthUser): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(AUTH_KEY, JSON.stringify(user));
}

export function clearAuth(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(AUTH_KEY);
}

export function getCurrentUserId(): number {
  const auth = getAuth();
  if (auth) return auth.user_id;
  return 0;
}

export function isAdmin(): boolean {
  return getAuth()?.role === "ADMIN";
}

/** Headers for API requests (includes X-User-Id for auth) */
export function getAuthHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-User-Id": String(getCurrentUserId()),
  };
}

type JsonMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
type ApiJsonOptions = {
  timeoutMs?: number;
};

export async function apiJson<T>(
  path: string,
  method: JsonMethod = "GET",
  body?: unknown,
  query?: Record<string, string | number | undefined>,
  options?: ApiJsonOptions,
): Promise<T> {
  const qs = new URLSearchParams();
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null) qs.set(k, String(v));
    }
  }
  const suffix = qs.size ? `?${qs.toString()}` : "";
  const userId = getCurrentUserId();
  if (userId <= 0) {
    throw new Error("Not signed in. Open /login and sign in again so settings save to your account.");
  }
  let res: Response;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;
  const controller = new AbortController();
  const explicitTimeoutMs = options?.timeoutMs;
  const timeoutMsRaw = Number(
    explicitTimeoutMs ?? process.env.NEXT_PUBLIC_API_TIMEOUT_MS ?? DEFAULT_API_TIMEOUT_MS,
  );
  const maxAllowedMs =
    explicitTimeoutMs != null && Number.isFinite(Number(explicitTimeoutMs)) ? 120_000 : 60_000;
  const timeoutMs = Number.isFinite(timeoutMsRaw)
    ? Math.max(2000, Math.min(maxAllowedMs, timeoutMsRaw))
    : DEFAULT_API_TIMEOUT_MS;
  timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    res = await fetch(`${API_BASE}${path}${suffix}`, {
      method,
      cache: "no-store",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": String(userId),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (e) {
    if (timeoutId) clearTimeout(timeoutId);
    const msg = e instanceof Error ? e.message : "Network error";
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s: ${path}`);
    }
    if (msg === "Failed to fetch" || msg.toLowerCase().includes("network")) {
      throw new Error("Cannot reach backend. Ensure the backend is running (e.g. uvicorn) and reachable.");
    }
    throw e;
  }
  if (timeoutId) clearTimeout(timeoutId);
  const text = await res.text();
  const trimmed = text.trim();
  let json: unknown;
  if (!trimmed) {
    if (!res.ok) {
      throw new Error(`Server error ${res.status}: ${res.statusText || "empty response"}`);
    }
    throw new Error(`Empty response from ${path}`);
  }
  try {
    json = JSON.parse(text) as unknown;
  } catch {
    const snippet = trimmed.replace(/\s+/g, " ").slice(0, 200);
    throw new Error(
      res.ok
        ? `Invalid JSON from ${path}: ${snippet}`
        : `Server error ${res.status}: ${snippet || res.statusText}`,
    );
  }
  if (!res.ok) {
    const detail = typeof json === "object" && json !== null && "detail" in json ? (json as { detail?: string }).detail : null;
    throw new Error(detail || res.statusText || `Error ${res.status}`);
  }
  return json as T;
}

/** Option chain + recommendation engine; call once per tick before fetching recommendations/open so UI stays aligned. */
export async function postTradesRefreshCycle(): Promise<{
  ok: boolean;
  recommendation_engine_run: boolean;
  engine_provider?: "fyers" | "zerodha" | "none";
}> {
  return apiJson<{
    ok: boolean;
    recommendation_engine_run: boolean;
    engine_provider?: "fyers" | "zerodha" | "none";
  }>("/api/trades/refresh-cycle", "POST");
}

export type ObservabilityTradeMarker = {
  kind: string;
  time: number;
  tradeRef: string;
  symbol: string;
  mode: string;
  side: string | null;
  price: number;
};

export type ObservabilitySeriesBase = {
  ok?: boolean;
  reason?: string;
  times?: number[];
  [key: string]: unknown;
};

/** SuperTrend Trail: same bar as `evaluate_supertrend_trail_signal` (last closed candle in series). */
export type ObservabilitySignalBar = {
  ok?: boolean;
  reason?: string | null;
  direction?: string | null;
  close?: number | null;
  closePrev?: number | null;
  emaFast?: number | null;
  emaSlow?: number | null;
  emaFastPrev?: number | null;
  emaSlowPrev?: number | null;
};

export type ObservabilityPanel = {
  kind: "stochastic_bnf" | "supertrend_trail" | "ps_vs_mtf";
  strategyId: string;
  strategyVersion: string;
  displayName: string;
  instrument: string;
  interval: string;
  series: ObservabilitySeriesBase;
  markers: ObservabilityTradeMarker[];
  optionVwap?: { time: number; value: number }[];
  optionSymbol?: string | null;
  signalBar?: ObservabilitySignalBar;
};

export type ObservabilitySnapshot = {
  fetchedAt: number;
  brokerSource: string;
  panels: ObservabilityPanel[];
  error?: string;
};

export async function fetchObservabilitySnapshot(refresh?: boolean): Promise<ObservabilitySnapshot> {
  return apiJson<ObservabilitySnapshot>(
    "/api/observability/snapshot",
    "GET",
    undefined,
    refresh ? { refresh: "true" } : undefined,
    { timeoutMs: 90_000 },
  );
}
