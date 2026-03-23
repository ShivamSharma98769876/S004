"use client";

import { useCallback, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { intlDateTimeOptionsIST, intlTimeOptionsIST } from "@/lib/datetime_ist";

/** Top GENERATED row from s004_trade_recommendations for this strategy (camelCase from API). */
export type TrendPulseRecommendation = {
  recommendationId: string;
  symbol: string;
  instrument: string;
  expiry: string | null;
  side: string;
  entryPrice: number;
  targetPrice: number;
  stopLossPrice: number;
  confidenceScore: number;
  rankValue: number;
  status: string;
  score?: number;
  strike?: number;
  optionType?: string;
};

export type TrendPulseTradeSignal = {
  entryEligible: boolean;
  summary: string;
  cross: string | null;
  htfBias?: string;
  reasonCode?: string;
  adxSt?: number;
  adxMin?: number;
  recommendation?: TrendPulseRecommendation | null;
};

export type TrendPulseEntryEvent = {
  tailIndex: number;
  time: string;
  cross: "bullish" | "bearish";
  htfBias: "bullish" | "bearish" | "neutral";
  psZ: number;
  vsZ: number;
  adxSt: number;
  leg?: string;
  strike?: number;
  optionType?: string;
  /** Filled trade compact symbol at this bar (e.g. NIFTY2632423500PE). */
  optionSymbol?: string;
  /** Plan recommendation symbol on latest bar when no fill yet. */
  planSymbol?: string;
};

export type TrendPulseTradeEvent = {
  tailIndex: number;
  openedAt: string;
  mode: "PAPER" | "LIVE" | string;
  side: string;
  symbol: string;
  tradeRef: string;
  strike?: number;
  optionType?: string;
};

type Props = {
  times: string[];
  psZ: number[];
  vsZ: number[];
  title?: string;
  stIntervalLabel?: string;
  /** e.g. "2025-03-23 (IST)" — plot is one session day; z-scores still use full history server-side */
  sessionDayNote?: string | null;
  tradeSignal?: TrendPulseTradeSignal | null;
  entryEvents?: TrendPulseEntryEvent[];
  tradeEvents?: TrendPulseTradeEvent[];
};

/** Wider plot = one column per ~5m bar; scroll horizontally. */
const PLOT_H = 300;
const PX_PER_SAMPLE = 16;
const MIN_PLOT_W = 520;
const Y_AXIS_W_REM = 2.85;
/** Served from `public/images/trendpulse-rupee-coin.png` — add your transparent PNG there. */
const RUPEE_COIN_HREF = "/images/trendpulse-rupee-coin.png";
/** On-plot marker size (px) — gold coin sits between PS_z and VS_z at the signal bar. */
/** On-plot rupee coin size — 2/3 of prior 40px for a subtler marker. */
const SIGNAL_ICON_PX = (40 * 2) / 3;

function formatTick(t: string): string {
  if (!t) return "";
  try {
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString("en-IN", intlTimeOptionsIST);
  } catch {
    return "";
  }
}

function formatBarHint(t: string): string {
  if (!t) return "";
  try {
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return "";
    const wall = d.toLocaleString("en-IN", intlDateTimeOptionsIST);
    return `${wall} IST`;
  } catch {
    return "";
  }
}

function formatTimeOnlyIST(t: string): string {
  if (!t) return "";
  try {
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return "";
    const hhmmss = d.toLocaleTimeString("en-IN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone: "Asia/Kolkata",
    });
    return `${hhmmss} IST`;
  } catch {
    return "";
  }
}

function barIntervalMinutes(stLabel: string): number {
  const m = String(stLabel).match(/(\d+)\s*m/i);
  if (m) return Math.max(1, parseInt(m[1]!, 10));
  return 5;
}

export default function TrendPulseChart({
  times,
  psZ,
  vsZ,
  title = "TrendPulse Z — signal timeframe",
  stIntervalLabel = "ST",
  sessionDayNote = null,
  tradeSignal,
  entryEvents = [],
  tradeEvents = [],
}: Props) {
  const safeEntryEvents = Array.isArray(entryEvents) ? entryEvents : [];
  const safeTradeEvents = Array.isArray(tradeEvents) ? tradeEvents : [];
  const barMin = barIntervalMinutes(stIntervalLabel);
  const [tip, setTip] = useState<{
    left: number;
    top: number;
    transform: string;
    lines: string[];
  } | null>(null);

  const clearTip = useCallback(() => setTip(null), []);

  /**
   * Tooltip directly above the hit target (the icon rect), viewport coords.
   * Uses the <rect> element — SVG <g> getBoundingClientRect() is often wrong vs screen.
   * Rendered via portal to document.body so position:fixed is not offset by parent transforms.
   */
  const showSignalTipAnchored = useCallback((target: SVGRectElement, lines: string[]) => {
    const rect = target.getBoundingClientRect();
    if (rect.width < 1 && rect.height < 1) return;
    const gap = 6;
    const cx = rect.left + rect.width / 2;
    const estH = 72;
    const placeAbove = rect.top >= estH + 8;
    if (placeAbove) {
      setTip({
        left: cx,
        top: rect.top - gap,
        transform: "translate(-50%, -100%)",
        lines,
      });
    } else {
      setTip({
        left: cx,
        top: rect.bottom + gap,
        transform: "translate(-50%, 0)",
        lines,
      });
    }
  }, []);

  const chart = useMemo(() => {
    const n = Math.min(times.length, psZ.length, vsZ.length);
    if (n < 2) {
      return {
        pathPs: "",
        pathVs: "",
        minY: 0,
        maxY: 1,
        yTicks: [] as { val: number; ySvg: number }[],
        yZero: null as number | null,
        lastX: 0,
        lastYps: 0,
        lastYvs: 0,
        plotW: MIN_PLOT_W,
        xTicks: [] as { x: number; label: string; idx: number }[],
        barsPerLabel: 1,
      };
    }

    const plotW = Math.max(MIN_PLOT_W, (n - 1) * PX_PER_SAMPLE);

    let lo = Math.min(...psZ.slice(0, n), ...vsZ.slice(0, n));
    let hi = Math.max(...psZ.slice(0, n), ...vsZ.slice(0, n));
    if (hi - lo < 1e-6) {
      lo -= 0.5;
      hi += 0.5;
    }
    const pad = (hi - lo) * 0.08;
    lo -= pad;
    hi += pad;

    const xAt = (i: number) => (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yn = (v: number) => PLOT_H - ((v - lo) / (hi - lo)) * PLOT_H;

    const ptsPs = psZ
      .slice(0, n)
      .map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(2)} ${yn(v).toFixed(2)}`);
    const ptsVs = vsZ
      .slice(0, n)
      .map((v, i) => `${i === 0 ? "M" : "L"} ${xAt(i).toFixed(2)} ${yn(v).toFixed(2)}`);

    const yTickCount = 5;
    const yTicks: { val: number; ySvg: number }[] = [];
    for (let j = 0; j < yTickCount; j++) {
      const val = lo + ((hi - lo) * j) / (yTickCount - 1);
      yTicks.push({ val, ySvg: yn(val) });
    }

    let yZero: number | null = null;
    if (lo <= 0 && hi >= 0) {
      yZero = yn(0);
    }

    // One label per bar when interval is ~5m and pixels allow — scroll prevents crush.
    const barsPerLabel = barMin <= 7 ? 1 : Math.max(1, Math.ceil(40 / PX_PER_SAMPLE));
    const xTicks: { x: number; label: string; idx: number }[] = [];
    for (let i = 0; i < n; i += barsPerLabel) {
      xTicks.push({ x: xAt(i), label: formatTick(times[i] ?? ""), idx: i });
    }
    if (xTicks.length === 0 || xTicks[xTicks.length - 1]!.idx !== n - 1) {
      xTicks.push({ x: xAt(n - 1), label: formatTick(times[n - 1] ?? ""), idx: n - 1 });
    }

    const li = n - 1;
    return {
      pathPs: ptsPs.join(" "),
      pathVs: ptsVs.join(" "),
      minY: lo,
      maxY: hi,
      yTicks,
      yZero,
      lastX: xAt(li),
      lastYps: yn(psZ[li]!),
      lastYvs: yn(vsZ[li]!),
      plotW,
      xTicks,
      barsPerLabel,
    };
  }, [times, psZ, vsZ, barMin]);

  const n = Math.min(times.length, psZ.length, vsZ.length);
  const eligible = tradeSignal?.entryEligible === true;
  const lastPs = n > 0 ? psZ[n - 1] : null;
  const lastVs = n > 0 ? vsZ[n - 1] : null;
  const rec = tradeSignal?.recommendation;
  const lastIdx = n - 1;

  const entryMarkers = safeEntryEvents
    .filter((e) => Number.isFinite(e.tailIndex) && e.tailIndex >= 0 && e.tailIndex < n)
    .map((e) => {
      const plotW = chart.plotW;
      const x = n > 1 ? (e.tailIndex / (n - 1)) * plotW : plotW / 2;
      const leg = e.leg ?? (e.cross === "bullish" ? "CE" : "PE");
      const opt = e.optionType ?? leg;
      let strikeLine: string;
      if (e.strike != null && Number.isFinite(e.strike)) {
        strikeLine = `${Number(e.strike).toLocaleString("en-IN")} ${opt}`;
      } else if (e.tailIndex === lastIdx && rec?.strike != null && Number.isFinite(rec.strike)) {
        strikeLine = `${Number(rec.strike).toLocaleString("en-IN")} ${rec.optionType ?? leg} · plan`;
      } else {
        strikeLine = `${leg} · fill for strike`;
      }

      const contract =
        (e.optionSymbol || "").trim() ||
        (e.planSymbol || "").trim() ||
        (e.tailIndex === lastIdx ? (rec?.symbol || "").trim() : "");

      const lines: string[] = [];
      if (contract) {
        lines.push(contract);
      } else {
        lines.push(`Suggested leg: ${leg} — run recommendations / take a fill to see a symbol like NIFTY…CE/PE`);
      }
      const barHint = formatBarHint(e.time);
      if (barHint) lines.push(`Bar close: ${barHint}`);

      const spanY = Math.max(1e-9, chart.maxY - chart.minY);
      const ynPlot = (v: number) => PLOT_H - ((v - chart.minY) / spanY) * PLOT_H;
      const ti = e.tailIndex;
      const yPs = ynPlot(Number(psZ[ti]));
      const yVs = ynPlot(Number(vsZ[ti]));
      const yIcon = (yPs + yVs) / 2;

      return { ...e, x, yIcon, strikeLine, hoverLines: lines };
    });

  const tradeMarkers = useMemo(() => {
    const grouped = new Map<number, TrendPulseTradeEvent[]>();
    for (const ev of safeTradeEvents) {
      const idx = Number(ev.tailIndex);
      if (!Number.isFinite(idx) || idx < 0 || idx >= n) continue;
      const key = Math.trunc(idx);
      const list = grouped.get(key) ?? [];
      list.push(ev);
      grouped.set(key, list);
    }

    const out: Array<{ tailIndex: number; x: number; yIcon: number; hoverLines: string[] }> = [];
    for (const [idx, eventsAtIdx] of grouped.entries()) {
      const plotW = chart.plotW;
      const x = n > 1 ? (idx / (n - 1)) * plotW : plotW / 2;
      const spanY = Math.max(1e-9, chart.maxY - chart.minY);
      const ynPlot = (v: number) => PLOT_H - ((v - chart.minY) / spanY) * PLOT_H;
      const yPs = ynPlot(Number(psZ[idx]));
      const yVs = ynPlot(Number(vsZ[idx]));
      const yIcon = (yPs + yVs) / 2;

      const strikeLabels = Array.from(
        new Set(
          eventsAtIdx
            .map((te) => {
              if (te.strike != null && Number.isFinite(Number(te.strike))) {
                return `${Number(te.strike).toLocaleString("en-IN")} ${String(te.optionType || "").trim()}`.trim();
              }
              return "";
            })
            .filter(Boolean),
        ),
      );
      const symbols = Array.from(new Set(eventsAtIdx.map((te) => String(te.symbol || "").trim()).filter(Boolean)));
      const openedTimesOnly = Array.from(
        new Set(eventsAtIdx.map((te) => formatTimeOnlyIST(te.openedAt)).filter((x): x is string => !!x)),
      );
      const openedTimesFull = Array.from(
        new Set(eventsAtIdx.map((te) => formatBarHint(te.openedAt)).filter((x): x is string => !!x)),
      );

      const hoverLines: string[] = [];
      if (openedTimesOnly.length > 0) {
        hoverLines.push(
          `Time (IST): ${openedTimesOnly.length === 1 ? openedTimesOnly[0] : openedTimesOnly.join(" | ")}`,
        );
      }
      if (strikeLabels.length > 0) {
        hoverLines.push(`Trade Open: ${strikeLabels.join(", ")}`);
      } else {
        hoverLines.push("Trade Open");
      }
      if (openedTimesFull.length > 0) {
        hoverLines.push(
          `${openedTimesFull.length === 1 ? "Bar close" : "Bar closes"}: ${openedTimesFull.join(" | ")}`,
        );
      }
      if (symbols.length > 0) {
        const shown = symbols.slice(0, 3);
        hoverLines.push(`Symbol${symbols.length > 1 ? "s" : ""}: ${shown.join(", ")}${symbols.length > 3 ? " ..." : ""}`);
      }

      out.push({ tailIndex: idx, x, yIcon, hoverLines });
    }
    out.sort((a, b) => a.tailIndex - b.tailIndex);
    return out;
  }, [safeTradeEvents, n, chart.plotW, chart.maxY, chart.minY, psZ, vsZ]);

  if (n < 2) {
    return (
      <div className="trendpulse-chart-empty">
        <p style={{ color: "var(--muted)" }}>Not enough data to plot yet (need live index candles).</p>
      </div>
    );
  }

  const { plotW, barsPerLabel } = chart;
  const xAxisDense = barsPerLabel <= 1;

  const tooltipNode =
    tip && typeof document !== "undefined"
      ? createPortal(
          <div
            className="trendpulse-signal-tooltip trendpulse-signal-tooltip--portal"
            style={{ left: tip.left, top: tip.top, transform: tip.transform }}
            role="tooltip"
          >
            {tip.lines.map((line, i) => (
              <div key={i} className={i === 0 ? "trendpulse-signal-tooltip-primary" : "trendpulse-signal-tooltip-line"}>
                {line}
              </div>
            ))}
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="trendpulse-chart-wrap">
      {tooltipNode}

      <div className="trendpulse-chart-about">
        <div className="trendpulse-chart-about-title">What this chart is</div>
        <p className="trendpulse-chart-about-text">
          It plots two z-scores on your <strong>{stIntervalLabel}</strong> (signal) bars: how unusually strong{" "}
          <strong>price momentum</strong> (PS_z) and <strong>volume momentum</strong> (VS_z) are versus their recent history.
          When PS_z crosses VS_z in the direction that matches higher-timeframe bias — and trend strength (ADX) passes your
          minimum — the strategy treats that as a potential entry (see banner above).
        </p>
      </div>

      <div className="trendpulse-latest-values">
        <div className="trendpulse-latest-values-title">Latest bar (right edge)</div>
        <div className="trendpulse-latest-values-grid">
          <div className="trendpulse-latest-pill tp-ps">
            <span className="trendpulse-latest-label">PS_z</span>
            <span className="trendpulse-latest-num">{lastPs !== null ? lastPs.toFixed(3) : "—"}</span>
          </div>
          <div className="trendpulse-latest-pill tp-vs">
            <span className="trendpulse-latest-label">VS_z</span>
            <span className="trendpulse-latest-num">{lastVs !== null ? lastVs.toFixed(3) : "—"}</span>
          </div>
        </div>
      </div>

      <div className="trendpulse-legend-grid" role="group" aria-label="Chart legend">
        <div className="trendpulse-legend-item tp-leg-ps">
          <div className="trendpulse-legend-line" />
          <div>
            <div className="trendpulse-legend-name">PS_z — price momentum</div>
            <div className="trendpulse-legend-hint">Z-score of log return over {stIntervalLabel} bars</div>
          </div>
        </div>
        <div className="trendpulse-legend-item tp-leg-vs">
          <div className="trendpulse-legend-line" />
          <div>
            <div className="trendpulse-legend-name">VS_z — volume momentum</div>
            <div className="trendpulse-legend-hint">Z-score of volume vs its recent average</div>
          </div>
        </div>
      </div>
      <p className="trendpulse-legend-readout">
        <strong>How to read:</strong> A <em>bullish entry</em> is when PS_z crosses <strong>above</strong> VS_z and higher-timeframe
        bias is bullish; <em>bearish entry</em> when PS_z crosses <strong>below</strong> VS_z with bearish HTF — plus ADX ≥ minimum on
        the plan.
      </p>
      <div className="trendpulse-events-legend" role="group" aria-label="Markers">
        <span className="trendpulse-events-chip tp-entry">
          Gold rupee coin on PS/VS lines only when a trade is opened — hover for strike(s), symbol, and time. Place PNG at{" "}
          <code>public/images/trendpulse-rupee-coin.png</code>. Scroll for 5m steps.
        </span>
      </div>

      <div
        className="trendpulse-chart-scroll"
        role="region"
        aria-label="Scrollable z-score chart"
        onScroll={clearTip}
      >
        <div className="trendpulse-chart-scroll-track">
          <div
            className="trendpulse-y-axis-html trendpulse-y-axis-html--sticky"
            aria-hidden
            style={{ width: `${Y_AXIS_W_REM}rem`, height: PLOT_H }}
          >
            <span className="trendpulse-y-axis-title">Z</span>
            {[...chart.yTicks]
              .sort((a, b) => b.val - a.val)
              .map((tk, i) => (
                <span key={`y-${i}`} className="trendpulse-y-axis-tick">
                  {tk.val.toFixed(2)}
                </span>
              ))}
          </div>

          <div className="trendpulse-scroll-plot-col" style={{ width: plotW }}>
            <svg
              className="trendpulse-chart-svg trendpulse-chart-svg--px"
              width={plotW}
              height={PLOT_H}
              viewBox={`0 0 ${plotW} ${PLOT_H}`}
              preserveAspectRatio="xMinYMid meet"
              aria-label={title}
              onMouseLeave={clearTip}
            >
              <title>{title}</title>
              <rect x={0} y={0} width={plotW} height={PLOT_H} fill="transparent" />
              {chart.yTicks.map((tk, i) => (
                <line
                  key={`g-${i}`}
                  x1={0}
                  y1={tk.ySvg}
                  x2={plotW}
                  y2={tk.ySvg}
                  stroke="var(--border)"
                  strokeOpacity={0.12}
                  strokeWidth={1}
                />
              ))}
              {chart.yZero !== null && chart.yZero >= 0 && chart.yZero <= PLOT_H && (
                <line
                  x1={0}
                  y1={chart.yZero}
                  x2={plotW}
                  y2={chart.yZero}
                  stroke="var(--muted)"
                  strokeOpacity={0.45}
                  strokeWidth={1}
                  strokeDasharray="4 5"
                />
              )}
              <path d={chart.pathPs} fill="none" stroke="#2dd4bf" strokeWidth={1.6} />
              <path d={chart.pathVs} fill="none" stroke="#a78bfa" strokeWidth={1.6} />
              {tradeMarkers.map((e, i) => (
                <line
                  key={`entry-line-${i}-${e.tailIndex}`}
                  x1={e.x}
                  y1={0}
                  x2={e.x}
                  y2={PLOT_H}
                  stroke="#f59e0b"
                  strokeOpacity={0.22}
                  strokeWidth={1}
                  pointerEvents="none"
                />
              ))}
              <circle
                cx={chart.lastX}
                cy={chart.lastYps}
                r={eligible ? 5 : 3}
                fill={eligible ? "#22c55e" : "#2dd4bf"}
                stroke={eligible ? "#bbf7d0" : "none"}
                strokeWidth={eligible ? 1 : 0}
              />
              <circle
                cx={chart.lastX}
                cy={chart.lastYvs}
                r={eligible ? 5 : 3}
                fill={eligible ? "#22c55e" : "#a78bfa"}
                stroke={eligible ? "#bbf7d0" : "none"}
                strokeWidth={eligible ? 1 : 0}
              />
              {eligible && (
                <line
                  x1={chart.lastX}
                  y1={0}
                  x2={chart.lastX}
                  y2={PLOT_H}
                  stroke="#22c55e"
                  strokeOpacity={0.35}
                  strokeWidth={1}
                />
              )}
              {/* User-supplied coin PNG, centered between PS_z and VS_z only for opened trades. */}
              {tradeMarkers.map((e, i) => {
                const half = SIGNAL_ICON_PX / 2;
                return (
                  <g key={`entry-coin-${i}-${e.tailIndex}`} transform={`translate(${e.x}, ${e.yIcon})`}>
                    <rect
                      x={-half}
                      y={-half}
                      width={SIGNAL_ICON_PX}
                      height={SIGNAL_ICON_PX}
                      rx={6}
                      fill="rgba(0,0,0,0.01)"
                      className="trendpulse-rupee-hit"
                      style={{ cursor: "pointer" }}
                      onMouseEnter={(ev) => showSignalTipAnchored(ev.currentTarget, e.hoverLines)}
                      onMouseMove={(ev) => showSignalTipAnchored(ev.currentTarget, e.hoverLines)}
                      onMouseLeave={clearTip}
                    />
                    <image
                      href={RUPEE_COIN_HREF}
                      x={-half}
                      y={-half}
                      width={SIGNAL_ICON_PX}
                      height={SIGNAL_ICON_PX}
                      preserveAspectRatio="xMidYMid meet"
                      style={{ pointerEvents: "none" }}
                    />
                  </g>
                );
              })}
            </svg>

            <div
              className={`trendpulse-x-axis-html trendpulse-x-axis-html--scroll${xAxisDense ? " trendpulse-x-axis-html--dense" : ""}`}
              style={{ width: plotW }}
            >
              {chart.xTicks.map((t, i) => (
                <span
                  key={`${t.idx}-${i}`}
                  className="trendpulse-x-axis-tick"
                  style={{ left: `${(t.x / plotW) * 100}%` }}
                >
                  {t.label}
                </span>
              ))}
            </div>
            <p className="trendpulse-x-axis-caption">
              Time — one column per {stIntervalLabel} close (~{barMin} min), <strong>current session day only</strong>. Use
              horizontal scrollbar to pan.{" "}
              {sessionDayNote ? <span className="trendpulse-session-note">{sessionDayNote}</span> : null}
            </p>
          </div>
        </div>
      </div>

      <p className="trendpulse-footnote">
        Vertical axis: z-score (same scale for PS_z and VS_z); values are computed from <strong>full candle history</strong> on
        the server, but the graph shows <strong>one market session day</strong> (IST) so entries/trades align with today&apos;s
        tape. Dashed line = 0. The <strong>rupee coin</strong> marks bars where this strategy actually opened a trade; tooltip above/beside the coin shows strike(s) and time. PNG:{" "}
        <code>public/images/trendpulse-rupee-coin.png</code>.
      </p>
    </div>
  );
}
