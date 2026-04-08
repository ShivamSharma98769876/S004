/**
 * All user-visible dates/times use **IST (Asia/Kolkata)** so display matches NSE session
 * regardless of browser/OS timezone (e.g. UTC on corporate laptops).
 *
 * **API contract:** Postgres `TIMESTAMP WITHOUT TIME ZONE` for trade times is stored as UTC
 * wall time. FastAPI/asyncpg serialize that as ISO strings **without** `Z`. ECMAScript treats
 * `2026-03-24T04:51:12` as *local* time, which breaks IST display on machines not set to UTC.
 * We treat zone-less API datetimes as UTC, then format with `timeZone: Asia/Kolkata`.
 */

export const APP_TIMEZONE = "Asia/Kolkata";

/** Parse trade / server timestamps: naive ISO → UTC instant; pass-through if `Z` or offset present. */
export function parseBackendUtcNaive(input: string | number | Date | null | undefined): Date {
  if (input == null || input === "") return new Date(NaN);
  if (input instanceof Date) return new Date(input.getTime());
  if (typeof input === "number") return new Date(input);
  const s0 = String(input).trim();
  if (/Z$/i.test(s0) || /[+-]\d{2}:\d{2}$/.test(s0)) return new Date(s0);
  if (/^\d{4}-\d{2}-\d{2}$/.test(s0)) return new Date(`${s0}T00:00:00.000Z`);
  const normalized = s0.includes("T") ? s0 : s0.replace(" ", "T");
  if (!/^\d{4}-\d{2}-\d{2}T\d/.test(normalized)) return new Date(normalized);
  return new Date(`${normalized}Z`);
}

/** Milliseconds for sorting; 0 if missing/invalid. */
export function backendInstantMs(iso: string | null | undefined): number {
  const t = parseBackendUtcNaive(iso ?? "").getTime();
  return Number.isNaN(t) ? 0 : t;
}

function parseInstant(input: string | number | Date): Date {
  return input instanceof Date ? input : parseBackendUtcNaive(input);
}

export function formatTimeIST(
  input: string | number | Date | null | undefined,
  opts?: { seconds?: boolean; fallback?: string; hour12?: boolean; appendIstLabel?: boolean },
): string {
  const fb = opts?.fallback ?? "—";
  if (input == null) return fb;
  try {
    const d = parseInstant(input);
    if (Number.isNaN(d.getTime())) return fb;
    const s = d.toLocaleTimeString("en-IN", {
      hour12: opts?.hour12 ?? false,
      hour: "2-digit",
      minute: "2-digit",
      ...(opts?.seconds ? { second: "2-digit" } : {}),
      timeZone: APP_TIMEZONE,
    });
    return opts?.appendIstLabel ? `${s} IST` : s;
  } catch {
    return fb;
  }
}

/** Date + time in IST (compact, for tooltips / admin / analytics “updated”). */
export function formatDateTimeIST(
  input: string | number | Date | null | undefined,
  fallback = "—",
  opts?: { seconds?: boolean; appendIstLabel?: boolean },
): string {
  if (input == null) return fallback;
  try {
    const d = parseInstant(input);
    if (Number.isNaN(d.getTime())) return fallback;
    const s = d.toLocaleString("en-IN", {
      hour12: false,
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      ...(opts?.seconds ? { second: "2-digit" } : {}),
      timeZone: APP_TIMEZONE,
    });
    return opts?.appendIstLabel ? `${s} IST` : s;
  } catch {
    return fallback;
  }
}

/**
 * Calendar date in IST as `YYYY-MM-DD` (for query params, CSV filenames, presets).
 * Pass any instant; the **IST** calendar date is used (not UTC midnight).
 */
export function toYmdIST(d: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: APP_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

/** Shift a Date by whole days in IST is approximated via UTC ms (good enough for report presets). */
export function addDaysIST(base: Date, days: number): Date {
  return new Date(base.getTime() + days * 24 * 60 * 60 * 1000);
}

/** Live clock string in IST (e.g. dashboard header). */
export function formatClockNowIST(): string {
  return new Date().toLocaleTimeString("en-IN", {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: APP_TIMEZONE,
  });
}

/** Short day+month label in IST (e.g. performance charts). */
export function formatDateShortIST(input: string | number | Date | null | undefined, fallback = "—"): string {
  if (input == null) return fallback;
  try {
    const d = parseInstant(input);
    if (Number.isNaN(d.getTime())) return fallback;
    return d.toLocaleDateString("en-IN", {
      day: "numeric",
      month: "short",
      timeZone: APP_TIMEZONE,
    });
  } catch {
    return fallback;
  }
}

/** Numeric YYYY-MM-DD in IST for table columns (reports). */
export function formatDateYmdIST(input: string | number | Date | null | undefined, fallback = "--"): string {
  if (input == null) return fallback;
  try {
    const d = parseInstant(input);
    if (Number.isNaN(d.getTime())) return fallback;
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: APP_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).format(d);
  } catch {
    return fallback;
  }
}

/** Options shared with chart axis formatters (TrendPulse, etc.). */
export const intlTimeOptionsIST: Intl.DateTimeFormatOptions = {
  hour12: false,
  hour: "2-digit",
  minute: "2-digit",
  timeZone: APP_TIMEZONE,
};

export const intlDateTimeOptionsIST: Intl.DateTimeFormatOptions = {
  hour12: false,
  day: "2-digit",
  month: "short",
  ...intlTimeOptionsIST,
};

/** NSE NIFTY cash session (IST, no DST). */
const NSE_OPEN_IST = { h: 9, m: 15 };
const NSE_CLOSE_IST = { h: 15, m: 30 };

function istYmdParts(utcMs: number): { y: number; mo: number; d: number } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: APP_TIMEZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(utcMs));
  const y = Number(parts.find((p) => p.type === "year")?.value);
  const mo = Number(parts.find((p) => p.type === "month")?.value);
  const d = Number(parts.find((p) => p.type === "day")?.value);
  return { y, mo, d };
}

/** UTC epoch ms for a calendar instant in IST (Asia/Kolkata, +05:30). */
export function utcMsForIstWallClock(y: number, month: number, day: number, hour: number, minute: number): number {
  const pad = (n: number) => String(n).padStart(2, "0");
  return Date.parse(`${y}-${pad(month)}-${pad(day)}T${pad(hour)}:${pad(minute)}:00+05:30`);
}

/** Today’s NSE cash open / close in UTC ms from a reference instant (default now). */
export function nseCashSessionBoundsUtcMs(refUtcMs: number = Date.now()): { open: number; close: number } {
  const { y, mo, d } = istYmdParts(refUtcMs);
  return {
    open: utcMsForIstWallClock(y, mo, d, NSE_OPEN_IST.h, NSE_OPEN_IST.m),
    close: utcMsForIstWallClock(y, mo, d, NSE_CLOSE_IST.h, NSE_CLOSE_IST.m),
  };
}

/** Whether `utcMs` falls inside today’s NSE cash session in IST. */
export function isNseCashSessionNow(utcMs: number = Date.now()): boolean {
  const { open, close } = nseCashSessionBoundsUtcMs(utcMs);
  return utcMs >= open && utcMs <= close;
}
