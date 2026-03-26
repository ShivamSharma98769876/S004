"use client";

import { useCallback, useEffect, useId, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import AppFrame from "@/components/AppFrame";
import TrendPulseChart, {
  type TrendPulseEntryEvent,
  type TrendPulseTradeEvent,
  type TrendPulseTradeSignal,
} from "@/components/TrendPulseChart";
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
import MarketVerdictWidget, {
  formatTpInterval,
  htfTrendBias,
  sessionTrendBias,
} from "@/components/landing/MarketVerdictWidget";
import { apiJson, getAuth } from "@/lib/api_client";
import { formatTimeIST } from "@/lib/datetime_ist";

type MarketSnapshot = {
  nifty: { spot: number; changePct: number };
  pcr: number | null;
  sentimentLabel: string;
  intradayTrendLabel: string;
};

type TrendPulsePayload = {
  strategyId: string | null;
  strategyVersion: string | null;
  strategyType: string;
  trendpulseEnabled: boolean;
  stInterval?: string;
  htfInterval?: string;
  htfBias?: string | null;
  series: {
    times: string[];
    ps_z: number[];
    vs_z: number[];
    adx_last: number | null;
    displayDate?: string | null;
    displayTimezone?: string;
    displayDateFallback?: boolean;
    noBarsForDisplayDate?: boolean;
    chartHint?: string | null;
  } | null;
  entryEvents?: TrendPulseEntryEvent[];
  tradeEvents?: TrendPulseTradeEvent[];
  tradeSignal?: TrendPulseTradeSignal | null;
  message: string | null;
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
  trendpulse: TrendPulsePayload;
  strategyDayFit?: StrategyDayFitPayload;
  newsSentiment?: NewsSentimentPayload;
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
  const [tp, setTp] = useState<TrendPulsePayload | null>(null);
  const [history, setHistory] = useState<SentimentHistoryPayload | null>(null);
  const [strategyFit, setStrategyFit] = useState<StrategyDayFitPayload | null>(null);
  const [newsSentiment, setNewsSentiment] = useState<NewsSentimentPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const snapshot = await apiJson<DecisionSnapshotPayload>("/api/landing/decision-snapshot");
      const hist = await apiJson<SentimentHistoryPayload>("/api/landing/sentiment-history", "GET", undefined, { limit: 90 });
      setSnap(snapshot.marketSnapshot);
      setSentiment(snapshot.sentiment);
      setTp(snapshot.trendpulse);
      setStrategyFit(snapshot.strategyDayFit ?? null);
      setNewsSentiment(snapshot.newsSentiment ?? null);
      setHistory(hist);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!getAuth()) {
      router.replace("/login");
      return;
    }
    void load();
    const id = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(id);
  }, [router, load]);

  const chg = snap?.nifty.changePct ?? 0;
  const chgClass = chg > 0 ? "pos" : chg < 0 ? "neg" : "";
  const dir = sentiment?.directionLabel ?? "NEUTRAL";
  const dirClass = dir === "BULLISH" ? "pos" : dir === "BEARISH" ? "neg" : "";
  const trendPoints = history?.points ?? [];
  const latestReplay = history?.replay?.[history.replay.length - 1];
  const waterfallDrivers = (latestReplay?.sentiment?.drivers ?? sentiment?.drivers ?? []).slice(0, 6);
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
  const heroHtfBias = tp?.trendpulseEnabled ? htfTrendBias(tp.htfBias) : null;
  const trendGradId = `landingTrendFill-${useId().replace(/:/g, "")}`;
  const tpSessionNote =
    tp?.series?.displayDate != null && tp?.series?.displayDate !== ""
      ? `Session ${tp.series.displayDate}${tp.series.displayTimezone === "Asia/Kolkata" || !tp.series.displayTimezone ? " (IST)" : ` (${tp.series.displayTimezone})`}${tp.series.noBarsForDisplayDate ? " · no bars in feed yet" : ""}`
      : null;

  return (
    <AppFrame>
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
                {loading && !snap ? "—" : snap?.nifty.spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
              </span>
              <span className={`landing-delta-pill ${chgClass}`}>
                {loading && !snap ? "…" : `${chg >= 0 ? "+" : ""}${snap?.nifty.changePct.toFixed(2) ?? "—"}%`}
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
              meaning="Session = today’s NIFTY % move as Bullish/Bearish/Sideways. HTF shows TrendPulse higher-timeframe bias and its chart interval. Signal = TrendPulse chart timeframe (see Market pulse)."
              usage="Prefer trades that align with HTF bias when TrendPulse is on; counter-trend entries need stricter risk checks in your settings."
            />
            <span className="landing-hero-eyebrow">Trend</span>
            <div className="landing-hero-trend-stack">
              <div className="landing-hero-tf-row">
                <div className="landing-hero-tf-meta">
                  <span className="landing-hero-tf-name">Session</span>
                  <span className="landing-hero-tf-sub muted">NIFTY day %</span>
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
              {tp?.trendpulseEnabled && heroHtfBias ? (
                <div className="landing-hero-tf-row">
                  <div className="landing-hero-tf-meta">
                    <span className="landing-hero-tf-name">HTF {formatTpInterval(tp.htfInterval)}</span>
                    <span className="landing-hero-tf-sub muted">TrendPulse</span>
                  </div>
                  <span className={`landing-hero-tf-bias landing-hero-tf-bias--${heroHtfBias.toLowerCase()}`}>
                    {heroHtfBias}
                  </span>
                </div>
              ) : null}
              {tp?.trendpulseEnabled ? (
                <p className="landing-hero-tf-signal muted">
                  Signal <strong>{formatTpInterval(tp.stInterval)}</strong>
                </p>
              ) : null}
            </div>
          </div>
        </section>

        <MarketVerdictWidget
          loading={loading}
          sentiment={sentiment}
          snap={snap}
          tp={tp}
          news={newsSentiment}
        />

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
              usage="The number on the right is each driver’s weighted impact on the direction score, not raw PCR. PCR ≈ 1.0 and balanced OI produce ~0 impact even when data is present. The smaller line under each name shows the live reading (ratios, OI counts). Several drivers aligned is stronger than one dominant driver."
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
              {latestReplay?.timestamp && (
                <time className="landing-waterfall-time" dateTime={latestReplay.timestamp}>
                  {formatTimeIST(latestReplay.timestamp, { seconds: true })}
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

      <section className="landing-tp-panel panel-accent-chain landing-widget-help-host">
        <LandingWidgetHelp
          meaning="TrendPulse Z: PS_z vs VS_z on your signal timeframe. Rupee icons mark bars where this strategy actually opened a trade."
          usage="Read the banner for live eligibility. Hover a rupee for IST time, strike, and symbol. Scroll the chart to see earlier session bars."
        />
        <header className="landing-tp-head">
          <div>
            <h2 className="landing-tp-title">TrendPulse Z</h2>
            <p className="landing-tp-sub">Strategy signal on your subscribed / active plan</p>
          </div>
        </header>
        <div className="landing-tp-body">
        {loading && !tp ? (
          <p className="muted">Loading…</p>
        ) : tp && !tp.trendpulseEnabled ? (
          <p className="muted">{tp.message}</p>
        ) : !tp?.series?.times?.length ? (
          <p className="muted">{tp?.series?.chartHint ?? tp?.message ?? "No chart data for this session."}</p>
        ) : (
          <>
            <p className="landing-tp-meta muted">
              {(tp?.stInterval ?? "5minute").replace("minute", "m")} bars · ADX (ST) last: {tp?.series?.adx_last ?? "—"}
              {tp?.tradeSignal?.adxMin != null && tp?.tradeSignal?.adxSt != null && (
                <> · min ADX {tp.tradeSignal.adxMin}</>
              )}
              {tpSessionNote ? <> · {tpSessionNote}</> : null}
            </p>
            {tp?.tradeSignal && (
              <div
                className={`trendpulse-signal-banner${tp.tradeSignal.entryEligible ? " trendpulse-signal-banner--ok" : " trendpulse-signal-banner--hold"}`}
                role="status"
              >
                <div className="trendpulse-signal-banner-title">
                  {tp.tradeSignal.entryEligible ? "Entry conditions satisfied (latest bar)" : "Wait — entry not confirmed"}
                </div>
                <p className="trendpulse-signal-banner-text">{tp.tradeSignal.summary}</p>
                {tp.tradeSignal.recommendation && (
                  <div className="trendpulse-rec-box" role="region" aria-label="Plan recommendation">
                    <div className="trendpulse-rec-title">Plan recommendation (₹ at risk)</div>
                    <p className="trendpulse-rec-body">
                      {tp.tradeSignal.recommendation.strike != null && Number.isFinite(tp.tradeSignal.recommendation.strike) ? (
                        <>
                          <strong>
                            {Number(tp.tradeSignal.recommendation.strike).toLocaleString("en-IN")}{" "}
                            {tp.tradeSignal.recommendation.optionType ?? ""}
                          </strong>
                          {" · "}
                        </>
                      ) : null}
                      <span className="trendpulse-rec-symbol">{tp.tradeSignal.recommendation.symbol || "—"}</span>
                      {" · "}
                      Entry {tp.tradeSignal.recommendation.entryPrice?.toFixed?.(2) ?? "—"} · T{" "}
                      {tp.tradeSignal.recommendation.targetPrice?.toFixed?.(2) ?? "—"} · SL{" "}
                      {tp.tradeSignal.recommendation.stopLossPrice?.toFixed?.(2) ?? "—"}
                      {tp.tradeSignal.recommendation.confidenceScore != null ? (
                        <> · conf. {Math.round(tp.tradeSignal.recommendation.confidenceScore)}%</>
                      ) : null}
                    </p>
                  </div>
                )}
              </div>
            )}
            <TrendPulseChart
              times={tp?.series?.times ?? []}
              psZ={tp?.series?.ps_z ?? []}
              vsZ={tp?.series?.vs_z ?? []}
              stIntervalLabel={(tp?.stInterval ?? "5minute").replace("minute", "m")}
              sessionDayNote={tpSessionNote}
              tradeSignal={tp?.tradeSignal ?? null}
              entryEvents={tp?.entryEvents ?? []}
              tradeEvents={tp?.tradeEvents ?? []}
            />
          </>
        )}
        </div>
      </section>
    </AppFrame>
  );
}
