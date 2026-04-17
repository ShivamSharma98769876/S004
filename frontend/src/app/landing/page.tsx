"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppFrame from "@/components/AppFrame";
import {
  CePeFlowArena,
  ConfidenceGauge,
  CumulativeWaterfall,
  LandingWidgetHelp,
  MiniSparkline,
  RegimeBadge,
} from "@/components/landing/LandingDashWidgets";
import StrategyDayFitWidget, { type StrategyDayFitPayload } from "@/components/landing/StrategyDayFitWidget";
import { type NewsSentimentPayload } from "@/components/landing/NewsSentimentWidget";
import MarketVerdictWidget, { sessionTrendBias } from "@/components/landing/MarketVerdictWidget";
import OiSellerPinboardWidget, { type OiWallsPayload } from "@/components/landing/OiSellerPinboardWidget";
import SidewaysRegimePanel, { type SidewaysRegimePayload } from "@/components/landing/SidewaysRegimePanel";
import { apiJson, getAuth } from "@/lib/api_client";
import { formatTimeIST } from "@/lib/datetime_ist";

type MarketSnapshot = {
  nifty: { spot: number; changePct: number };
  nifty15m?: { changePct: number | null; trendLabel: string };
  pcr: number | null;
  sentimentLabel: string;
  intradayTrendLabel: string;
  vix?: number | null;
};

type OptionsIntelPayload = {
  hasChainData?: boolean;
  ceOiPct?: number | null;
  peOiPct?: number | null;
  oiDominant?: string;
  volDominant?: string;
  modelOptionTilt?: string;
  optionFlowScore?: number;
  flowBlendScore?: number;
  optionsOnlyScore?: number;
  bullishWingLabel?: string;
  headline?: string;
  dataCaveat?: string;
  playbookHeadline?: string;
  playbookDetail?: string;
  ceStrengthPct?: number;
  peStrengthPct?: number;
  suggestion?: string;
};

type SentimentPayload = {
  sentimentLabel: string;
  directionLabel: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  directionScore: number;
  confidence: number;
  regime: string;
  inputs?: {
    pcr?: number;
    pcrVol?: number;
    spotChgPct?: number;
    ceOi?: number;
    peOi?: number;
    ceVol?: number;
    peVol?: number;
  };
  optionsIntel?: OptionsIntelPayload | null;
  drivers: Array<{
    key: string;
    label: string;
    direction: "bullish" | "bearish";
    impact: number;
    value: number;
    reading?: string;
  }>;
  alerts: string[];
};

type DecisionSnapshotPayload = {
  marketSnapshot: MarketSnapshot;
  sentiment: SentimentPayload;
  /** Present from API; landing page does not render TrendPulse. */
  trendpulse?: unknown;
  strategyDayFit?: StrategyDayFitPayload;
  newsSentiment?: NewsSentimentPayload;
  oiWalls?: OiWallsPayload | null;
  sidewaysRegime?: SidewaysRegimePayload | null;
  updatedAt: string;
};

type SentimentHistoryPoint = {
  timestamp: string;
  directionScore: number;
  confidence: number;
  directionLabel: string;
  sentimentLabel: string;
  regime: string;
  modelOptionTilt?: string;
  ceStrengthPct?: number;
};

type SentimentReplayItem = {
  timestamp: string;
  sentiment: SentimentPayload;
};

type SentimentHistoryPayload = {
  limit: number;
  available: number;
  retention: string;
  points: SentimentHistoryPoint[];
  replay: SentimentReplayItem[];
};

export default function LandingPage() {
  const router = useRouter();
  const [snap, setSnap] = useState<MarketSnapshot | null>(null);
  const [sentiment, setSentiment] = useState<SentimentPayload | null>(null);
  const [history, setHistory] = useState<SentimentHistoryPayload | null>(null);
  const [strategyFit, setStrategyFit] = useState<StrategyDayFitPayload | null>(null);
  const [newsSentiment, setNewsSentiment] = useState<NewsSentimentPayload | null>(null);
  const [oiWalls, setOiWalls] = useState<OiWallsPayload | null>(null);
  const [sidewaysRegime, setSidewaysRegime] = useState<SidewaysRegimePayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastRefreshAt, setLastRefreshAt] = useState<string | null>(null);
  const inFlightRef = useRef(false);

  const load = useCallback(async () => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setErr(null);
    try {
      const [snapshotRes, histRes] = await Promise.allSettled([
        apiJson<DecisionSnapshotPayload>(
          "/api/landing/decision-snapshot",
          "GET",
          undefined,
          undefined,
          { timeoutMs: 35_000 },
        ),
        apiJson<SentimentHistoryPayload>("/api/landing/sentiment-history", "GET", undefined, { limit: 90 }),
      ]);
      let hadAnySuccess = false;
      let failureMsg: string | null = null;
      if (snapshotRes.status === "fulfilled") {
        const snapshot = snapshotRes.value;
        hadAnySuccess = true;
        setSnap(snapshot.marketSnapshot);
        setSentiment(snapshot.sentiment);
        setStrategyFit(snapshot.strategyDayFit ?? null);
        setNewsSentiment(snapshot.newsSentiment ?? null);
        setOiWalls(snapshot.oiWalls ?? null);
        setSidewaysRegime(snapshot.sidewaysRegime ?? null);
        setLastRefreshAt(snapshot.updatedAt ?? new Date().toISOString());
      } else {
        failureMsg = snapshotRes.reason instanceof Error ? snapshotRes.reason.message : "Failed to load snapshot";
      }
      if (histRes.status === "fulfilled") {
        hadAnySuccess = true;
        setHistory(histRes.value);
      } else if (!failureMsg) {
        failureMsg = histRes.reason instanceof Error ? histRes.reason.message : "Failed to load history";
      }
      setErr(hadAnySuccess ? null : failureMsg ?? "Failed to load");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getAuth()) {
      router.replace("/login");
      return;
    }
    void load();
    /** Same cadence as dashboard/trades after refresh-cycle. */
    const id = window.setInterval(() => void load(), 30_000);
    return () => window.clearInterval(id);
  }, [router, load]);

  const chg = snap?.nifty.changePct ?? 0;
  const niftySpotLive = snap != null && snap.nifty.spot > 0;
  const chgClass = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
  const dir = sentiment?.directionLabel ?? "NEUTRAL";
  const dirClass = dir === "BULLISH" ? "pos" : dir === "BEARISH" ? "neg" : "";
  const trendPoints = history?.points ?? [];
  const latestReplay = history?.replay?.[history.replay.length - 1];
  // Keep Drivers in lockstep with Conviction/market widgets (current snapshot first).
  const waterfallDrivers = (sentiment?.drivers ?? latestReplay?.sentiment?.drivers ?? []).slice(0, 6);
  const trendChart = useMemo(() => {
    const raw = trendPoints.map((p) => Number(p.directionScore) || 0);
    if (raw.length === 0) {
      return {
        linePath: "",
        areaPath: "",
        zeroY: 50,
        yMin: -100,
        yMax: 100,
        singlePoint: false,
      };
    }
    // One snapshot → duplicate so the line renders (flat until the next poll adds a second point).
    const vals = raw.length === 1 ? [raw[0]!, raw[0]!] : raw;
    const loRaw = Math.min(...vals, -10);
    const hiRaw = Math.max(...vals, 10);
    const pad = Math.max(8, (hiRaw - loRaw) * 0.12);
    const lo = Math.max(-100, loRaw - pad);
    const hi = Math.min(100, hiRaw + pad);
    const span = Math.max(1e-6, hi - lo);
    const xn = (i: number) => (i / (vals.length - 1)) * 100;
    const yn = (v: number) => 100 - ((v - lo) / span) * 100;
    const line = vals.map((v, i) => `${i === 0 ? "M" : "L"} ${xn(i).toFixed(3)} ${yn(v).toFixed(3)}`).join(" ");
    const area = `${line} L 100 100 L 0 100 Z`;
    const zeroY = yn(0);
    return {
      linePath: line,
      areaPath: area,
      zeroY,
      yMin: lo,
      yMax: hi,
      singlePoint: raw.length === 1,
    };
  }, [trendPoints]);
  const trendStart = trendPoints[0]?.timestamp;
  const trendEnd = trendPoints[trendPoints.length - 1]?.timestamp;
  const trendStartLabel = trendStart ? formatTimeIST(trendStart, { fallback: "—" }) : "—";
  const trendEndLabel = trendEnd ? formatTimeIST(trendEnd, { fallback: "—" }) : "—";
  const scoreSparkValues = useMemo(() => {
    const raw = trendPoints.map((p) => Number(p.directionScore) || 0);
    if (raw.length === 1) return [raw[0]!, raw[0]!];
    return raw;
  }, [trendPoints]);
  const pcrDisplay =
    snap?.pcr != null
      ? snap.pcr
      : typeof sentiment?.inputs?.pcr === "number" && Number.isFinite(sentiment.inputs.pcr)
        ? sentiment.inputs.pcr
        : null;

  const optIntel = sentiment?.optionsIntel;
  const replayOptIntel = latestReplay?.sentiment?.optionsIntel;

  const heroSessionBias = loading && !snap ? null : sessionTrendBias(snap?.intradayTrendLabel);
  const m15 = snap?.nifty15m;
  const m15Pct = m15?.changePct;
  const m15Label = m15?.trendLabel;
  const hero15mBias =
    loading && !snap
      ? null
      : m15Label != null && m15Label !== "—"
        ? sessionTrendBias(m15Label)
        : null;
  const trendGradId = `landingTrendFill-${useId().replace(/:/g, "")}`;

  return (
    <AppFrame>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "0.6rem" }}>
        <span className="summary-label">
          Last refresh: {lastRefreshAt ? formatTimeIST(lastRefreshAt, { fallback: "—" }) : "—"}
        </span>
      </div>
      {err && (
        <div className="panel-accent-chain" style={{ padding: "0.75rem 1rem", marginBottom: "1rem", borderRadius: 8 }}>
          {err}
        </div>
      )}

      <div className="landing-dash">
        <section className="landing-dash-hero landing-dash-hero--compact panel-accent-chain" aria-label="Market overview">
          <div className="landing-hero-cell landing-hero-nifty landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Live NIFTY 50 level and session % change."
              usage="Use as broad market context; fast moves can shift option premiums and risk — size and timing should reflect that."
            />
            <span className="landing-hero-eyebrow">NIFTY 50</span>
            <div className="landing-hero-nifty-row">
              <span className="landing-hero-price">
                {loading && !snap
                  ? "—"
                  : niftySpotLive
                    ? snap!.nifty.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })
                    : "—"}
              </span>
              <span className={`landing-delta-pill ${chgClass}`}>
                {loading && !snap
                  ? "…"
                  : niftySpotLive
                    ? `${chg >= 0 ? "+" : ""}${snap!.nifty.changePct.toFixed(2)}%`
                    : "n/a"}
              </span>
            </div>
          </div>

          <div className="landing-hero-divider" aria-hidden />

          <div className="landing-hero-cell landing-hero-flow landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Put/call ratio (PCR) from the live option chain when your broker is connected. Detail and direction score are in Market pulse below."
              usage="PCR above ~1 means more put OI/volume than calls (often read as cautious); below ~1 the opposite. Not a trade signal by itself."
            />
            <span className="landing-hero-eyebrow">Option flow</span>
            <div className="landing-hero-flow-inline landing-hero-flow-inline--pcr">
              <span
                className="landing-hero-pcr landing-hero-pcr--hero"
                title={
                  pcrDisplay != null
                    ? "Put/call ratio from chain"
                    : "Connect broker / ensure option chain is available for live PCR"
                }
              >
                <span className="landing-hero-pcr-k">PCR</span>
                <span className="landing-hero-pcr-v">
                  {loading && !snap && !sentiment
                    ? "…"
                    : pcrDisplay != null
                      ? pcrDisplay.toFixed(2)
                      : "n/a"}
                </span>
              </span>
            </div>
          </div>

          <div className="landing-hero-divider" aria-hidden />

          <div className="landing-hero-cell landing-hero-trend landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Session = full-day NIFTY % change. 15m = last 15-minute bar vs the prior bar (same index)."
              usage="Use 15m for very recent swing vs session for day bias."
            />
            <span className="landing-hero-eyebrow">Trend</span>
            <div className="landing-hero-trend-stack">
              <div className="landing-hero-tf-row">
                <div className="landing-hero-tf-meta">
                  <span className="landing-hero-tf-name">Session</span>
                  <span className="landing-hero-tf-sub muted" title="NIFTY session % change (same as index pill)">
                    {loading && !snap
                      ? "NIFTY day %"
                      : niftySpotLive
                        ? `Day ${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`
                        : "NIFTY day % —"}
                  </span>
                </div>
                <span
                  className={
                    heroSessionBias == null
                      ? "landing-hero-tf-bias landing-hero-tf-bias--loading muted"
                      : `landing-hero-tf-bias landing-hero-tf-bias--${heroSessionBias.toLowerCase()}`
                  }
                >
                  {heroSessionBias ?? "…"}
                </span>
              </div>
              <div className="landing-hero-tf-row">
                <div className="landing-hero-tf-meta">
                  <span className="landing-hero-tf-name">15m</span>
                  <span
                    className="landing-hero-tf-sub muted"
                    title="NIFTY: last 15-minute candle close vs previous bar (IST session)"
                  >
                    {loading && !snap
                      ? "Bar vs prior"
                      : m15Pct != null && Number.isFinite(m15Pct)
                        ? `Bar ${m15Pct >= 0 ? "+" : ""}${m15Pct.toFixed(2)}%`
                        : "Bar vs prior —"}
                  </span>
                </div>
                <span
                  className={
                    loading && !snap
                      ? "landing-hero-tf-bias landing-hero-tf-bias--loading muted"
                      : hero15mBias
                        ? `landing-hero-tf-bias landing-hero-tf-bias--${hero15mBias.toLowerCase()}`
                        : "landing-hero-tf-bias muted"
                  }
                >
                  {loading && !snap ? "…" : hero15mBias ?? "—"}
                </span>
              </div>
            </div>
          </div>
        </section>

        <MarketVerdictWidget
          loading={loading}
          sentiment={sentiment}
          snap={snap}
          tp={null}
          news={newsSentiment}
        />

        <div className="landing-sideways-regime-outer">
          <SidewaysRegimePanel data={sidewaysRegime} loading={loading && !sidewaysRegime} />
        </div>

        <section className="landing-oi-pinboard-wrap" aria-label="Open interest walls">
          <OiSellerPinboardWidget data={oiWalls} loading={loading && !oiWalls} />
        </section>

        <section className="landing-bento" aria-label="Sentiment analytics">
          <div className="landing-bento-conviction landing-bento-cell panel-accent-signals landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Confidence in the current bias plus market regime (e.g. trending vs choppy)."
              usage="CE vs PE block uses the same inputs as Drivers (PCR, OI, volume, momentum). Flow tilt CE means the chain skews toward call-side participation in this model; PE the opposite. Higher confidence in a trending regime usually supports cleaner follow-through; low confidence or chop suggests smaller size or waiting."
            />
            <header className="landing-bento-head">
              <span className="landing-bento-title">Conviction</span>
              <span className="landing-bento-live" title="Data refreshes every minute">
                <span className="landing-bento-live-dot" aria-hidden />
                Live
              </span>
            </header>
            <div className="landing-bento-gauge-row">
              <ConfidenceGauge value={sentiment?.confidence ?? 0} loading={loading && !sentiment} compact />
              <RegimeBadge regime={sentiment?.regime ?? ""} loading={loading && !sentiment} compact />
            </div>
            <div className="landing-bento-kpis" role="group" aria-label="Quick figures">
              <div className="landing-kpi-tile">
                <span className="landing-kpi-k">Dir. score</span>
                <span className={`landing-kpi-v ${dirClass}`}>
                  {loading && !sentiment ? "—" : sentiment?.directionScore?.toFixed(1) ?? "—"}
                </span>
              </div>
              <div className="landing-kpi-tile">
                <span className="landing-kpi-k">PCR</span>
                <span className="landing-kpi-v">
                  {loading && !sentiment && pcrDisplay == null
                    ? "—"
                    : pcrDisplay != null
                      ? pcrDisplay.toFixed(2)
                      : "—"}
                </span>
              </div>
            </div>
            <div className="landing-options-intel" role="region" aria-label="CE versus PE option positioning">
              <CePeFlowArena
                variant="full"
                loading={loading && !sentiment}
                cePct={optIntel?.ceStrengthPct ?? 50}
                pePct={optIntel?.peStrengthPct ?? 50}
                wingLabel={optIntel?.bullishWingLabel ?? "CE / PE balanced"}
                tilt={optIntel?.modelOptionTilt ?? "NEUTRAL"}
                playbookHeadline={optIntel?.playbookHeadline ?? "Playbook loads with snapshot."}
              />
              <div className="landing-options-intel-chips">
                {optIntel?.oiDominant && optIntel.oiDominant !== "EVEN" ? (
                  <span className={`landing-opt-chip landing-opt-chip--dom landing-opt-chip--${optIntel.oiDominant.toLowerCase()}`}>
                    OI lead: {optIntel.oiDominant}
                  </span>
                ) : null}
                {optIntel?.volDominant && optIntel.volDominant !== "EVEN" ? (
                  <span className={`landing-opt-chip landing-opt-chip--dom landing-opt-chip--${optIntel.volDominant.toLowerCase()}`}>
                    Vol lead: {optIntel.volDominant}
                  </span>
                ) : null}
                {typeof optIntel?.flowBlendScore === "number" ? (
                  <span className="landing-opt-chip landing-opt-chip--meta" title="Blended options + index direction (-1 PE … +1 CE)">
                    Blend {optIntel.flowBlendScore >= 0 ? "+" : ""}
                    {optIntel.flowBlendScore.toFixed(2)}
                  </span>
                ) : null}
              </div>
              {optIntel?.ceOiPct != null && optIntel?.peOiPct != null ? (
                <p className="landing-options-intel-split muted">
                  Chain OI share · CE {optIntel.ceOiPct.toFixed(0)}% · PE {optIntel.peOiPct.toFixed(0)}%
                </p>
              ) : null}
              <p className="landing-options-intel-headline">
                {loading && !sentiment ? "…" : optIntel?.headline ?? "Option skew loads with the live chain."}
              </p>
              {optIntel?.playbookDetail ? (
                <p className="landing-options-intel-playbook-detail">{optIntel.playbookDetail}</p>
              ) : null}
              {optIntel?.dataCaveat ? (
                <p className="landing-options-intel-caveat muted">{optIntel.dataCaveat}</p>
              ) : null}
              <p className="landing-options-intel-suggest muted">
                {loading && !sentiment ? "" : optIntel?.suggestion ?? ""}
              </p>
            </div>
            <div className="landing-bento-spark-block">
              <div className="landing-bento-spark-head">
                <span>Score path</span>
                <span className="landing-bento-spark-hint" title="From saved snapshots">
                  {scoreSparkValues.length >= 2 ? `${scoreSparkValues.length} pts` : "Building…"}
                </span>
              </div>
              <div className="landing-bento-spark-shell">
                <MiniSparkline values={scoreSparkValues} className="landing-bento-spark" stroke="var(--accent)" />
              </div>
            </div>
            <div className="landing-bento-alerts" role="region" aria-label="Alerts">
              <span className="landing-bento-alerts-label">Alerts</span>
              <div className="landing-alert-chips landing-alert-chips--tight">
                {(sentiment?.alerts ?? ["No high-conviction alert right now."]).map((a, idx) => (
                  <div key={idx} className="landing-alert-chip">
                    {a}
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="landing-bento-replay landing-bento-cell panel-accent-chain landing-widget-help-host">
            <LandingWidgetHelp
              meaning="History of the option-flow direction score; dashed line is neutral (0)."
              usage="Each time Home loads (or auto-refreshes every 60s), the server saves one point. You see a flat line after the first point; the path moves once there are two or more snapshots. With Redis configured, history survives server restarts."
            />
            <header className="landing-bento-head">
              <div>
                <span className="landing-bento-title">Sentiment replay</span>
                <p className="landing-bento-sub">
                  Direction score over time (zero line = neutral). Dots below show how CE vs PE tilt evolved on each saved
                  snapshot (last 16 points).
                </p>
              </div>
              {trendPoints.length === 0 ? (
                <span className="landing-pill-stat landing-pill-stat--dim">No history yet</span>
              ) : trendChart.singlePoint ? (
                <span className="landing-pill-stat landing-pill-stat--dim" title="Wait for next refresh or revisit Home">
                  n=1 · next poll draws slope
                </span>
              ) : (
                <span className="landing-pill-stat">
                  n={trendPoints.length} · [{Math.round(trendChart.yMin)}, {Math.round(trendChart.yMax)}]
                </span>
              )}
            </header>
            {trendPoints.length === 0 ? (
              <div className="landing-replay-empty landing-replay-empty--short">
                <MiniSparkline values={[]} />
                <p className="muted landing-replay-empty-text">
                  Open Home once the backend is up — the first snapshot is stored when this page loads successfully.
                </p>
              </div>
            ) : (
              <div className="landing-trend-widget landing-trend-widget--bento">
                {trendChart.singlePoint ? (
                  <p className="landing-replay-single-hint muted" role="status">
                    Current direction score:{" "}
                    <strong className="landing-replay-single-val">
                      {(Number(trendPoints[0]?.directionScore) || 0).toFixed(1)}
                    </strong>
                    {" — "}
                    flat line until a second snapshot arrives (stay on this page ~60s or refresh).
                  </p>
                ) : null}
                <svg
                  className="landing-trend-svg landing-trend-svg--bento"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  aria-label="Sentiment direction score trend"
                >
                  <line
                    x1="0"
                    y1={trendChart.zeroY}
                    x2="100"
                    y2={trendChart.zeroY}
                    stroke="var(--muted)"
                    strokeOpacity="0.45"
                    strokeWidth="0.45"
                    strokeDasharray="2 2"
                  />
                  <path d={trendChart.areaPath} fill={`url(#${trendGradId})`} />
                  <path d={trendChart.linePath} fill="none" stroke="#4f7cff" strokeWidth="1.2" />
                  <defs>
                    <linearGradient id={trendGradId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="rgba(79,124,255,0.5)" />
                      <stop offset="100%" stopColor="rgba(79,124,255,0.02)" />
                    </linearGradient>
                  </defs>
                </svg>
                <div className="landing-trend-axis landing-trend-axis--bento">
                  <span title="Oldest snapshot">{trendStartLabel}</span>
                  <span className="landing-trend-axis-arrow" aria-hidden>
                    →
                  </span>
                  <span title="Newest snapshot">{trendEndLabel}</span>
                </div>
                {replayOptIntel?.headline ? (
                  <p className="landing-replay-opt-foot muted" role="note">
                    Latest snapshot option read: <strong>{replayOptIntel.headline}</strong>
                  </p>
                ) : null}
                {trendPoints.length >= 2 ? (
                  <div className="landing-replay-cepe-strip" role="img" aria-label="Flow tilt over recent snapshots">
                    <span className="landing-replay-cepe-strip-label muted">Flow tilt trail</span>
                    <div className="landing-replay-cepe-dots">
                      {trendPoints.slice(-16).map((p, i) => {
                        const t = (p.modelOptionTilt ?? "NEUTRAL").toLowerCase();
                        return (
                          <span
                            key={`${p.timestamp}-${i}`}
                            className={`landing-replay-cepe-dot landing-replay-cepe-dot--${t}`}
                            title={`${formatTimeIST(p.timestamp, { fallback: "—" })} · ${p.modelOptionTilt ?? "NEUTRAL"}${p.ceStrengthPct != null ? ` · CE ${p.ceStrengthPct}%` : ""}`}
                          />
                        );
                      })}
                    </div>
                  </div>
                ) : null}
              </div>
            )}
          </div>

          <div className="landing-bento-drivers landing-bento-cell panel-accent-chain landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Breakdown of what pushed the direction score (spot, PCR, momentum, etc.)."
              usage="The number on the right is each driver’s weighted impact on the direction score, not raw PCR. PCR ≈ 1.0 and balanced OI produce ~0 impact even when data is present (neutral contribution); the line under each name is the live reading. In the bridge and sliders, constructive (call-side) pulls left and cautious (put-side) pulls right. Several drivers aligned is stronger than one dominant driver."
            />
            <header className="landing-bento-head">
              <div>
                <span className="landing-bento-title">Drivers</span>
                <p className="landing-bento-sub">
                  Who moved the score · bridge + bars
                  {optIntel?.modelOptionTilt && optIntel.modelOptionTilt !== "NEUTRAL" ? (
                    <>
                      {" "}
                      · model flow tilt <strong className="landing-drivers-tilt">{optIntel.modelOptionTilt}</strong>
                    </>
                  ) : null}
                </p>
              </div>
              {lastRefreshAt && (
                <time className="landing-waterfall-time" dateTime={lastRefreshAt}>
                  {formatTimeIST(lastRefreshAt, { seconds: true })}
                </time>
              )}
            </header>
            <CePeFlowArena
              variant="compact"
              loading={loading && !sentiment}
              cePct={optIntel?.ceStrengthPct ?? 50}
              pePct={optIntel?.peStrengthPct ?? 50}
              wingLabel={optIntel?.bullishWingLabel ?? "CE / PE balanced"}
              tilt={optIntel?.modelOptionTilt ?? "NEUTRAL"}
              playbookHeadline={optIntel?.playbookHeadline ?? ""}
            />
            <CumulativeWaterfall
              dense
              drivers={waterfallDrivers.map((d) => ({
                key: d.key,
                label: d.label,
                impact: d.impact,
                direction: d.direction,
                reading: d.reading,
              }))}
            />
          </div>
        </section>
      </div>

      <div className="landing-strategy-fit-host landing-widget-help-host">
        <LandingWidgetHelp
          meaning="Daily option-buyer vs option-seller style picks from live context, plus recent outcome stats when available."
          usage="Use to choose or sanity-check which strategy family fits today — not a guarantee. Confirm with your risk settings and execution rules before trading."
        />
        <StrategyDayFitWidget data={strategyFit} loading={loading} />
      </div>
    </AppFrame>
  );
}
