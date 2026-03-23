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
  ConfidenceGauge,
  CumulativeWaterfall,
  DirectionChip,
  LandingWidgetHelp,
  MiniSparkline,
  RegimeBadge,
} from "@/components/landing/LandingDashWidgets";
import StrategyDayFitWidget, { type StrategyDayFitPayload } from "@/components/landing/StrategyDayFitWidget";
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

type SentimentPayload = {
  sentimentLabel: string;
  directionLabel: "BULLISH" | "BEARISH" | "NEUTRAL" | string;
  directionScore: number;
  confidence: number;
  regime: string;
  drivers: Array<{
    key: string;
    label: string;
    direction: "bullish" | "bearish";
    impact: number;
    value: number;
  }>;
  alerts: string[];
};

type DecisionSnapshotPayload = {
  marketSnapshot: MarketSnapshot;
  sentiment: SentimentPayload;
  trendpulse: TrendPulsePayload;
  strategyDayFit?: StrategyDayFitPayload;
  updatedAt: string;
};

type SentimentHistoryPoint = {
  timestamp: string;
  directionScore: number;
  confidence: number;
  directionLabel: string;
  sentimentLabel: string;
  regime: string;
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
    if (trendPoints.length < 2) return { linePath: "", areaPath: "", zeroY: 50, yMin: -100, yMax: 100 };
    const vals = trendPoints.map((p) => Number(p.directionScore) || 0);
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
    return { linePath: line, areaPath: area, zeroY, yMin: lo, yMax: hi };
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
  const htfClass =
    tp?.htfBias === "bullish" ? "landing-htf-chip--bull" : tp?.htfBias === "bearish" ? "landing-htf-chip--bear" : "landing-htf-chip--flat";
  const trendGradId = `landingTrendFill-${useId().replace(/:/g, "")}`;
  const tpSessionNote =
    tp?.series?.displayDate != null && tp?.series?.displayDate !== ""
      ? `Session ${tp.series.displayDate}${tp.series.displayTimezone === "Asia/Kolkata" || !tp.series.displayTimezone ? " (IST)" : ` (${tp.series.displayTimezone})`}${tp.series.noBarsForDisplayDate ? " · no bars in feed yet" : ""}`
      : null;

  return (
    <AppFrame title="Home" subtitle="Live context, sentiment, and TrendPulse — one screen.">
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
              meaning="Options sentiment label, direction score (bullish/bearish strength), and PCR."
              usage="Treat as directional bias, not a trade signal by itself. Align option leg choice (CE vs PE) with the stronger side when your plan allows."
            />
            <span className="landing-hero-eyebrow">Option flow</span>
            <div className="landing-hero-flow-inline">
              <p className={`landing-hero-sent-label ${dirClass}`}>
                {loading && !sentiment ? "…" : sentiment?.sentimentLabel ?? snap?.sentimentLabel ?? "—"}
              </p>
              <DirectionChip label={String(sentiment?.directionLabel ?? "NEUTRAL")} score={Number(sentiment?.directionScore ?? 0)} />
              <span className="landing-hero-pcr landing-hero-pcr--inline">PCR {snap?.pcr != null ? snap.pcr.toFixed(2) : "—"}</span>
            </div>
          </div>

          <div className="landing-hero-divider" aria-hidden />

          <div className="landing-hero-cell landing-hero-trend landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Intraday trend label plus higher-timeframe (HTF) TrendPulse bias."
              usage="Prefer trades that align with HTF bias; counter-trend entries need stricter risk checks in your settings."
            />
            <span className="landing-hero-eyebrow">Trend</span>
            <div className="landing-hero-trend-row">
              <span className="landing-intraday-pill">
                {loading && !snap ? "…" : snap?.intradayTrendLabel ?? "—"}
              </span>
              {tp?.trendpulseEnabled ? (
                <span className={`landing-htf-chip ${htfClass}`}>
                  HTF {(tp.htfInterval ?? "15minute").replace("minute", "m")} · {tp.htfBias ?? "—"}
                </span>
              ) : (
                <span className="landing-htf-chip landing-htf-chip--muted">TPZ below</span>
              )}
            </div>
          </div>
        </section>

        <section className="landing-bento" aria-label="Sentiment analytics">
          <div className="landing-bento-conviction landing-bento-cell panel-accent-signals landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Confidence in the current bias plus market regime (e.g. trending vs choppy)."
              usage="Higher confidence in a trending regime usually supports cleaner directional follow-through; low confidence or chop suggests smaller size or waiting. Score path and alerts below are part of this picture."
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
                <span className="landing-kpi-v">{snap?.pcr != null ? snap.pcr.toFixed(2) : "—"}</span>
              </div>
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
              meaning="History of the direction score; the dashed line is neutral (0)."
              usage="Judge persistence: a steady drift above or below zero is more meaningful than one spike. Needs a few Home refreshes (polls) before the line appears."
            />
            <header className="landing-bento-head">
              <div>
                <span className="landing-bento-title">Sentiment replay</span>
                <p className="landing-bento-sub">Direction score over time (zero line = neutral)</p>
              </div>
              {trendPoints.length >= 2 ? (
                <span className="landing-pill-stat">
                  n={trendPoints.length} · [{Math.round(trendChart.yMin)}, {Math.round(trendChart.yMax)}]
                </span>
              ) : (
                <span className="landing-pill-stat landing-pill-stat--dim">Need 2+ polls</span>
              )}
            </header>
            {trendPoints.length < 2 ? (
              <div className="landing-replay-empty landing-replay-empty--short">
                <MiniSparkline values={[]} />
                <p className="muted landing-replay-empty-text">Stay on Home or revisit — each load adds a snapshot.</p>
              </div>
            ) : (
              <div className="landing-trend-widget landing-trend-widget--bento">
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
              </div>
            )}
          </div>

          <div className="landing-bento-drivers landing-bento-cell panel-accent-chain landing-widget-help-host">
            <LandingWidgetHelp
              meaning="Breakdown of what pushed the direction score (spot, PCR, momentum, etc.)."
              usage="Several drivers pointing the same way is stronger than one dominant driver. If one item explains almost everything, the read can flip quickly when that input changes."
            />
            <header className="landing-bento-head">
              <div>
                <span className="landing-bento-title">Drivers</span>
                <p className="landing-bento-sub">Who moved the score · bridge + bars</p>
              </div>
              {latestReplay?.timestamp && (
                <time className="landing-waterfall-time" dateTime={latestReplay.timestamp}>
                  {formatTimeIST(latestReplay.timestamp, { seconds: true })}
                </time>
              )}
            </header>
            <CumulativeWaterfall
              dense
              drivers={waterfallDrivers.map((d) => ({
                key: d.key,
                label: d.label,
                impact: d.impact,
                direction: d.direction,
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
