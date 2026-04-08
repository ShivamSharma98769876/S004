"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { LandingWidgetHelp } from "@/components/landing/LandingDashWidgets";
import {
  isNseCashSessionNow,
  nseCashSessionBoundsUtcMs,
  toYmdIST,
  utcMsForIstWallClock,
} from "@/lib/datetime_ist";

export type OiWallLeg = {
  strike: number;
  oi: number;
  oiChgPct: number;
  ltp?: number | null;
  iv?: number | null;
  buildup: string;
  distanceFromSpotPts: number;
  positionVsSpot: string;
  sellerNote: string;
  thetaHint?: string | null;
};

export type SpotTrailPoint = { ts: number; spot: number };

export type OiWallsPayload = {
  /** ok | no_broker | no_expiries | chain_error | no_rows | zero_oi */
  status?: string;
  detail?: string;
  expiry: string;
  spot: number;
  /** Server 5m NIFTY closes for today’s session (UTC ms + spot); drives continuous spot trace. */
  spotTrail?: SpotTrailPoint[] | null;
  ceLeaders: OiWallLeg[];
  peLeaders: OiWallLeg[];
  pinRangeHint: string | null;
  windowNote: string;
};

function statusFallback(status: string | undefined): string {
  switch (status) {
    case "no_broker":
      return "No Zerodha market-data session for your login (see server detail or Settings → Brokers).";
    case "no_expiries":
      return "NIFTY expiries not loaded (run NFO bootstrap on the server).";
    case "chain_error":
      return "Option chain failed (timeout, rate limit, or broker error).";
    case "zero_oi":
      return "OI is zero on all strikes in this window.";
    case "no_rows":
      return "Chain returned no strikes.";
    default:
      return "Live OI is not available right now.";
  }
}

function fmtContracts(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "—";
  return n.toLocaleString("en-IN");
}

function fmtOiChg(pct: number): { text: string; tone: "up" | "down" | "flat" } {
  if (!Number.isFinite(pct)) return { text: "—", tone: "flat" };
  if (Math.abs(pct) < 0.05) return { text: "0%", tone: "flat" };
  const tone = pct > 0 ? "up" : "down";
  return { text: `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`, tone };
}

const FIVE_MIN_MS = 5 * 60 * 1000;
/** Match TrendPulseChart: PLOT_H 300, PX_PER_SAMPLE 16, MIN_PLOT_W 520 */
const OI_SPOT_PLOT_H = 300;
const OI_SPOT_PX_PER_SLOT = 16;
const OI_SPOT_MIN_PLOT_W = 520;
const OI_SPOT_Y_AXIS_REM = 2.85;

function fmtHmIST(utcMs: number): string {
  return new Intl.DateTimeFormat("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  }).format(new Date(utcMs));
}

/** Monotone cubic (Fritsch–Carlson) through samples; x non-decreasing — no overshoot between points (time-series safe). */
function spotPathMonotone(points: { x: number; y: number }[]): string {
  const n = points.length;
  if (n < 2) return "";
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const f = (v: number) => v.toFixed(2);
  if (n === 2) return `M ${f(xs[0]!)} ${f(ys[0]!)} L ${f(xs[1]!)} ${f(ys[1]!)}`;

  const mSeg: number[] = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    const dx = xs[i + 1]! - xs[i]!;
    mSeg[i] = dx === 0 ? 0 : (ys[i + 1]! - ys[i]!) / dx;
  }

  const d: number[] = new Array(n).fill(0);
  d[0] = mSeg[0]!;
  for (let i = 1; i < n - 1; i++) {
    if (mSeg[i - 1] === 0 || mSeg[i] === 0 || mSeg[i - 1]! * mSeg[i]! < 0) d[i] = 0;
    else {
      const h0 = xs[i]! - xs[i - 1]!;
      const h1 = xs[i + 1]! - xs[i]!;
      const w0 = 2 * h1 + h0;
      const w1 = h1 + 2 * h0;
      d[i] = (w0 + w1) / (w0 / mSeg[i - 1]! + w1 / mSeg[i]!);
    }
  }
  d[n - 1] = mSeg[n - 2]!;

  let path = `M ${f(xs[0]!)} ${f(ys[0]!)}`;
  for (let i = 0; i < n - 1; i++) {
    const h = xs[i + 1]! - xs[i]!;
    if (h === 0) continue;
    const x1 = xs[i]! + h / 3;
    const y1 = ys[i]! + (d[i]! * h) / 3;
    const x2 = xs[i + 1]! - h / 3;
    const y2 = ys[i + 1]! - (d[i + 1]! * h) / 3;
    path += ` C ${f(x1)} ${f(y1)}, ${f(x2)} ${f(y2)}, ${f(xs[i + 1]!)} ${f(ys[i + 1]!)}`;
  }
  return path;
}

/** CE walls: higher strike first — dark green (R2), teal (R1). PE walls: lower strike first — pink (S1), orange (S2). */
const WALL_CE_STROKES = ["rgba(22, 101, 52, 0.92)", "rgba(45, 212, 191, 0.88)"] as const;
const WALL_PE_STROKES = ["rgba(244, 114, 182, 0.9)", "rgba(251, 146, 60, 0.9)"] as const;

function OiSpotSessionChart({
  trail,
  resistanceStrikes,
  supportStrikes,
  liveSpot,
  istYmd,
}: {
  trail: SpotTrailPoint[];
  resistanceStrikes: number[];
  supportStrikes: number[];
  liveSpot: number;
  /** IST calendar day for session bounds when trail is still empty */
  istYmd: string;
}) {
  const gid = useId().replace(/:/g, "");
  const scrollRef = useRef<HTMLDivElement>(null);
  const [chartTick, setChartTick] = useState(0);
  useEffect(() => {
    if (!isNseCashSessionNow()) return;
    const id = window.setInterval(() => setChartTick((n) => n + 1), 30_000);
    return () => window.clearInterval(id);
  }, [istYmd]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !isNseCashSessionNow()) return;
    el.scrollLeft = el.scrollWidth - el.clientWidth;
  }, [trail.length, chartTick]);

  const ceWalls = useMemo(
    () => [...resistanceStrikes].filter((s) => Number.isFinite(s) && s > 0).sort((a, b) => b - a),
    [resistanceStrikes],
  );
  const peWalls = useMemo(
    () => [...supportStrikes].filter((s) => Number.isFinite(s) && s > 0).sort((a, b) => a - b),
    [supportStrikes],
  );

  const chart = useMemo(() => {
    const [y0, m0, d0] = istYmd.split("-").map((x) => Number(x));
    const refTs =
      trail.length > 0 ? trail[trail.length - 1]!.ts : utcMsForIstWallClock(y0, m0, d0, 10, 0);
    const { open, close } = nseCashSessionBoundsUtcMs(refTs);
    const todayYmd = toYmdIST();
    const liveSession = isNseCashSessionNow();
    const nowMs = Date.now();
    const nowClamped = Math.min(Math.max(nowMs, open), close);
    const endTs =
      istYmd === todayYmd && liveSession ? Math.min(nowMs, close) : close;

    const lastSlot = Math.max(0, Math.floor((endTs - open) / FIVE_MIN_MS));
    const n = lastSlot + 1;

    const sortedTrail = [...trail].sort((a, b) => a.ts - b.ts);
    const perSlot: (number | null)[] = Array(n).fill(null);
    for (const p of sortedTrail) {
      if (!Number.isFinite(p.spot) || p.spot <= 0) continue;
      const i = Math.min(n - 1, Math.max(0, Math.floor((p.ts - open) / FIVE_MIN_MS)));
      perSlot[i] = p.spot;
    }

    const filled: (number | null)[] = Array(n).fill(null);
    let carry: number | null = null;
    for (let i = 0; i < n; i++) {
      if (perSlot[i] != null) carry = perSlot[i];
      filled[i] = carry;
    }

    const liveIdx = Math.min(n - 1, Math.max(0, Math.floor((nowClamped - open) / FIVE_MIN_MS)));
    if (liveSession && Number.isFinite(liveSpot) && liveSpot > 0) {
      filled[liveIdx] = liveSpot;
    }

    let firstIdx = -1;
    for (let i = 0; i < n; i++) {
      if (filled[i] != null) {
        firstIdx = i;
        break;
      }
    }

    const plotW = n <= 1 ? OI_SPOT_MIN_PLOT_W : Math.max(OI_SPOT_MIN_PLOT_W, (n - 1) * OI_SPOT_PX_PER_SLOT);
    const xAt = (i: number) => (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);

    const strikeAll = [...resistanceStrikes, ...supportStrikes].filter((s) => Number.isFinite(s) && s > 0);
    const spotVals = filled.filter((v): v is number => v != null && Number.isFinite(v));
    const yCandidates = [...spotVals, liveSpot, ...strikeAll].filter((v) => Number.isFinite(v) && v > 0);
    if (yCandidates.length === 0) return null;

    let yLo = Math.min(...yCandidates);
    let yHi = Math.max(...yCandidates);
    if (yHi - yLo < 1e-6) {
      yLo -= 1;
      yHi += 1;
    }
    const yPad = Math.max((yHi - yLo) * 0.06, 18);
    yLo -= yPad;
    yHi += yPad;
    const ySpan = Math.max(yHi - yLo, 1);
    const yn = (v: number) => OI_SPOT_PLOT_H - ((v - yLo) / ySpan) * OI_SPOT_PLOT_H;
    const syPx = yn;

    const yTickCount = 4;
    const yTicks: { val: number; ySvg: number; label: string }[] = [];
    for (let j = 0; j <= yTickCount; j++) {
      const val = yHi - (j / yTickCount) * (yHi - yLo);
      yTicks.push({ val, ySvg: yn(val), label: Math.round(val).toLocaleString("en-IN") });
    }

    const yMidVal = (yLo + yHi) / 2;
    const yMidSvg = yn(yMidVal);

    const spotPts: { x: number; y: number }[] = [];
    if (firstIdx >= 0) {
      for (let i = firstIdx; i < n; i++) {
        const v = filled[i];
        if (v == null) continue;
        spotPts.push({ x: xAt(i), y: yn(v) });
      }
    }

    const spotPathD = spotPts.length >= 2 ? spotPathMonotone(spotPts) : "";
    const drawLine = spotPts.length >= 2;
    const lastPt = spotPts.length > 0 ? spotPts[spotPts.length - 1]! : { x: xAt(liveIdx), y: yn(liveSpot) };

    const xTicks: { idx: number; x: number; label: string; ts: number }[] = [];
    for (let i = 0; i < n; i++) {
      const ts = open + i * FIVE_MIN_MS;
      xTicks.push({ idx: i, x: xAt(i), label: fmtHmIST(ts), ts });
    }

    return {
      plotW,
      n,
      open,
      close,
      endTs,
      yLo,
      yHi,
      yMidSvg,
      yTicks,
      syPx,
      spotPathD,
      drawLine,
      lastPt,
      xTicks,
      firstIdx,
      sessionLabel: `${fmtHmIST(open)}–${fmtHmIST(close)} IST`,
    };
  }, [trail, resistanceStrikes, supportStrikes, liveSpot, istYmd, chartTick]);

  if (!chart) return null;

  return (
    <div
      className="landing-oi-chart"
      role="img"
      aria-label={`NIFTY spot vs CE and PE OI strikes. Session ${chart.sessionLabel}.`}
    >
      <div className="landing-oi-chart-head">
        <span className="landing-oi-chart-title">Spot vs OI walls</span>
        <span className="landing-oi-chart-sub muted">
          Visual layout aligned with TrendPulse Z: 5m columns, dark panel, faint grid, neon spot trace. R2/R1 = CE walls,
          S1/S2 = PE walls (labels inside plot left).
        </span>
      </div>
      <p className="landing-oi-chart-window muted">{chart.sessionLabel}</p>

      <div ref={scrollRef} className="landing-oi-spot-scroll" role="region" aria-label="Spot vs OI walls chart">
        <div className="landing-oi-spot-scroll-track">
          <div
            className="landing-oi-spot-y-axis landing-oi-spot-y-axis--sticky"
            aria-hidden
            style={{ width: `${OI_SPOT_Y_AXIS_REM}rem`, height: OI_SPOT_PLOT_H }}
          >
            <span className="landing-oi-spot-y-title">NIFTY</span>
            {[...chart.yTicks]
              .sort((a, b) => b.val - a.val)
              .map((tk, i) => (
                <span key={`y-${i}`} className="landing-oi-spot-y-tick">
                  {tk.label}
                </span>
              ))}
          </div>

          <div className="landing-oi-spot-plot-col" style={{ width: chart.plotW }}>
            <svg
              className="landing-oi-spot-svg"
              width={chart.plotW}
              height={OI_SPOT_PLOT_H}
              viewBox={`0 0 ${chart.plotW} ${OI_SPOT_PLOT_H}`}
              preserveAspectRatio="xMinYMid meet"
              data-session-tick={chartTick}
            >
              <defs>
                <clipPath id={`oiSpotClip-${gid}`}>
                  <rect x={0} y={0} width={chart.plotW} height={OI_SPOT_PLOT_H} />
                </clipPath>
                <linearGradient id={`oiSpotLineGrad-${gid}`} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="rgb(94, 234, 212)" stopOpacity="1" />
                  <stop offset="45%" stopColor="rgb(34, 211, 238)" stopOpacity="1" />
                  <stop offset="100%" stopColor="rgb(20, 184, 166)" stopOpacity="1" />
                </linearGradient>
                <filter id={`oiSpotGlow-${gid}`} x="-40%" y="-40%" width="180%" height="180%">
                  <feGaussianBlur in="SourceGraphic" stdDeviation="1.35" result="b" />
                  <feMerge>
                    <feMergeNode in="b" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              <rect
                x={0}
                y={0}
                width={chart.plotW}
                height={OI_SPOT_PLOT_H}
                fill="rgb(8, 11, 26)"
                rx={6}
              />

              {chart.yTicks.map((tk, i) => (
                <line
                  key={`gh-${i}`}
                  x1={0}
                  y1={tk.ySvg}
                  x2={chart.plotW}
                  y2={tk.ySvg}
                  stroke="rgba(148, 163, 184, 0.1)"
                  strokeWidth={1}
                />
              ))}

              {chart.yMidSvg >= 0 && chart.yMidSvg <= OI_SPOT_PLOT_H ? (
                <line
                  x1={0}
                  y1={chart.yMidSvg}
                  x2={chart.plotW}
                  y2={chart.yMidSvg}
                  stroke="rgba(203, 213, 225, 0.38)"
                  strokeWidth={1}
                  strokeDasharray="4 6"
                  pointerEvents="none"
                />
              ) : null}

              <g clipPath={`url(#oiSpotClip-${gid})`}>
                {ceWalls.map((strike, i) => (
                  <line
                    key={`r-${strike}-${i}`}
                    x1={0}
                    y1={chart.syPx(strike)}
                    x2={chart.plotW}
                    y2={chart.syPx(strike)}
                    stroke={WALL_CE_STROKES[Math.min(i, WALL_CE_STROKES.length - 1)]!}
                    strokeWidth={2.25}
                    strokeOpacity={0.72}
                  >
                    <title>{`${i === 0 ? "R2" : "R1"} CE wall ${strike.toLocaleString("en-IN")}`}</title>
                  </line>
                ))}
                {peWalls.map((strike, i) => (
                  <line
                    key={`s-${strike}-${i}`}
                    x1={0}
                    y1={chart.syPx(strike)}
                    x2={chart.plotW}
                    y2={chart.syPx(strike)}
                    stroke={WALL_PE_STROKES[Math.min(i, WALL_PE_STROKES.length - 1)]!}
                    strokeWidth={2.25}
                    strokeOpacity={0.72}
                  >
                    <title>{`${i === 0 ? "S1" : "S2"} PE wall ${strike.toLocaleString("en-IN")}`}</title>
                  </line>
                ))}
              </g>

              <g pointerEvents="none" aria-hidden>
                {ceWalls.map((strike, i) => {
                  const role = i === 0 ? "R2" : "R1";
                  const fill = WALL_CE_STROKES[Math.min(i, WALL_CE_STROKES.length - 1)]!;
                  const y = chart.syPx(strike);
                  return (
                    <text
                      key={`ce-lab-${strike}-${i}`}
                      x={8}
                      y={y}
                      dy={4}
                      textAnchor="start"
                      fill={fill}
                      fillOpacity={0.92}
                      fontSize={10}
                      fontWeight={700}
                      stroke="rgb(8,11,26)"
                      strokeWidth={2.5}
                      paintOrder="stroke"
                    >
                      {role} {strike.toLocaleString("en-IN")}
                    </text>
                  );
                })}
                {peWalls.map((strike, i) => {
                  const role = i === 0 ? "S1" : "S2";
                  const fill = WALL_PE_STROKES[Math.min(i, WALL_PE_STROKES.length - 1)]!;
                  const y = chart.syPx(strike);
                  return (
                    <text
                      key={`pe-lab-${strike}-${i}`}
                      x={8}
                      y={y}
                      dy={4}
                      textAnchor="start"
                      fill={fill}
                      fillOpacity={0.92}
                      fontSize={10}
                      fontWeight={700}
                      stroke="rgb(8,11,26)"
                      strokeWidth={2.5}
                      paintOrder="stroke"
                    >
                      {role} {strike.toLocaleString("en-IN")}
                    </text>
                  );
                })}
              </g>

              <rect
                x={0}
                y={0}
                width={chart.plotW}
                height={OI_SPOT_PLOT_H}
                fill="none"
                stroke="rgba(71, 85, 105, 0.55)"
                strokeWidth={1}
                rx={6}
              />

              <g clipPath={`url(#oiSpotClip-${gid})`}>
                {chart.drawLine ? (
                  <>
                    <path
                      d={chart.spotPathD}
                      fill="none"
                      stroke="rgba(45, 212, 191, 0.55)"
                      strokeWidth={5}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                      filter={`url(#oiSpotGlow-${gid})`}
                    />
                    <path
                      d={chart.spotPathD}
                      fill="none"
                      stroke={`url(#oiSpotLineGrad-${gid})`}
                      strokeWidth={2.2}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                    />
                    <circle
                      cx={chart.lastPt.x}
                      cy={chart.lastPt.y}
                      r={5.5}
                      fill="rgb(204, 251, 241)"
                      stroke="rgb(15, 23, 42)"
                      strokeWidth={1.2}
                    >
                      <title>{`Spot ${liveSpot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}</title>
                    </circle>
                  </>
                ) : (
                  <circle
                    cx={chart.lastPt.x}
                    cy={chart.lastPt.y}
                    r={6.5}
                    fill="rgb(94, 234, 212)"
                    stroke="rgb(15,23,42)"
                    strokeWidth={1.2}
                  >
                    <title>{`Spot ${liveSpot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`}</title>
                  </circle>
                )}
              </g>
            </svg>

            <div
              className="landing-oi-spot-x-axis landing-oi-spot-x-axis--dense"
              style={{ width: chart.plotW }}
            >
              {chart.xTicks.map((t, i) => (
                <span
                  key={`${t.idx}-${i}`}
                  className="landing-oi-spot-x-tick"
                  style={{ left: `${(t.x / chart.plotW) * 100}%` }}
                >
                  {t.label}
                </span>
              ))}
            </div>
            <p className="landing-oi-spot-x-caption">
              Time — one column per 5m slot from session open (~5 min). Plot ends at <strong>now</strong> while the market
              is open. <strong>Current session day only.</strong> Use horizontal scrollbar to pan.
            </p>
            <p className="landing-oi-spot-session-line">Session {istYmd} (IST)</p>
          </div>
        </div>
      </div>

      <ul className="landing-oi-chart-legend" aria-hidden>
        <li>
          <span className="landing-oi-chart-line landing-oi-chart-line--spot" /> Spot
        </li>
        <li>
          <span className="landing-oi-chart-line landing-oi-chart-line--res2" />
          <span className="landing-oi-chart-line landing-oi-chart-line--res1" /> CE — R2 (higher strike) · R1
        </li>
        <li>
          <span className="landing-oi-chart-line landing-oi-chart-line--sup1" />
          <span className="landing-oi-chart-line landing-oi-chart-line--sup2" /> PE — S1 (lower strike) · S2
        </li>
      </ul>
      <p className="landing-oi-chart-foot muted">
        Spot trace uses 5m NIFTY index closes for the session when available, plus forward-fill across empty buckets. The
        live quote updates only the current 5m slot (no flat extension to session close).
      </p>
    </div>
  );
}

function BuildupPill({ text }: { text: string }) {
  const t = (text || "").toLowerCase();
  let cls = "landing-oi-pill landing-oi-pill--neutral";
  if (t.includes("long") && t.includes("build")) cls = "landing-oi-pill landing-oi-pill--longb";
  else if (t.includes("short") && t.includes("build")) cls = "landing-oi-pill landing-oi-pill--shortb";
  else if (t.includes("cover")) cls = "landing-oi-pill landing-oi-pill--cover";
  else if (t.includes("unwind")) cls = "landing-oi-pill landing-oi-pill--unwind";
  return <span className={cls}>{text || "—"}</span>;
}

export default function OiSellerPinboardWidget({
  data,
  loading,
}: {
  data: OiWallsPayload | null;
  loading: boolean;
}) {
  const ce = data?.ceLeaders ?? [];
  const pe = data?.peLeaders ?? [];
  const empty = !loading && ce.length === 0 && pe.length === 0;
  const wallStatus = data?.status;
  const problemText =
    !loading && empty
      ? data?.detail?.trim()
        ? data.detail
        : statusFallback(wallStatus)
      : null;
  const statusBarWarn = Boolean(wallStatus && wallStatus !== "ok");

  const resistanceStrikes = useMemo(
    () => ce.map((c) => c.strike).filter((s) => Number.isFinite(s) && s > 0).slice(0, 2),
    [ce],
  );
  const supportStrikes = useMemo(
    () => pe.map((p) => p.strike).filter((s) => Number.isFinite(s) && s > 0).slice(0, 2),
    [pe],
  );

  const [spotTrail, setSpotTrail] = useState<SpotTrailPoint[]>([]);
  const sessionKeyRef = useRef("");

  const serverSpotTrail = useMemo(() => {
    const raw = data?.spotTrail;
    if (!Array.isArray(raw)) return [] as SpotTrailPoint[];
    return raw
      .filter(
        (p): p is SpotTrailPoint =>
          p != null &&
          typeof p.ts === "number" &&
          Number.isFinite(p.ts) &&
          typeof p.spot === "number" &&
          Number.isFinite(p.spot) &&
          p.spot > 0,
      )
      .map((p) => ({ ts: p.ts, spot: p.spot }))
      .sort((a, b) => a.ts - b.ts);
  }, [data?.spotTrail]);

  useEffect(() => {
    if (!data || empty || !Number.isFinite(data.spot) || data.spot <= 0) return;
    const key = `${toYmdIST()}|${data.expiry ?? ""}`;
    if (sessionKeyRef.current !== key) {
      sessionKeyRef.current = key;
      setSpotTrail([]);
      return;
    }
    if (!isNseCashSessionNow()) return;
    setSpotTrail((prev) => {
      const next = [...prev, { ts: Date.now(), spot: data.spot }];
      return next.length > 2000 ? next.slice(-2000) : next;
    });
  }, [data, empty]);

  const combinedSpotTrail = useMemo(() => {
    const lastSrvTs = serverSpotTrail.length ? serverSpotTrail[serverSpotTrail.length - 1]!.ts : 0;
    const extra = spotTrail.filter((p) => p.ts > lastSrvTs);
    const merged = [...serverSpotTrail, ...extra].sort((a, b) => a.ts - b.ts);
    if (merged.length === 0 && data && Number.isFinite(data.spot) && data.spot > 0) {
      return [{ ts: Date.now(), spot: data.spot }];
    }
    return merged;
  }, [serverSpotTrail, spotTrail, data?.spot]);

  const showSessionChart =
    !empty &&
    data &&
    data.spot > 0 &&
    (resistanceStrikes.length > 0 || supportStrikes.length > 0 || combinedSpotTrail.length > 0);

  const istTodayYmd = toYmdIST();

  return (
    <div className="landing-bento-cell landing-oi-pinboard panel-accent-chain landing-widget-help-host">
      <LandingWidgetHelp
        meaning="Top call and put strikes by open interest in a wide NIFTY window (nearest expiry). OI change % compares to the previous in-memory snapshot from your broker feed."
        usage="Option sellers watch heavy OI as potential magnets or pain points. High OI alone is not entry logic — combine with your risk rules, greeks, and time to expiry. Numbers are contracts in the visible chain slice, not full-exchange totals."
      />
      <header className="landing-bento-head landing-oi-pin-head">
        <div>
          <span className="landing-bento-title">OI walls · seller lens</span>
          <p className="landing-bento-sub">
            Top 2 CE / PE by open interest · change vs last poll · buildup + theta context
          </p>
        </div>
        {data?.expiry ? (
          <span className="landing-oi-expiry-pill" title="Nearest NIFTY expiry used for this window">
            {data.expiry}
          </span>
        ) : loading ? (
          <span className="landing-pill-stat landing-pill-stat--dim">…</span>
        ) : null}
      </header>

      {data?.spot != null && data.spot > 0 ? (
        <p className="landing-oi-spot-ref muted">
          NIFTY ref <strong>{data.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</strong>
        </p>
      ) : null}

      {problemText ? (
        <div
          className={`landing-oi-status-bar ${statusBarWarn ? "landing-oi-status-bar--warn" : "landing-oi-status-bar--soft"}`}
          role="status"
        >
          <span className="landing-oi-status-k">
            {wallStatus && wallStatus !== "ok" ? wallStatus.replace(/_/g, " ") : "Notice"}
          </span>
          <span className="landing-oi-status-msg">{problemText}</span>
        </div>
      ) : null}

      <div className="landing-oi-pin-grid" aria-label="Highest OI strikes">
        <div className="landing-oi-pin-col landing-oi-pin-col--ce">
          <div className="landing-oi-pin-col-head">
            <span className="landing-oi-pin-col-k">Call writers</span>
            <span className="landing-oi-pin-col-sub muted">CE · resistance / cap zone</span>
          </div>
          {loading && !data ? (
            <div className="landing-oi-skel" aria-hidden>
              <span />
              <span />
            </div>
          ) : empty ? (
            <p className="muted landing-oi-empty">
              {problemText ? "See notice above." : "No call OI leaders in this window."}
            </p>
          ) : (
            <ul className="landing-oi-pin-list">
              {ce.map((row, i) => {
                const chg = fmtOiChg(row.oiChgPct);
                return (
                  <li key={`ce-${row.strike}-${i}`} className="landing-oi-pin-card">
                    <div className="landing-oi-pin-rank" aria-hidden>
                      #{i + 1}
                    </div>
                    <div className="landing-oi-pin-main">
                      <div className="landing-oi-pin-row1">
                        <span className="landing-oi-pin-strike">{row.strike}</span>
                        <span className="landing-oi-pin-oi" title="Open interest (contracts in chain window)">
                          {fmtContracts(row.oi)}
                        </span>
                      </div>
                      <div className="landing-oi-pin-row2">
                        <span className={`landing-oi-chg landing-oi-chg--${chg.tone}`}>ΔOI {chg.text}</span>
                        <span className="landing-oi-meta muted">
                          {row.positionVsSpot} · {row.distanceFromSpotPts >= 0 ? "+" : ""}
                          {row.distanceFromSpotPts} pts
                        </span>
                      </div>
                      <div className="landing-oi-pin-row3">
                        <BuildupPill text={row.buildup} />
                        {row.iv != null ? <span className="landing-oi-iv muted">IV {Number(row.iv).toFixed(1)}%</span> : null}
                      </div>
                      <p className="landing-oi-seller-note">{row.sellerNote}</p>
                      {row.thetaHint ? <p className="landing-oi-theta-hint muted">{row.thetaHint}</p> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="landing-oi-pin-mid" aria-hidden>
          <div className="landing-oi-pin-mid-line" />
          <span className="landing-oi-pin-mid-label">vs</span>
          <div className="landing-oi-pin-mid-line" />
        </div>

        <div className="landing-oi-pin-col landing-oi-pin-col--pe">
          <div className="landing-oi-pin-col-head">
            <span className="landing-oi-pin-col-k">Put writers</span>
            <span className="landing-oi-pin-col-sub muted">PE · support / floor zone</span>
          </div>
          {loading && !data ? (
            <div className="landing-oi-skel" aria-hidden>
              <span />
              <span />
            </div>
          ) : empty ? (
            <p className="muted landing-oi-empty">{problemText ? "See notice above." : "No put OI leaders."}</p>
          ) : (
            <ul className="landing-oi-pin-list">
              {pe.map((row, i) => {
                const chg = fmtOiChg(row.oiChgPct);
                return (
                  <li key={`pe-${row.strike}-${i}`} className="landing-oi-pin-card">
                    <div className="landing-oi-pin-rank" aria-hidden>
                      #{i + 1}
                    </div>
                    <div className="landing-oi-pin-main">
                      <div className="landing-oi-pin-row1">
                        <span className="landing-oi-pin-strike">{row.strike}</span>
                        <span className="landing-oi-pin-oi" title="Open interest (contracts in chain window)">
                          {fmtContracts(row.oi)}
                        </span>
                      </div>
                      <div className="landing-oi-pin-row2">
                        <span className={`landing-oi-chg landing-oi-chg--${chg.tone}`}>ΔOI {chg.text}</span>
                        <span className="landing-oi-meta muted">
                          {row.positionVsSpot} · {row.distanceFromSpotPts >= 0 ? "+" : ""}
                          {row.distanceFromSpotPts} pts
                        </span>
                      </div>
                      <div className="landing-oi-pin-row3">
                        <BuildupPill text={row.buildup} />
                        {row.iv != null ? <span className="landing-oi-iv muted">IV {Number(row.iv).toFixed(1)}%</span> : null}
                      </div>
                      <p className="landing-oi-seller-note">{row.sellerNote}</p>
                      {row.thetaHint ? <p className="landing-oi-theta-hint muted">{row.thetaHint}</p> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </div>

      {showSessionChart ? (
        <OiSpotSessionChart
          trail={combinedSpotTrail}
          resistanceStrikes={resistanceStrikes}
          supportStrikes={supportStrikes}
          liveSpot={data!.spot}
          istYmd={istTodayYmd}
        />
      ) : null}

      {data?.pinRangeHint ? <p className="landing-oi-pin-hint">{data.pinRangeHint}</p> : null}
      {data?.windowNote ? <p className="landing-oi-window-note muted">{data.windowNote}</p> : null}
    </div>
  );
}
