"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type CandlestickData,
  type ISeriesApi,
  type IChartApi,
  LineType,
  type LineData,
  type SeriesMarker,
  type Time,
  TickMarkType,
  type UTCTimestamp,
  type WhitespaceData,
} from "lightweight-charts";
import type { ObservabilityPanel, ObservabilitySnapshot, ObservabilityTradeMarker } from "@/lib/api_client";

const IST_TZ = "Asia/Kolkata";
const IST_TIME_FMT = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const IST_DATE_FMT = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST_TZ,
  day: "2-digit",
  month: "2-digit",
});
const IST_DATE_TIME_FMT = new Intl.DateTimeFormat("en-IN", {
  timeZone: IST_TZ,
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function timeToUnixSeconds(time: Time): number | null {
  if (typeof time === "number") return time;
  if (typeof time === "string") {
    const ms = Date.parse(time);
    return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
  }
  if (time && typeof time === "object" && "year" in time && "month" in time && "day" in time) {
    return Math.floor(Date.UTC(time.year, time.month - 1, time.day) / 1000);
  }
  return null;
}

function istTickMarkFormatter(time: Time, tickMarkType: TickMarkType): string {
  const unix = timeToUnixSeconds(time);
  if (unix == null) return "";
  const dt = new Date(unix * 1000);
  if (tickMarkType === TickMarkType.DayOfMonth || tickMarkType === TickMarkType.Month || tickMarkType === TickMarkType.Year) {
    return IST_DATE_FMT.format(dt);
  }
  return IST_TIME_FMT.format(dt);
}

function istTimeFormatter(time: Time): string {
  const unix = timeToUnixSeconds(time);
  if (unix == null) return "";
  return `${IST_DATE_TIME_FMT.format(new Date(unix * 1000))} IST`;
}

function zipLine(times: number[] | undefined, vals: number[] | undefined): LineData[] {
  if (!times?.length || !vals?.length) return [];
  const n = Math.min(times.length, vals.length);
  const pts: Array<{ time: number; value: number }> = [];
  for (let i = 0; i < n; i++) {
    const t = times[i];
    const v = vals[i];
    if (!Number.isFinite(t) || v == null || Number.isNaN(v)) continue;
    pts.push({ time: t, value: v });
  }
  // Ensure strictly ascending times (lightweight-charts asserts this).
  pts.sort((a, b) => a.time - b.time);
  const out: LineData[] = [];
  for (const p of pts) {
    const last = out.length ? (out[out.length - 1] as LineData) : null;
    if (last && Number(last.time) === p.time) {
      (out[out.length - 1] as LineData).value = p.value;
      continue;
    }
    out.push({ time: p.time as UTCTimestamp, value: p.value });
  }
  return out;
}

/** Bull segment uses lower band (green); bear segment uses upper band (red) — matches TradingView-style ST. */
function splitSuperTrendSteps(
  times: number[] | undefined,
  stUpper: number[] | undefined,
  stLower: number[] | undefined,
  dir: number[] | undefined,
): { bull: Array<LineData | WhitespaceData>; bear: Array<LineData | WhitespaceData> } {
  const bull: Array<LineData | WhitespaceData> = [];
  const bear: Array<LineData | WhitespaceData> = [];
  if (!times?.length || !stUpper?.length || !stLower?.length || !dir?.length) return { bull, bear };
  const n = Math.min(times.length, stUpper.length, stLower.length, dir.length);
  const pts: Array<{ time: number; up: number; lo: number; dir: number }> = [];
  for (let i = 0; i < n; i++) {
    const t = times[i];
    if (!Number.isFinite(t)) continue;
    pts.push({ time: t, up: Number(stUpper[i]), lo: Number(stLower[i]), dir: Number(dir[i] ?? 0) });
  }
  pts.sort((a, b) => a.time - b.time);
  const dedup: typeof pts = [];
  for (const p of pts) {
    const last = dedup.length ? dedup[dedup.length - 1] : null;
    if (last && last.time === p.time) dedup[dedup.length - 1] = p;
    else dedup.push(p);
  }

  let prev: number | null = null;
  for (const p of dedup) {
    const d = p.dir;
    const t = p.time as UTCTimestamp;
    if (d !== 1 && d !== -1) {
      prev = null;
      bull.push({ time: t });
      bear.push({ time: t });
      continue;
    }
    if (prev != null && d !== prev) {
      bull.push({ time: t });
      bear.push({ time: t });
      prev = d;
      continue;
    }
    if (d === 1) {
      const v = p.lo;
      bull.push(Number.isFinite(v) ? { time: t, value: v } : { time: t });
      bear.push({ time: t });
    } else {
      const v = p.up;
      bear.push(Number.isFinite(v) ? { time: t, value: v } : { time: t });
      bull.push({ time: t });
    }
    prev = d;
  }
  return { bull, bear };
}

function zipCandles(
  times: number[],
  open: number[],
  high: number[],
  low: number[],
  close: number[],
): CandlestickData[] {
  const n = Math.min(times.length, open.length, high.length, low.length, close.length);
  const pts: Array<{ time: number; open: number; high: number; low: number; close: number }> = [];
  for (let i = 0; i < n; i++) {
    const t = times[i];
    if (!Number.isFinite(t)) continue;
    pts.push({ time: t, open: open[i], high: high[i], low: low[i], close: close[i] });
  }
  pts.sort((a, b) => a.time - b.time);
  const out: CandlestickData[] = [];
  for (const p of pts) {
    const last = out.length ? (out[out.length - 1] as CandlestickData) : null;
    if (last && Number(last.time) === p.time) {
      (out[out.length - 1] as CandlestickData).open = p.open;
      (out[out.length - 1] as CandlestickData).high = p.high;
      (out[out.length - 1] as CandlestickData).low = p.low;
      (out[out.length - 1] as CandlestickData).close = p.close;
      continue;
    }
    out.push({
      time: p.time as UTCTimestamp,
      open: p.open,
      high: p.high,
      low: p.low,
      close: p.close,
    });
  }
  return out;
}

function markerStyle(m: ObservabilityTradeMarker): { color: string; position: "aboveBar" | "belowBar"; shape: "arrowUp" | "arrowDown" } {
  if (m.kind === "EXIT") {
    return { color: cssVar("--danger", "#ff6b6b"), position: "aboveBar", shape: "arrowDown" };
  }
  const side = (m.side || "").toUpperCase();
  if (side === "CE") return { color: "#64b5f6", position: "belowBar", shape: "arrowUp" };
  if (side === "PE") return { color: cssVar("--warning", "#ffb648"), position: "belowBar", shape: "arrowUp" };
  return { color: cssVar("--accent", "#4f7cff"), position: "belowBar", shape: "arrowUp" };
}

function nearestBarTime(target: number, times: number[], maxDiffSec: number): number | null {
  let best: number | null = null;
  let d = Infinity;
  for (const x of times) {
    const dd = Math.abs(x - target);
    if (dd < d) {
      d = dd;
      best = x;
    }
  }
  if (best == null || d > maxDiffSec) return null;
  return best;
}

function tradeMarkers(times: number[], markers: ObservabilityTradeMarker[]): SeriesMarker<Time>[] {
  const tol = 8 * 60;
  const out: SeriesMarker<Time>[] = [];
  for (const m of markers) {
    const t = nearestBarTime(m.time, times, tol);
    if (t == null) continue;
    const st = markerStyle(m);
    out.push({
      time: t as UTCTimestamp,
      position: st.position,
      color: st.color,
      shape: st.shape,
      text: `${m.kind} ${m.mode}`,
    });
  }
  return out;
}

function chartThemeOptions() {
  const bg = cssVar("--surface", "#0f1728");
  const text = cssVar("--text", "#e6edf8");
  const border = cssVar("--border", "#21314f");
  return {
    layout: {
      background: { type: ColorType.Solid, color: bg },
      textColor: text,
    },
    grid: {
      vertLines: { color: border },
      horzLines: { color: border },
    },
    borderColor: border,
  };
}

function StochasticBnfBlock({ panel }: { panel: ObservabilityPanel }) {
  const mainRef = useRef<HTMLDivElement>(null);
  const stochRef = useRef<HTMLDivElement>(null);
  const adxRef = useRef<HTMLDivElement>(null);
  const s = panel.series as Record<string, unknown>;
  const mainChartRef = useRef<IChartApi | null>(null);
  const stochChartRef = useRef<IChartApi | null>(null);
  const adxChartRef = useRef<IChartApi | null>(null);
  const syncingRangeRef = useRef(false);
  const [expanded, setExpanded] = useState(false);
  const heightRef = useRef(340);
  const prevHeightRef = useRef<number | null>(null);

  useEffect(() => {
    if (!s?.ok) return;
    const times = s.times as number[] | undefined;
    if (!times?.length) return;
    const open = s.open as number[];
    const high = s.high as number[];
    const low = s.low as number[];
    const close = s.close as number[];
    const markers = tradeMarkers(times, panel.markers || []);

    const mEl = mainRef.current;
    const stEl = stochRef.current;
    const adEl = adxRef.current;
    if (!mEl || !stEl || !adEl) return;

    const theme = chartThemeOptions();
    const main = createChart(mEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: mEl.clientWidth,
      height: 340,
      localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const candle = main.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    candle.setData(zipCandles(times, open, high, low, close));
    const ema5 = main.addLineSeries({ color: "#42a5f5", lineWidth: 2, title: "EMA5" });
    ema5.setData(zipLine(times, s.ema5 as number[]));
    const ema15 = main.addLineSeries({ color: "#ab47bc", lineWidth: 2, title: "EMA15" });
    ema15.setData(zipLine(times, s.ema15 as number[]));
    const ema50 = main.addLineSeries({ color: "#78909c", lineWidth: 1, title: "EMA50" });
    ema50.setData(zipLine(times, s.ema50 as number[]));
    const vwap = main.addLineSeries({ color: "#ffcc80", lineWidth: 1, lineStyle: 2, title: "VWAP" });
    vwap.setData(zipLine(times, s.vwap as number[]));
    candle.setMarkers(markers);

    const stoch = createChart(stEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: stEl.clientWidth,
      height: 140,
      timeScale: { visible: false },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const kS = stoch.addLineSeries({ color: "#7cb342", lineWidth: 2, title: "Stoch %K" });
    kS.setData(zipLine(times, s.stochK as number[]));
    const dS = stoch.addLineSeries({ color: "#29b6f6", lineWidth: 2, title: "Stoch %D" });
    dS.setData(zipLine(times, s.stochD as number[]));

    const adxc = createChart(adEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: adEl.clientWidth,
      height: 110,
      localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const adx = adxc.addLineSeries({ color: "#ef5350", lineWidth: 2, title: "ADX" });
    adx.setData(zipLine(times, s.adx as number[]));
    mainChartRef.current = main;
    stochChartRef.current = stoch;
    adxChartRef.current = adxc;
    const charts = [main, stoch, adxc] as const;
    const syncRange = (
      source: IChartApi,
      range: ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleLogicalRange"]>,
    ) => {
      if (!range || syncingRangeRef.current) return;
      syncingRangeRef.current = true;
      try {
        for (const chart of charts) {
          if (chart === source) continue;
          chart.timeScale().setVisibleLogicalRange(range);
        }
      } finally {
        syncingRangeRef.current = false;
      }
    };
    const onMainRange = (r: ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleLogicalRange"]>) =>
      syncRange(main, r);
    const onStochRange = (r: ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleLogicalRange"]>) =>
      syncRange(stoch, r);
    const onAdxRange = (r: ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleLogicalRange"]>) =>
      syncRange(adxc, r);
    main.timeScale().subscribeVisibleLogicalRangeChange(onMainRange);
    stoch.timeScale().subscribeVisibleLogicalRangeChange(onStochRange);
    adxc.timeScale().subscribeVisibleLogicalRangeChange(onAdxRange);

    const roMain = new ResizeObserver(() => {
      const w = mEl.clientWidth;
      main.applyOptions({ width: w });
    });
    const roS = new ResizeObserver(() => stoch.applyOptions({ width: stEl.clientWidth }));
    const roA = new ResizeObserver(() => adxc.applyOptions({ width: adEl.clientWidth }));
    roMain.observe(mEl);
    roS.observe(stEl);
    roA.observe(adEl);

    main.timeScale().fitContent();
    stoch.timeScale().fitContent();
    adxc.timeScale().fitContent();

    return () => {
      roMain.disconnect();
      roS.disconnect();
      roA.disconnect();
      main.timeScale().unsubscribeVisibleLogicalRangeChange(onMainRange);
      stoch.timeScale().unsubscribeVisibleLogicalRangeChange(onStochRange);
      adxc.timeScale().unsubscribeVisibleLogicalRangeChange(onAdxRange);
      main.remove();
      stoch.remove();
      adxc.remove();
      mainChartRef.current = null;
      stochChartRef.current = null;
      adxChartRef.current = null;
    };
  }, [panel]);

  const zoomH = (factor: number) => {
    const main = mainChartRef.current;
    if (!main) return;
    const ts = main.timeScale();
    const r = ts.getVisibleLogicalRange();
    if (!r) return;
    const from = Number(r.from);
    const to = Number(r.to);
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    const c = (from + to) / 2;
    const half = (to - from) / 2;
    const nh = Math.max(5, half * factor);
    ts.setVisibleLogicalRange({ from: c - nh, to: c + nh });
  };

  const resetView = () => {
    mainChartRef.current?.timeScale().fitContent();
    stochChartRef.current?.timeScale().fitContent();
    adxChartRef.current?.timeScale().fitContent();
  };

  const toggleExpand = () => {
    const main = mainChartRef.current;
    if (!main) {
      setExpanded((v) => !v);
      return;
    }
    if (!expanded) {
      prevHeightRef.current = heightRef.current;
      const nextH = Math.max(
        420,
        Math.min(
          900,
          (typeof window !== "undefined" ? window.innerHeight : 900) - 240,
        ),
      );
      heightRef.current = nextH;
      main.applyOptions({ height: nextH });
      setExpanded(true);
      return;
    }
    const prev = prevHeightRef.current ?? 340;
    heightRef.current = prev;
    main.applyOptions({ height: prev });
    prevHeightRef.current = null;
    setExpanded(false);
  };

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleExpand();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded]);

  if (!s?.ok) {
    return (
      <p className="obs-chart-error">
        {String(s?.reason || "Series unavailable")}
      </p>
    );
  }

  return (
    <div className={`obs-chart-stack ${expanded ? "obs-chart-stack--expanded" : ""}`}>
      <div className="obs-subchart-label">
        StochasticBNF
        <span className="obs-tv-toolbar">
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(0.7)}
            aria-label="Zoom in"
            title="Zoom in"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm1 2v2h2v2h-2v2H9v-2H7V9h2V7h2Zm8.7 13.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(1.3)}
            aria-label="Zoom out"
            title="Zoom out"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm-3 4h6v2H7V9Zm12.7 11.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={resetView}
            aria-label="Fit"
            title="Fit"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={toggleExpand}
            aria-label={expanded ? "Exit full screen" : "Expand"}
            title={expanded ? "Exit full screen" : "Expand"}
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
        </span>
      </div>
      <div ref={mainRef} className="obs-chart-wrap" />
      <div className="obs-subchart-label">Stochastic</div>
      <div ref={stochRef} className="obs-chart-wrap obs-chart-wrap--sub" />
      <div className="obs-subchart-label">ADX</div>
      <div ref={adxRef} className="obs-chart-wrap obs-chart-wrap--sub" />
      {expanded ? (
        <>
          <button type="button" className="obs-expand-close" onClick={toggleExpand} aria-label="Close full screen" title="Close">
            ×
          </button>
          <div className="obs-expand-backdrop" onClick={toggleExpand} />
        </>
      ) : null}
    </div>
  );
}

function PsVsMtfBlock({ panel }: { panel: ObservabilityPanel }) {
  const mainRef = useRef<HTMLDivElement>(null);
  const rsiRef = useRef<HTMLDivElement>(null);
  const m15Ref = useRef<HTMLDivElement>(null);
  const adx15Ref = useRef<HTMLDivElement>(null);
  const s = panel.series as Record<string, unknown>;
  const mainChartRef = useRef<IChartApi | null>(null);
  const rsiChartRef = useRef<IChartApi | null>(null);
  const m15ChartRef = useRef<IChartApi | null>(null);
  const adxChartRef = useRef<IChartApi | null>(null);
  const syncingTimeRef = useRef(false);
  const [expanded, setExpanded] = useState(false);
  const heightRef = useRef(340);
  const prevHeightRef = useRef<number | null>(null);

  useEffect(() => {
    if (!s?.ok) return;
    const times = s.times as number[] | undefined;
    if (!times?.length) return;
    const open = s.open as number[];
    const high = s.high as number[];
    const low = s.low as number[];
    const close = s.close as number[];
    const markers = tradeMarkers(times, panel.markers || []);

    const mEl = mainRef.current;
    const rEl = rsiRef.current;
    const m15El = m15Ref.current;
    const adxEl = adx15Ref.current;
    if (!mEl || !rEl || !m15El || !adxEl) return;

    const theme = chartThemeOptions();
    const main = createChart(mEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: mEl.clientWidth,
      height: heightRef.current,
      localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const candle = main.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    candle.setData(zipCandles(times, open, high, low, close));
    const ps = main.addLineSeries({ color: "#42a5f5", lineWidth: 2, title: "PS (EMA of RSI)" });
    ps.setData(zipLine(times, s.ps as number[]));
    const vs = main.addLineSeries({ color: "#ab47bc", lineWidth: 2, title: "VS (WMA of RSI)" });
    vs.setData(zipLine(times, s.vs as number[]));
    candle.setMarkers(markers);

    const rsiC = createChart(rEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: rEl.clientWidth,
      height: 120,
      timeScale: { visible: false },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const rsiL = rsiC.addLineSeries({ color: "#7cb342", lineWidth: 2, title: "RSI (3m)" });
    rsiL.setData(zipLine(times, s.rsi as number[]));

    const times15 = (s.times15 as number[] | undefined) || [];
    const m15 = createChart(m15El, {
      layout: theme.layout,
      grid: theme.grid,
      width: m15El.clientWidth,
      height: 120,
      timeScale: { visible: false },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    if (times15.length > 0) {
      const ps15 = m15.addLineSeries({ color: "#42a5f5", lineWidth: 2, title: "PS 15m" });
      ps15.setData(zipLine(times15, s.ps15 as number[]));
      const vs15 = m15.addLineSeries({ color: "#ab47bc", lineWidth: 2, title: "VS 15m" });
      vs15.setData(zipLine(times15, s.vs15 as number[]));
    }

    const adxChart = createChart(adxEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: adxEl.clientWidth,
      height: 100,
      localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    if (times15.length > 0) {
      const adxL = adxChart.addLineSeries({ color: "#ef5350", lineWidth: 2, title: "ADX (15m)" });
      adxL.setData(zipLine(times15, s.adx15 as number[]));
    }

    mainChartRef.current = main;
    rsiChartRef.current = rsiC;
    m15ChartRef.current = m15;
    adxChartRef.current = adxChart;

    const charts = [main, rsiC, m15, adxChart] as const;
    const syncTimeFrom =
      (source: IChartApi) =>
      (range: ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleRange"]> | null) => {
        if (!range || syncingTimeRef.current) return;
        syncingTimeRef.current = true;
        try {
          for (const ch of charts) {
            if (ch === source) continue;
            ch.timeScale().setVisibleRange(range);
          }
        } finally {
          syncingTimeRef.current = false;
        }
      };
    const onMainT = syncTimeFrom(main);
    const onRsiT = syncTimeFrom(rsiC);
    const onM15T = syncTimeFrom(m15);
    const onAdxT = syncTimeFrom(adxChart);
    main.timeScale().subscribeVisibleTimeRangeChange(onMainT);
    rsiC.timeScale().subscribeVisibleTimeRangeChange(onRsiT);
    m15.timeScale().subscribeVisibleTimeRangeChange(onM15T);
    adxChart.timeScale().subscribeVisibleTimeRangeChange(onAdxT);

    const roMain = new ResizeObserver(() => main.applyOptions({ width: mEl.clientWidth }));
    const roR = new ResizeObserver(() => rsiC.applyOptions({ width: rEl.clientWidth }));
    const roM15 = new ResizeObserver(() => m15.applyOptions({ width: m15El.clientWidth }));
    const roAdx = new ResizeObserver(() => adxChart.applyOptions({ width: adxEl.clientWidth }));
    roMain.observe(mEl);
    roR.observe(rEl);
    roM15.observe(m15El);
    roAdx.observe(adxEl);

    main.timeScale().fitContent();
    rsiC.timeScale().fitContent();
    m15.timeScale().fitContent();
    adxChart.timeScale().fitContent();

    return () => {
      main.timeScale().unsubscribeVisibleTimeRangeChange(onMainT);
      rsiC.timeScale().unsubscribeVisibleTimeRangeChange(onRsiT);
      m15.timeScale().unsubscribeVisibleTimeRangeChange(onM15T);
      adxChart.timeScale().unsubscribeVisibleTimeRangeChange(onAdxT);
      roMain.disconnect();
      roR.disconnect();
      roM15.disconnect();
      roAdx.disconnect();
      main.remove();
      rsiC.remove();
      m15.remove();
      adxChart.remove();
      mainChartRef.current = null;
      rsiChartRef.current = null;
      m15ChartRef.current = null;
      adxChartRef.current = null;
    };
  }, [panel]);

  const zoomH = (factor: number) => {
    const main = mainChartRef.current;
    if (!main) return;
    const ts = main.timeScale();
    const r = ts.getVisibleLogicalRange();
    if (!r) return;
    const from = Number(r.from);
    const to = Number(r.to);
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    const c = (from + to) / 2;
    const half = (to - from) / 2;
    const nh = Math.max(5, half * factor);
    ts.setVisibleLogicalRange({ from: c - nh, to: c + nh });
  };

  const resetView = () => {
    mainChartRef.current?.timeScale().fitContent();
    rsiChartRef.current?.timeScale().fitContent();
    m15ChartRef.current?.timeScale().fitContent();
    adxChartRef.current?.timeScale().fitContent();
  };

  const toggleExpand = () => {
    const main = mainChartRef.current;
    if (!main) {
      setExpanded((v) => !v);
      return;
    }
    if (!expanded) {
      prevHeightRef.current = heightRef.current;
      const nextH = Math.max(
        420,
        Math.min(
          900,
          (typeof window !== "undefined" ? window.innerHeight : 900) - 240,
        ),
      );
      heightRef.current = nextH;
      main.applyOptions({ height: nextH });
      setExpanded(true);
      return;
    }
    const prev = prevHeightRef.current ?? 340;
    heightRef.current = prev;
    main.applyOptions({ height: prev });
    prevHeightRef.current = null;
    setExpanded(false);
  };

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleExpand();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [expanded]);

  if (!s?.ok) {
    return <p className="obs-chart-error">{String(s?.reason || "Series unavailable")}</p>;
  }

  return (
    <div className={`obs-chart-stack ${expanded ? "obs-chart-stack--expanded" : ""}`}>
      <div className="obs-subchart-label">
        PS / VS MTF (3m + 15m)
        <span className="obs-tv-toolbar">
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(0.7)}
            aria-label="Zoom in"
            title="Zoom in"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm1 2v2h2v2h-2v2H9v-2H7V9h2V7h2Zm8.7 13.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(1.3)}
            aria-label="Zoom out"
            title="Zoom out"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm-3 4h6v2H7V9Zm12.7 11.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={resetView}
            aria-label="Fit"
            title="Fit"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={toggleExpand}
            aria-label={expanded ? "Exit full screen" : "Expand"}
            title={expanded ? "Exit full screen" : "Expand"}
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
        </span>
      </div>
      <div ref={mainRef} className="obs-chart-wrap" />
      <div className="obs-subchart-label">RSI (3m)</div>
      <div ref={rsiRef} className="obs-chart-wrap obs-chart-wrap--sub" />
      <div className="obs-subchart-label">PS / VS (15m, resampled)</div>
      <div ref={m15Ref} className="obs-chart-wrap obs-chart-wrap--sub" />
      <div className="obs-subchart-label">ADX (15m)</div>
      <div ref={adx15Ref} className="obs-chart-wrap obs-chart-wrap--sub" />
      {expanded ? (
        <>
          <button type="button" className="obs-expand-close" onClick={toggleExpand} aria-label="Close full screen" title="Close">
            ×
          </button>
          <div className="obs-expand-backdrop" onClick={toggleExpand} />
        </>
      ) : null}
    </div>
  );
}

function fmtNum(n: unknown, d = 2): string {
  return typeof n === "number" && Number.isFinite(n) ? n.toFixed(d) : "—";
}

function SuperTrendTrailBlock({ panel }: { panel: ObservabilityPanel }) {
  const mainRef = useRef<HTMLDivElement>(null);
  const optRef = useRef<HTMLDivElement>(null);
  const s = panel.series as Record<string, unknown>;
  const optPts = panel.optionVwap;
  const sb = panel.signalBar;
  const stDir = (s.stDirection as number[] | undefined) ?? [];
  const stDirLast = stDir.length ? stDir[stDir.length - 1] : null;
  const stState =
    stDirLast === 1 ? "BULLISH" : stDirLast === -1 ? "BEARISH" : null;

  const markers = useMemo(() => {
    const times = s.times as number[] | undefined;
    return times?.length ? tradeMarkers(times, panel.markers || []) : [];
  }, [panel.markers, s.times]);

  const mainChartRef = useRef<IChartApi | null>(null);
  const mainSeriesRef = useRef<{
    candle: ISeriesApi<"Candlestick">;
    stBull: ISeriesApi<"Line">;
    stBear: ISeriesApi<"Line">;
    emaF: ISeriesApi<"Line">;
    emaSl: ISeriesApi<"Line">;
    vwap: ISeriesApi<"Line">;
  } | null>(null);
  const optChartRef = useRef<IChartApi | null>(null);
  const optSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const initDoneRef = useRef(false);
  const userRangeRef = useRef<ReturnType<ReturnType<IChartApi["timeScale"]>["getVisibleRange"]> | null>(null);
  const userHasInteractedRef = useRef(false);
  const applyingRangeRef = useRef(false);
  const heightRef = useRef(380);
  const [expanded, setExpanded] = useState(false);
  const prevHeightRef = useRef<number | null>(null);
  const lastMainWidthRef = useRef<number>(0);
  const lastOptWidthRef = useRef<number>(0);
  const expandedRef = useRef(false);

  // Create charts once; preserve zoom / visible range across refresh updates.
  useEffect(() => {
    if (!s?.ok) return;
    const mEl = mainRef.current;
    if (!mEl || mainChartRef.current) return;

    const theme = chartThemeOptions();
    const main = createChart(mEl, {
      layout: theme.layout,
      grid: theme.grid,
      width: mEl.clientWidth,
      height: heightRef.current,
      localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
      rightPriceScale: { borderColor: theme.borderColor },
    });
    const candle = main.addCandlestickSeries({
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    const stBull = main.addLineSeries({
      color: "#26a69a",
      lineWidth: 2,
      lineType: LineType.WithSteps,
      title: "SuperTrend (bull)",
    });
    const stBear = main.addLineSeries({
      color: "#ef5350",
      lineWidth: 2,
      lineType: LineType.WithSteps,
      title: "SuperTrend (bear)",
    });
    const emaF = main.addLineSeries({ color: "#42a5f5", lineWidth: 2, title: "EMA fast" });
    const emaSl = main.addLineSeries({ color: "#ab47bc", lineWidth: 2, title: "EMA slow" });
    const vwap = main.addLineSeries({ color: "#ffcc80", lineWidth: 1, lineStyle: 2, title: "Spot VWAP" });

    mainChartRef.current = main;
    mainSeriesRef.current = { candle, stBull, stBear, emaF, emaSl, vwap };

    const ts = main.timeScale();
    const onRange = (r: any) => {
      if (!initDoneRef.current) return;
      if (applyingRangeRef.current) return;
      if (!r) return;
      userHasInteractedRef.current = true;
      userRangeRef.current = r;
    };
    // Track the last user-visible window so refresh doesn't snap back.
    ts.subscribeVisibleTimeRangeChange(onRange);

    const roMain = new ResizeObserver(() => {
      if (expandedRef.current) return;
      const nextW = mEl.clientWidth;
      if (!Number.isFinite(nextW) || nextW <= 0) return;
      if (Math.abs(nextW - lastMainWidthRef.current) < 1) return;
      lastMainWidthRef.current = nextW;
      main.applyOptions({ width: nextW });
    });
    roMain.observe(mEl);

    // Option chart is created lazily in the update effect when we have points + element.
    return () => {
      ts.unsubscribeVisibleTimeRangeChange(onRange);
      roMain.disconnect();
      main.remove();
      optChartRef.current?.remove();
      mainChartRef.current = null;
      mainSeriesRef.current = null;
      optChartRef.current = null;
      optSeriesRef.current = null;
      initDoneRef.current = false;
      userRangeRef.current = null;
      userHasInteractedRef.current = false;
      applyingRangeRef.current = false;
    };
  }, [s?.ok]);

  const zoomH = (factor: number) => {
    const main = mainChartRef.current;
    if (!main) return;
    const ts = main.timeScale();
    const r = ts.getVisibleLogicalRange();
    if (!r) return;
    const from = Number(r.from);
    const to = Number(r.to);
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    const c = (from + to) / 2;
    const half = (to - from) / 2;
    const nh = Math.max(5, half * factor);
    applyingRangeRef.current = true;
    try {
      ts.setVisibleLogicalRange({ from: c - nh, to: c + nh });
      userHasInteractedRef.current = true;
      userRangeRef.current = ts.getVisibleRange();
    } finally {
      applyingRangeRef.current = false;
    }
  };

  const resizeV = (delta: number) => {
    const main = mainChartRef.current;
    if (!main) return;
    heightRef.current = Math.max(260, Math.min(860, heightRef.current + delta));
    main.applyOptions({ height: heightRef.current });
  };

  const resetView = () => {
    const main = mainChartRef.current;
    if (!main) return;
    const ts = main.timeScale();
    applyingRangeRef.current = true;
    try {
      ts.fitContent();
      userHasInteractedRef.current = false;
      userRangeRef.current = ts.getVisibleRange();
    } finally {
      applyingRangeRef.current = false;
    }
  };

  const toggleExpand = () => {
    const main = mainChartRef.current;
    if (!main) {
      setExpanded((v) => !v);
      return;
    }
    if (!expanded) {
      prevHeightRef.current = heightRef.current;
      const nextH = Math.max(420, Math.min(900, (typeof window !== "undefined" ? window.innerHeight : 900) - 220));
      heightRef.current = nextH;
      main.applyOptions({ height: nextH });
      setExpanded(true);
      return;
    }
    const prev = prevHeightRef.current ?? 380;
    heightRef.current = prev;
    main.applyOptions({ height: prev });
    prevHeightRef.current = null;
    setExpanded(false);
  };

  useEffect(() => {
    expandedRef.current = expanded;
    // In expanded mode, apply width once after layout settles and avoid observer thrash.
    const main = mainChartRef.current;
    const apply = () => {
      if (!mainRef.current || !main) return;
      const nextW = mainRef.current.clientWidth;
      if (Number.isFinite(nextW) && nextW > 0) {
        lastMainWidthRef.current = nextW;
        main.applyOptions({ width: nextW, height: heightRef.current });
      }
      if (optRef.current && optChartRef.current) {
        const ow = optRef.current.clientWidth;
        if (Number.isFinite(ow) && ow > 0) {
          lastOptWidthRef.current = ow;
          optChartRef.current.applyOptions({ width: ow });
        }
      }
    };
    const raf1 = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(apply);
    });
    return () => window.cancelAnimationFrame(raf1);
  }, [expanded]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") toggleExpand();
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded]);

  // Update data without recreating chart (keeps expansion/zoom stable).
  useEffect(() => {
    if (!s?.ok) return;
    const times = s.times as number[] | undefined;
    if (!times?.length) return;
    const open = s.open as number[];
    const high = s.high as number[];
    const low = s.low as number[];
    const close = s.close as number[];
    const main = mainChartRef.current;
    const ser = mainSeriesRef.current;
    if (!main || !ser) return;

    const ts = main.timeScale();

    ser.candle.setData(zipCandles(times, open, high, low, close));
    const stDir = (s.stDirection as number[] | undefined) ?? [];
    const stSplit = splitSuperTrendSteps(times, s.supertrendUpper as number[], s.supertrendLower as number[], stDir);
    ser.stBull.setData(stSplit.bull as LineData[]);
    ser.stBear.setData(stSplit.bear as LineData[]);
    ser.emaF.setData(zipLine(times, s.emaFast as number[]));
    ser.emaSl.setData(zipLine(times, s.emaSlow as number[]));
    ser.vwap.setData(zipLine(times, s.vwap as number[]));
    ser.candle.setMarkers(markers);

    if (!initDoneRef.current) {
      ts.fitContent();
      initDoneRef.current = true;
      // Seed initial user range from the fitted view.
      userRangeRef.current = ts.getVisibleRange();
    } else if (userHasInteractedRef.current && userRangeRef.current) {
      // Restore the last user-controlled view after setData.
      applyingRangeRef.current = true;
      try {
        ts.setVisibleRange(userRangeRef.current);
      } catch {
        // no-op
      } finally {
        applyingRangeRef.current = false;
      }
    }

    const oEl = optRef.current;
    if (oEl && optPts && optPts.length > 0) {
      if (!optChartRef.current) {
        const theme = chartThemeOptions();
        const optChart = createChart(oEl, {
          layout: theme.layout,
          grid: theme.grid,
          width: oEl.clientWidth,
          height: 140,
          localization: { locale: "en-IN", timeFormatter: istTimeFormatter },
          timeScale: { timeVisible: true, secondsVisible: false, borderColor: theme.borderColor, tickMarkFormatter: istTickMarkFormatter },
          rightPriceScale: { borderColor: theme.borderColor },
        });
        optChartRef.current = optChart;
        optSeriesRef.current = optChart.addLineSeries({ color: "#aed581", lineWidth: 2, title: "Option VWAP" });
        const roOpt = new ResizeObserver(() => {
          if (expandedRef.current) return;
          const nextW = oEl.clientWidth;
          if (!Number.isFinite(nextW) || nextW <= 0) return;
          if (Math.abs(nextW - lastOptWidthRef.current) < 1) return;
          lastOptWidthRef.current = nextW;
          optChart.applyOptions({ width: nextW });
        });
        roOpt.observe(oEl);
        // Disconnect on unmount via the main cleanup (optChart.remove()).
      }
      optSeriesRef.current?.setData(optPts.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
      // Do NOT fitContent on refresh; preserves zoom/scroll if user adjusted it.
      if (initDoneRef.current && optChartRef.current && !Number.isNaN(optPts.length)) {
        // Fit only once when option chart first appears.
        if ((optChartRef.current as any).__s004FitOnceDone !== true) {
          optChartRef.current.timeScale().fitContent();
          (optChartRef.current as any).__s004FitOnceDone = true;
        }
      }
    } else {
      // If no option points, remove the option chart so it doesn't "jump" layout on refresh.
      optChartRef.current?.remove();
      optChartRef.current = null;
      optSeriesRef.current = null;
    }
  }, [markers, optPts, panel.markers, s, s?.ok]);

  if (!s?.ok) {
    return <p className="obs-chart-error">{String(s?.reason || "Series unavailable")}</p>;
  }

  return (
    <div className={`obs-chart-stack ${expanded ? "obs-chart-stack--expanded" : ""}`}>
      <div className="obs-subchart-label">
        SuperTrend state:{" "}
        <strong
          style={{
            color: stDirLast === -1 ? "#ef5350" : stDirLast === 1 ? "#26a69a" : undefined,
          }}
        >
          {stState ?? "—"}
        </strong>
        {sb ? (
          <span className="obs-signal-bar-caption" style={{ marginLeft: "0.75rem", opacity: 0.9, fontWeight: 400 }}>
            Engine last bar: close {fmtNum(sb.close)} · prev {fmtNum(sb.closePrev)} · fast {fmtNum(sb.emaFast)} · slow{" "}
            {fmtNum(sb.emaSlow)}
            {sb.reason ? ` · ${String(sb.reason)}` : ""}
          </span>
        ) : null}
        <span className="obs-tv-toolbar">
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(0.7)}
            aria-label="Zoom in"
            title="Zoom in"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm1 2v2h2v2h-2v2H9v-2H7V9h2V7h2Zm8.7 13.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => zoomH(1.3)}
            aria-label="Zoom out"
            title="Zoom out"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M10 3a7 7 0 1 1 0 14a7 7 0 0 1 0-14Zm0 2a5 5 0 1 0 0 10a5 5 0 0 0 0-10Zm-3 4h6v2H7V9Zm12.7 11.3l-3.2-3.2a1 1 0 0 0-1.4 1.4l3.2 3.2a1 1 0 0 0 1.4-1.4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => resizeV(120)}
            aria-label="Taller"
            title="Taller"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M12 3l4 4h-3v10h3l-4 4l-4-4h3V7H8l4-4Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={() => resizeV(-120)}
            aria-label="Shorter"
            title="Shorter"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M16 7l-4-4l-4 4h3v10H8l4 4l4-4h-3V7h3Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={resetView}
            aria-label="Fit"
            title="Fit"
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            type="button"
            className="toggle-button obs-tv-icon-btn"
            onClick={toggleExpand}
            aria-label={expanded ? "Exit full screen" : "Expand"}
            title={expanded ? "Exit full screen" : "Expand"}
          >
            <svg viewBox="0 0 24 24" className="obs-tv-icon" aria-hidden="true">
              <path
                d="M4 9V4h5v2H6v3H4Zm14-3h-3V4h5v5h-2V6ZM6 18h3v2H4v-5h2v3Zm12-3h2v5h-5v-2h3v-3Z"
                fill="currentColor"
              />
            </svg>
          </button>
        </span>
      </div>
      <div ref={mainRef} className="obs-chart-wrap" />
      {optPts && optPts.length > 0 ? (
        <>
          <div className="obs-subchart-label">
            Option session VWAP
            {panel.optionSymbol ? ` · ${panel.optionSymbol}` : ""}
          </div>
          <div ref={optRef} className="obs-chart-wrap obs-chart-wrap--sub" />
        </>
      ) : (
        <p className="obs-chart-hint">No open or today&apos;s leg symbol — option VWAP appears when a position exists.</p>
      )}
      {expanded ? (
        <>
          <button type="button" className="obs-expand-close" onClick={toggleExpand} aria-label="Close full screen" title="Close">
            ×
          </button>
          <div className="obs-expand-backdrop" onClick={toggleExpand} />
        </>
      ) : null}
    </div>
  );
}

export default function ObservabilityCharts({ snapshot }: { snapshot: ObservabilitySnapshot }) {
  if (snapshot.error) {
    return <div className="obs-banner obs-banner--warn">{snapshot.error}</div>;
  }
  if (!snapshot.panels.length) {
    return (
      <div className="table-card obs-empty">
        <p>
          No observability panels yet. Subscribe to <strong>Stochastic BNF</strong>, <strong>SuperTrend Trail</strong>, or{" "}
          <strong>PS/VS MTF</strong> on the Strategies page (active subscription required).
        </p>
      </div>
    );
  }

  return (
    <div className="obs-panels">
      {snapshot.panels.map((p) => (
        <section key={`${p.strategyId}-${p.strategyVersion}`} className="table-card obs-panel">
          <header className="obs-panel-head">
            <h2>{p.displayName}</h2>
            <p className="obs-panel-meta">
              {p.instrument} · {p.interval} · {p.strategyId} {p.strategyVersion}
            </p>
          </header>
          {p.kind === "stochastic_bnf" ? <StochasticBnfBlock panel={p} /> : null}
          {p.kind === "supertrend_trail" ? <SuperTrendTrailBlock panel={p} /> : null}
          {p.kind === "ps_vs_mtf" ? <PsVsMtfBlock panel={p} /> : null}
        </section>
      ))}
    </div>
  );
}
