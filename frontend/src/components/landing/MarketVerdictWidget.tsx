"use client";

import { useMemo } from "react";
import { LandingWidgetHelp } from "@/components/landing/LandingDashWidgets";
import {
  newestHeadlinePublishedAt,
  type NewsSentimentItem,
  type NewsSentimentPayload,
} from "@/components/landing/NewsSentimentWidget";
import { formatTimeIST } from "@/lib/datetime_ist";

type MarketSnapshot = {
  intradayTrendLabel: string;
};

type SentimentPayload = {
  directionLabel: string;
  directionScore: number;
  sentimentLabel?: string;
};

type TrendPulsePayload = {
  trendpulseEnabled: boolean;
  htfBias?: string | null;
  htfInterval?: string;
};

export type FlowTone = "bull" | "bear" | "neutral";
export type NewsTone = "pos" | "neg" | "neutral" | "empty" | "error";
export type SpotTone = "up" | "down" | "flat";
export type HtfTone = "bull" | "bear" | "flat" | "off";

export type MarketVerdict = {
  label: string;
  labelKey: "bullish" | "bearish" | "neutral" | "mixed";
  score: number;
  summary: string;
  summaryBullets: string[];
  flowSummary: string;
  newsSummary: string;
  trendSummary: string;
  caveat: string;
  pillars: {
    flow: { tone: FlowTone; scoreNum: number | null };
    news: { tone: NewsTone; scoreNum: number | null };
    trend: { spot: SpotTone; htf: HtfTone };
  };
};

function IconFlow({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M4 18V6M4 18h3M4 14h3M4 10h3M4 6h3" strokeLinecap="round" />
      <path d="M11 18V8M11 18h3M11 13h3M11 8h3" strokeLinecap="round" />
      <path d="M18 18v-9M18 18h2M18 12h2M18 6h2" strokeLinecap="round" />
    </svg>
  );
}

function IconNews({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M4 5h12a2 2 0 012 2v11H6a2 2 0 01-2-2V5z" strokeLinejoin="round" />
      <path d="M8 9h8M8 13h5" strokeLinecap="round" />
      <path d="M18 8h2v12H8" strokeLinejoin="round" />
    </svg>
  );
}

function IconTrend({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M4 16l4-5 4 3 4-6 4 4" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M17 8h3v3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconArrowUp({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden>
      <path d="M12 19V5M12 5l-6 6M12 5l6 6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconArrowDown({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.25" aria-hidden>
      <path d="M12 5v14M12 19l6-6M12 19l-6-6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconDash({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M6 12h12" strokeLinecap="round" />
    </svg>
  );
}

function IconScale({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M12 3v18M5 8l7-5 7 5M5 16l7 5 7-5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function IconAlert({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
      <path d="M10.3 3.6L2.6 17.4a1.5 1.5 0 001.3 2.2h16.2a1.5 1.5 0 001.3-2.2L13.7 3.6a1.5 1.5 0 00-2.6 0z" strokeLinejoin="round" />
    </svg>
  );
}

function IconSpark({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden>
      <path d="M12 3l1.8 5.5h5.7l-4.6 3.4 1.8 5.5L12 15.5l-4.7 3.4 1.8-5.5-4.6-3.4h5.7L12 3z" strokeLinejoin="round" />
    </svg>
  );
}

function ToneArrow({ tone }: { tone: "up" | "down" | "flat" }) {
  const cls = "landing-verdict-tone-ico";
  if (tone === "up") return <IconArrowUp className={cls} />;
  if (tone === "down") return <IconArrowDown className={cls} />;
  return <IconDash className={cls} />;
}

/** TrendPulse / chart interval label, e.g. 5minute → 5m */
export function formatTpInterval(interval: string | undefined): string {
  return (interval ?? "5minute").replace("minute", "m");
}

/** NIFTY session move (Up/Down/Sideways) → Bullish/Bearish/Sideways */
export function sessionTrendBias(intradayLabel: string | undefined): "Bullish" | "Bearish" | "Sideways" {
  const s = (intradayLabel || "").trim();
  if (s === "Up") return "Bullish";
  if (s === "Down") return "Bearish";
  return "Sideways";
}

/** HTF bias string → Bullish/Bearish/Sideways (flat → Sideways) */
export function htfTrendBias(bias: string | null | undefined): "Bullish" | "Bearish" | "Sideways" {
  const b = (bias || "").toLowerCase();
  if (b === "bullish") return "Bullish";
  if (b === "bearish") return "Bearish";
  return "Sideways";
}

function biasToArrowTone(b: "Bullish" | "Bearish" | "Sideways"): "up" | "down" | "flat" {
  if (b === "Bullish") return "up";
  if (b === "Bearish") return "down";
  return "flat";
}

function clamp(n: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, n));
}

function intradayScore(label: string | undefined): number {
  const s = (label || "").trim();
  if (s === "Up") return 0.55;
  if (s === "Down") return -0.55;
  return 0;
}

function htfScore(bias: string | null | undefined): number {
  const b = (bias || "").toLowerCase();
  if (b === "bullish") return 0.5;
  if (b === "bearish") return -0.5;
  return 0;
}

function storyVariant(text: string, index: number): "flow" | "news" | "trend" | "caution" {
  const t = text.toLowerCase();
  if (t.includes("disagree")) return "caution";
  if (t.includes("headline")) return "news";
  if (t.includes("trend context")) return "trend";
  if (t.includes("option flow")) return "flow";
  return (["flow", "news", "trend"] as const)[index % 3];
}

function labelClassNews(lab: string) {
  const u = lab.toUpperCase();
  if (u === "POSITIVE") return "pos";
  if (u === "NEGATIVE") return "neg";
  return "";
}

/** −100…+100 option-flow direction meter */
function FlowDirectionMeter({
  score,
  directionLabel,
  sentimentLabel,
  loading,
}: {
  score: number | null;
  directionLabel: string | null;
  sentimentLabel: string | null;
  loading: boolean;
}) {
  const pct = score == null ? 50 : Math.min(100, Math.max(0, ((score + 100) / 200) * 100));
  const signed =
    score == null ? "—" : score === 0 ? "0" : score > 0 ? `+${score.toFixed(0)}` : score.toFixed(0);

  return (
    <div className="landing-pulse-meter landing-pulse-meter--flow">
      <div className="landing-pulse-meter-head">
        <span className="landing-pulse-meter-ico" aria-hidden>
          <IconFlow className="landing-pulse-meter-ico-svg" />
        </span>
        <span className="landing-pulse-meter-title">Option flow</span>
      </div>
      <p className="landing-pulse-meter-lead">
        PCR, OI, momentum → one <strong>direction score</strong> on a fixed scale (not rupees, not % return).
      </p>
      <div className="landing-pulse-scale" aria-hidden>
        <div className="landing-pulse-scale-track landing-pulse-scale-track--flow">
          <span className="landing-pulse-scale-gradient" />
          <span className="landing-pulse-scale-tick landing-pulse-scale-tick--mid" />
          <span className="landing-pulse-scale-marker" style={{ left: `${pct}%` }} />
        </div>
        <div className="landing-pulse-scale-labels">
          <span>−100 bearish</span>
          <span>0 neutral</span>
          <span>+100 bullish</span>
        </div>
      </div>
      <div className="landing-pulse-meter-readout">
        {loading ? (
          <span className="muted">…</span>
        ) : (
          <>
            <span className="landing-pulse-readout-num">{signed}</span>
            <span className="landing-pulse-readout-sep">·</span>
            <span className="landing-pulse-readout-label">{directionLabel ?? "—"}</span>
          </>
        )}
      </div>
      {sentimentLabel ? <p className="landing-pulse-meter-sub muted">{sentimentLabel}</p> : null}
      <p className="landing-pulse-meter-foot">
        <strong>Why two words?</strong> The <em>number</em> is the fine dial. The <em>capital label</em> (BULLISH / NEUTRAL /
        BEARISH) is a wider band from model thresholds — you can be <strong>NEUTRAL</strong> with a score like{" "}
        <strong>+16</strong> because it has not crossed into the bullish band yet.
      </p>
    </div>
  );
}

/** −1…+1 headline tone index */
function NewsToneMeter({
  aggregateScore,
  aggregateLabel,
  headlineCount,
  loading,
}: {
  aggregateScore: number | null;
  aggregateLabel: string | null;
  headlineCount: number;
  loading: boolean;
}) {
  const pct =
    aggregateScore == null ? 50 : Math.min(100, Math.max(0, ((aggregateScore + 1) / 2) * 100));
  const signed =
    aggregateScore == null
      ? "—"
      : aggregateScore > 0
        ? `+${aggregateScore.toFixed(2)}`
        : aggregateScore.toFixed(2);

  return (
    <div className="landing-pulse-meter landing-pulse-meter--news">
      <div className="landing-pulse-meter-head">
        <span className="landing-pulse-meter-ico" aria-hidden>
          <IconNews className="landing-pulse-meter-ico-svg" />
        </span>
        <span className="landing-pulse-meter-title">News pulse</span>
      </div>
      <p className="landing-pulse-meter-lead">
        RSS headlines scored with a simple word list → <strong>tone index</strong> between <strong>−1</strong> and{" "}
        <strong>+1</strong>.
      </p>
      <div className="landing-pulse-scale" aria-hidden>
        <div className="landing-pulse-scale-track landing-pulse-scale-track--news">
          <span className="landing-pulse-scale-gradient landing-pulse-scale-gradient--news" />
          <span className="landing-pulse-scale-tick landing-pulse-scale-tick--mid" />
          <span className="landing-pulse-scale-marker landing-pulse-scale-marker--news" style={{ left: `${pct}%` }} />
        </div>
        <div className="landing-pulse-scale-labels">
          <span>−1.0 negative</span>
          <span>0 flat</span>
          <span>+1.0 positive</span>
        </div>
      </div>
      <div className="landing-pulse-meter-readout">
        {loading ? (
          <span className="muted">…</span>
        ) : headlineCount === 0 ? (
          <span className="muted">No headlines</span>
        ) : (
          <>
            <span className="landing-pulse-readout-num">{signed}</span>
            <span className="landing-pulse-readout-sep">·</span>
            <span className="landing-pulse-readout-label">{aggregateLabel ?? "—"}</span>
          </>
        )}
      </div>
      <p className="landing-pulse-meter-foot">
        <strong>Not a percentage.</strong> <strong>+0.13</strong> means headlines are slightly more positive than negative
        by our lexicon — on the <strong>−1 … +1</strong> bar above, not “13% of something.”
      </p>
    </div>
  );
}

/** Combines option-flow model, RSS tone, and spot/HTF trend into a single readable bias. */
export function computeMarketVerdict(
  sentiment: SentimentPayload | null,
  snap: MarketSnapshot | null,
  tp: TrendPulsePayload | null,
  news: NewsSentimentPayload | null,
): MarketVerdict {
  const flow = clamp((sentiment?.directionScore ?? 0) / 100, -1, 1);
  const hasNews = Boolean(news && news.headlineCount > 0);
  const newsSig = hasNews ? clamp(news!.aggregateScore, -1, 1) : 0;

  const spot = intradayScore(snap?.intradayTrendLabel);
  const htf = tp?.trendpulseEnabled ? htfScore(tp.htfBias) : 0;
  const trend =
    tp?.trendpulseEnabled && (tp.htfBias === "bullish" || tp.htfBias === "bearish" || tp.htfBias === "flat")
      ? (spot + htf) / 2
      : spot;

  const fw = hasNews ? 0.4 : 0.52;
  const nw = hasNews ? 0.22 : 0;
  const tw = hasNews ? 0.38 : 0.48;
  const score = clamp(fw * flow + nw * newsSig + tw * trend, -1, 1);

  const flowSummary = sentiment
    ? `${sentiment.directionLabel} · score ${Number(sentiment.directionScore).toFixed(0)}`
    : "—";

  let flowTone: FlowTone = "neutral";
  if (sentiment) {
    const d = String(sentiment.directionLabel || "").toUpperCase();
    if (d === "BULLISH") flowTone = "bull";
    else if (d === "BEARISH") flowTone = "bear";
  }

  let newsSummary: string;
  let newsTone: NewsTone = "empty";
  if (!news) {
    newsSummary = "No RSS block";
    newsTone = "empty";
  } else if (!hasNews) {
    newsSummary = news.feedErrors?.length ? "RSS error / empty" : "No headlines";
    newsTone = news.feedErrors?.length ? "error" : "empty";
  } else {
    const lab = String(news.aggregateLabel || "").toUpperCase();
    if (lab === "POSITIVE") newsTone = "pos";
    else if (lab === "NEGATIVE") newsTone = "neg";
    else newsTone = "neutral";
    newsSummary = `${news.aggregateLabel} · ${news.aggregateScore.toFixed(2)}`;
  }

  let trendSummary: string;
  let spotTone: SpotTone = "flat";
  if (snap) {
    const sp = String(snap.intradayTrendLabel || "").trim();
    if (sp === "Up") spotTone = "up";
    else if (sp === "Down") spotTone = "down";
    else spotTone = "flat";
  }
  let htfTone: HtfTone = "off";
  if (!snap) trendSummary = "—";
  else if (tp?.trendpulseEnabled) {
    const h = (tp.htfInterval ?? "15minute").replace("minute", "m");
    const hb = String(tp.htfBias || "").toLowerCase();
    if (hb === "bullish") htfTone = "bull";
    else if (hb === "bearish") htfTone = "bear";
    else if (hb === "flat") htfTone = "flat";
    else htfTone = "flat";
    trendSummary = `${snap.intradayTrendLabel} spot · HTF ${h} ${tp.htfBias ?? "—"}`;
  } else {
    trendSummary = `${snap.intradayTrendLabel} spot (HTF off)`;
    htfTone = "off";
  }

  const newsOpposeFlow =
    hasNews &&
    Math.abs(flow) > 0.22 &&
    Math.abs(newsSig) > 0.12 &&
    Math.sign(flow) !== Math.sign(newsSig);

  let labelKey: MarketVerdict["labelKey"];
  let label: string;
  if (newsOpposeFlow && Math.abs(score) < 0.2) {
    labelKey = "mixed";
    label = "Mixed — news vs flow";
  } else if (score >= 0.18) {
    labelKey = "bullish";
    label = "Lean bullish";
  } else if (score <= -0.18) {
    labelKey = "bearish";
    label = "Lean bearish";
  } else if (Math.abs(score) < 0.065) {
    labelKey = "neutral";
    label = "Neutral / range";
  } else {
    labelKey = "mixed";
    label = "Tilted — low conviction";
  }

  const parts: string[] = [];
  if (Math.abs(flow) >= 0.15) parts.push(`Option flow ${flow >= 0 ? "supports upside" : "leans defensive"}.`);
  if (hasNews && Math.abs(newsSig) >= 0.08)
    parts.push(`Headlines read ${news!.aggregateLabel.toLowerCase()}.`);
  if (Math.abs(trend) >= 0.12)
    parts.push(`Trend context is ${trend >= 0 ? "firm to up" : "soft to down"}.`);
  if (newsOpposeFlow) parts.push("Headlines and option-flow disagree — treat as caution.");
  if (parts.length === 0) parts.push("Signals are flat; no strong directional stack.");

  return {
    label,
    labelKey,
    score,
    summary: parts.join(" "),
    summaryBullets: parts,
    flowSummary,
    newsSummary,
    trendSummary,
    caveat: "Informative blend only — not a trade signal. Confirm with your plan and risk limits.",
    pillars: {
      flow: { tone: flowTone, scoreNum: sentiment ? Number(sentiment.directionScore) : null },
      news: { tone: newsTone, scoreNum: hasNews && news ? Number(news.aggregateScore) : null },
      trend: { spot: spotTone, htf: htfTone },
    },
  };
}

function StoryBulletIcon({ variant }: { variant: "flow" | "news" | "trend" | "caution" }) {
  const cls = "landing-pulse-story-ico-svg";
  if (variant === "flow") return <IconFlow className={cls} />;
  if (variant === "news") return <IconNews className={cls} />;
  if (variant === "trend") return <IconTrend className={cls} />;
  return <IconAlert className={cls} />;
}

export default function MarketVerdictWidget({
  loading,
  sentiment,
  snap,
  tp,
  news,
}: {
  loading: boolean;
  sentiment: SentimentPayload | null;
  snap: MarketSnapshot | null;
  tp: TrendPulsePayload | null;
  news: NewsSentimentPayload | null;
}) {
  const v = useMemo(
    () => computeMarketVerdict(sentiment, snap, tp, news),
    [sentiment, snap, tp, news],
  );

  const pillClass =
    v.labelKey === "bullish"
      ? "landing-pulse-pill--bull"
      : v.labelKey === "bearish"
        ? "landing-pulse-pill--bear"
        : v.labelKey === "mixed"
          ? "landing-pulse-pill--mixed"
          : "landing-pulse-pill--neutral";

  const heroIconClass = "landing-pulse-hero-ico";
  const heroIcon =
    v.labelKey === "bullish" ? (
      <IconArrowUp className={`${heroIconClass} landing-pulse-hero-ico--bull`} />
    ) : v.labelKey === "bearish" ? (
      <IconArrowDown className={`${heroIconClass} landing-pulse-hero-ico--bear`} />
    ) : v.labelKey === "mixed" ? (
      <IconAlert className={`${heroIconClass} landing-pulse-hero-ico--mixed`} />
    ) : (
      <IconScale className={`${heroIconClass} landing-pulse-hero-ico--neutral`} />
    );

  const { pillars: p } = v;

  const items: NewsSentimentItem[] = news?.items ?? [];
  const errs = news?.feedErrors ?? [];
  const updated = news?.updatedAt;
  const newestPub = newestHeadlinePublishedAt(items);
  const lab = news?.aggregateLabel ?? "—";

  const sessionBiasLabel = loading && !snap ? null : sessionTrendBias(snap?.intradayTrendLabel);
  const htfBiasLabelResolved = tp?.trendpulseEnabled ? htfTrendBias(tp.htfBias) : null;

  return (
    <section
      className="landing-market-pulse landing-widget-help-host"
      aria-label="Market pulse, verdict, and news headlines"
    >
      <LandingWidgetHelp
        meaning="Combined view: option-flow direction score (−100…+100), RSS headline tone (−1…+1), NIFTY session + HTF trend, latest headlines, and a plain-language verdict."
        usage="Numbers are indices on fixed scales, not P&L %. The verdict blends the three pillars for context only — not an order."
      />

      <div className="landing-pulse-ribbon" aria-hidden />

      <header className="landing-pulse-top">
        <div className="landing-pulse-hero">
          <div className="landing-pulse-hero-ring" aria-hidden>
            {loading && !sentiment && !snap ? <span className="landing-pulse-dots">…</span> : heroIcon}
          </div>
          <div className="landing-pulse-hero-text">
            <div className="landing-pulse-eyebrow">
              <IconSpark className="landing-pulse-eyebrow-ico" aria-hidden />
              Live blend
            </div>
            <h2 className="landing-pulse-title">Market pulse & verdict</h2>
            <div className={`landing-pulse-pill ${pillClass}`} role="status">
              {loading && !sentiment && !snap ? "…" : v.label}
            </div>
          </div>
        </div>
      </header>

      <div className="landing-pulse-meters" role="group" aria-label="Scaled inputs">
        <FlowDirectionMeter
          score={p.flow.scoreNum}
          directionLabel={sentiment?.directionLabel ?? null}
          sentimentLabel={sentiment?.sentimentLabel ?? null}
          loading={loading && !sentiment}
        />
        <NewsToneMeter
          aggregateScore={p.news.scoreNum}
          aggregateLabel={news?.aggregateLabel ?? null}
          headlineCount={news?.headlineCount ?? 0}
          loading={loading && !news}
        />
        <div className="landing-pulse-meter landing-pulse-meter--trend" title={v.trendSummary}>
          <div className="landing-pulse-meter-head">
            <span className="landing-pulse-meter-ico" aria-hidden>
              <IconTrend className="landing-pulse-meter-ico-svg" />
            </span>
            <span className="landing-pulse-meter-title">Trend</span>
          </div>
          <p className="landing-pulse-meter-lead">
            Each row is a <strong>timeframe</strong> plus <strong>Bullish / Bearish / Sideways</strong> (session from NIFTY
            day % change; HTF from TrendPulse when on; signal = chart interval).
          </p>
          <div className="landing-pulse-trend-rows">
            <div className="landing-pulse-trend-row">
              <div className="landing-pulse-trend-tf">
                <span className="landing-pulse-trend-tf-label">Session</span>
                <span className="landing-pulse-trend-tf-hint muted">live NIFTY %</span>
              </div>
              <span
                className={
                  sessionBiasLabel == null
                    ? "landing-pulse-trend-bias landing-pulse-trend-bias--loading muted"
                    : `landing-pulse-trend-bias landing-pulse-trend-bias--${sessionBiasLabel.toLowerCase()}`
                }
              >
                {sessionBiasLabel == null ? (
                  "…"
                ) : (
                  <>
                    <ToneArrow tone={biasToArrowTone(sessionBiasLabel)} />
                    {sessionBiasLabel}
                  </>
                )}
              </span>
            </div>
            {tp?.trendpulseEnabled && htfBiasLabelResolved ? (
              <div className="landing-pulse-trend-row">
                <div className="landing-pulse-trend-tf">
                  <span className="landing-pulse-trend-tf-label">HTF {formatTpInterval(tp.htfInterval)}</span>
                  <span className="landing-pulse-trend-tf-hint muted">TrendPulse bias</span>
                </div>
                <span
                  className={`landing-pulse-trend-bias landing-pulse-trend-bias--${htfBiasLabelResolved.toLowerCase()}`}
                >
                  <ToneArrow tone={biasToArrowTone(htfBiasLabelResolved)} />
                  {htfBiasLabelResolved}
                </span>
              </div>
            ) : null}
            {tp?.trendpulseEnabled ? (
              <p className="landing-pulse-trend-signal muted" role="note">
                Signal chart timeframe: <strong>{formatTpInterval(tp.stInterval)}</strong> (PS/VS_z series)
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div className="landing-pulse-story">
        <h3 className="landing-pulse-story-title">
          <IconSpark className="landing-pulse-story-title-ico" aria-hidden />
          Why this verdict
        </h3>
        <div className="landing-pulse-story-grid">
          {(loading && !sentiment ? ["Loading context…"] : v.summaryBullets).map((text, i) => {
            const variant = storyVariant(text, i);
            return (
              <div key={`${i}-${text.slice(0, 12)}`} className={`landing-pulse-story-card landing-pulse-story-card--${variant}`}>
                <span className="landing-pulse-story-ico" aria-hidden>
                  <StoryBulletIcon variant={variant} />
                </span>
                <p className="landing-pulse-story-text">{text}</p>
              </div>
            );
          })}
        </div>
      </div>

      <div className="landing-pulse-split">
        <div className="landing-pulse-headlines">
          <div className="landing-pulse-headlines-head">
            <span className="landing-bento-title">Headlines</span>
            <div className="landing-pulse-headlines-badges">
              <span className={`landing-news-agg-pill ${labelClassNews(lab)}`}>{loading && !news ? "…" : lab}</span>
              <span className="landing-pulse-meta">{news?.methodologyVersion ?? ""}</span>
            </div>
          </div>
          {updated ? (
            <div className="landing-news-fetch-meta" role="group" aria-label="News fetch times">
              <p className="landing-news-fetch-line">
                <span className="landing-news-fetch-k">RSS fetched (IST)</span>
                <time className="landing-news-fetch-v" dateTime={updated}>
                  {formatTimeIST(updated, { seconds: true })}
                </time>
                {news?.cached ? (
                  <span className="landing-news-fetch-cache muted" title="Served from server cache until TTL">
                    · cached
                  </span>
                ) : null}
              </p>
              {newestPub ? (
                <p className="landing-news-fetch-line">
                  <span className="landing-news-fetch-k">Newest headline (pub.)</span>
                  <time className="landing-news-fetch-v" dateTime={newestPub}>
                    {formatTimeIST(newestPub, { seconds: true })}
                  </time>
                </p>
              ) : null}
            </div>
          ) : null}
          {errs.length > 0 ? (
            <p className="landing-news-errors muted" role="status">
              Some feeds failed: {errs[0]}
              {errs.length > 1 ? ` (+${errs.length - 1} more)` : ""}
            </p>
          ) : null}
          <ul className="landing-news-list landing-pulse-news-list" aria-label="Recent headlines">
            {loading && !news ? (
              <li className="muted">Loading headlines…</li>
            ) : !news && !loading ? (
              <li className="muted">News block missing from API response.</li>
            ) : items.length === 0 ? (
              <li className="muted">No headlines — check RSS or network.</li>
            ) : (
              items.map((it, idx) => (
                <li key={`${it.title}-${idx}`} className="landing-news-li">
                  <span className={`landing-news-item-chip ${labelClassNews(it.itemSentiment)}`}>{it.itemSentiment}</span>
                  {it.link ? (
                    <a href={it.link} target="_blank" rel="noopener noreferrer" className="landing-news-link">
                      {it.title}
                    </a>
                  ) : (
                    <span className="landing-news-title">{it.title}</span>
                  )}
                </li>
              ))
            )}
          </ul>
        </div>
        <aside className="landing-pulse-aside" aria-label="How this data is used">
          <h3 className="landing-pulse-aside-title">How this will be used</h3>
          <ul className="landing-pulse-aside-list">
            <li>
              <strong className="landing-pulse-aside-k">Today</strong>
              <span>Context on Home — compare flow, headlines, and trend before sizing ideas.</span>
            </li>
            <li>
              <strong className="landing-pulse-aside-k">Next</strong>
              <span>Caution tags when news and option-flow disagree strongly.</span>
            </li>
            <li>
              <strong className="landing-pulse-aside-k">Later</strong>
              <span>Optional gates or size caps from headline risk clusters (per strategy).</span>
            </li>
          </ul>
          <p className="landing-pulse-aside-foot muted">Does not place trades. Execution follows your rules and risk settings.</p>
        </aside>
      </div>

      <p className="landing-pulse-caveat">
        <IconScale className="landing-pulse-caveat-ico" aria-hidden />
        {v.caveat}
      </p>
    </section>
  );
}
