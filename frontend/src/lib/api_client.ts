"use client";

// Use relative path in browser so requests go through Next.js proxy (avoids CORS, backend reachable from server)
const API_BASE = typeof window !== "undefined" ? "" : (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000");
const AUTH_KEY = "s004.auth";

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
    if (parsed?.user_id && parsed?.username && parsed?.role) return parsed as AuthUser;
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

export async function apiJson<T>(
  path: string,
  method: JsonMethod = "GET",
  body?: unknown,
  query?: Record<string, string | number | undefined>
): Promise<T> {
  const qs = new URLSearchParams();
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null) qs.set(k, String(v));
    }
  }
  const suffix = qs.size ? `?${qs.toString()}` : "";
  const userId = getCurrentUserId();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${suffix}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": String(userId),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Network error";
    if (msg === "Failed to fetch" || msg.toLowerCase().includes("network")) {
      throw new Error("Cannot reach backend. Ensure the backend is running (e.g. uvicorn) and reachable.");
    }
    throw e;
  }
  let json: unknown;
  try {
    json = await res.json();
  } catch {
    throw new Error(res.ok ? "Invalid response" : `Server error ${res.status}: ${res.statusText}`);
  }
  if (!res.ok) {
    const detail = typeof json === "object" && json !== null && "detail" in json ? (json as { detail?: string }).detail : null;
    throw new Error(detail || res.statusText || `Error ${res.status}`);
  }
  return json as T;
}
