"use client";

import { useId } from "react";
import { LandingWidgetHelp } from "@/components/landing/LandingDashWidgets";

export type SidewaysRegimeCheck = {
  key: string;
  label: string;
  pass: boolean;
  reading: string;
};

export type SidewaysRegimePayload = {
  enabled: boolean;
  message: string | null;
  regimeLabel: string;
  score: number;
  maxScore: number;
  checks: SidewaysRegimeCheck[];
  metrics?: Record<string, unknown>;
  /** Candle interval used for structure (e.g. 30m). */
  timeframe?: string;
};

function displayRegimeLabel(label: string): string {
  if (label === "TRENDING_VOLATILE") return "Trending / volatile";
  return label || "—";
}

function num(m: Record<string, unknown> | undefined, k: string): number | null {
  if (!m) return null;
  const v = m[k];
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return null;
}

function AdxMicroBar({ adx }: { adx: number }) {
  const gid = useId().replace(/:/g, "");
  const cap = 45;
  const pct = Math.min(100, Math.max(0, (adx / cap) * 100));
  const z20 = (20 / cap) * 100;
  const z25 = (25 / cap) * 100;
  return (
    <div className="landing-sideways-micro landing-sideways-micro--adx">
      <div className="landing-sideways-adx-readout">
        <span className="landing-sideways-adx-readout-k">ADX (trend strength)</span>
        <strong className="landing-sideways-adx-readout-v">{adx.toFixed(1)}</strong>
      </div>
      <div className="landing-sideways-adx-bar" aria-hidden>
        <svg className="landing-sideways-adx-svg" viewBox="0 0 100 10" preserveAspectRatio="none">
          <defs>
            <linearGradient id={`adxg-${gid}`} x1="0" y1="0" x2="100" y2="0" gradientUnits="userSpaceOnUse">
              <stop offset="0%" stopColor="rgba(34, 197, 94, 0.35)" />
              <stop offset={`${z20}%`} stopColor="rgba(34, 197, 94, 0.35)" />
              <stop offset={`${z25}%`} stopColor="rgba(251, 191, 36, 0.28)" />
              <stop offset="100%" stopColor="rgba(248, 113, 113, 0.32)" />
            </linearGradient>
          </defs>
          <rect x="0" y="0" width="100" height="10" rx="3" fill={`url(#adxg-${gid})`} stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
          <line x1={z20} y1="0" x2={z20} y2="10" stroke="rgba(255,255,255,0.2)" strokeWidth="0.6" />
          <line x1={z25} y1="0" x2={z25} y2="10" stroke="rgba(255,255,255,0.2)" strokeWidth="0.6" />
        </svg>
        <span className="landing-sideways-adx-needle" style={{ left: `${pct}%` }} title={`ADX ${adx.toFixed(1)}`} />
      </div>
      <div className="landing-sideways-adx-scale muted" aria-hidden>
        <span>0</span>
        <span>20 chop</span>
        <span>25</span>
        <span>45+</span>
      </div>
    </div>
  );
}

function VwapDistMicroBar({ distPct }: { distPct: number }) {
  const w = Math.min(100, (Math.abs(distPct) / 1.25) * 100);
  const near = distPct <= 0.15;
  return (
    <div className="landing-sideways-micro landing-sideways-micro--vwap" aria-hidden>
      <div className="landing-sideways-vwap-track">
        <div
          className={`landing-sideways-vwap-fill landing-sideways-vwap-fill--from-left ${near ? "landing-sideways-vwap-fill--near" : ""}`}
          style={{ width: `${w}%` }}
        />
      </div>
      <span className="landing-sideways-vwap-cap muted">{distPct.toFixed(2)}% from VWAP · {near ? "near" : "away"}</span>
    </div>
  );
}

function ScoreSegments({ score, max }: { score: number; max: number }) {
  return (
    <div className="landing-sideways-segments" role="img" aria-label={`Score ${score} of ${max}`}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} className={`landing-sideways-seg ${i < score ? "landing-sideways-seg--on" : ""}`} />
      ))}
    </div>
  );
}

export default function SidewaysRegimePanel({
  data,
  loading,
}: {
  data: SidewaysRegimePayload | null;
  loading: boolean;
}) {
  const disabled = !data?.enabled;
  const regime = data?.regimeLabel ?? "—";
  const display = displayRegimeLabel(regime);
  const isSideways = regime === "SIDEWAYS";
  const tf = data?.timeframe ?? "30m";
  const metrics = data?.metrics as Record<string, unknown> | undefined;
  const adx = num(metrics, "adx");
  const vwapDist = num(metrics, "vwapDistPct");

  return (
    <section
      className="landing-sideways-regime landing-bento-cell panel-accent-chain landing-widget-help-host"
      aria-label="Sideways versus trending regime"
    >
      <LandingWidgetHelp
        meaning={`NIFTY ${tf} candles drive ADX, ATR, VWAP, and bar-range checks; CE/PE OI and VIX still come from the live option-chain snapshot vs your last poll.`}
        usage="Score 4/6 or more tilts range-bound on this timeframe; below that, read the session as more directional or volatile. Heavier structure than 5m — fewer whipsaws, slower to flip."
      />
      <header className="landing-sideways-top">
        <div className="landing-sideways-top-main">
          <div className="landing-sideways-title-row">
            <span className="landing-bento-title">Sideways vs trending</span>
            <span className="landing-sideways-tf-pill" title="Structure timeframe">
              {tf} structure
            </span>
          </div>
          <p className="landing-sideways-sub muted">NIFTY · session VWAP on {tf} bars · flow vs prior poll</p>
        </div>
        <span className="landing-bento-live" title="Uses live snapshot + your prior poll for OI / VIX deltas">
          <span className="landing-bento-live-dot" aria-hidden />
          Live
        </span>
      </header>

      {disabled ? (
        <div className="landing-sideways-empty muted">{data?.message ?? (loading ? "Loading…" : "Waiting for data…")}</div>
      ) : (
        <div className="landing-sideways-dashboard">
          <div className="landing-sideways-hero">
            <div
              className={`landing-sideways-verdict ${isSideways ? "landing-sideways-verdict--range" : "landing-sideways-verdict--trend"}`}
            >
              <div className="landing-sideways-verdict-row">
                <span className="landing-sideways-verdict-kicker">Regime</span>
                <span className="landing-sideways-verdict-chip muted">{tf}</span>
              </div>
              <strong className="landing-sideways-verdict-title">{loading ? "—" : display}</strong>
              <p className="landing-sideways-verdict-hint muted">Blended read — not a trade signal</p>
            </div>

            <div className="landing-sideways-score-block">
              <div className="landing-sideways-score-head">
                <span className="landing-sideways-score-label">Structure score</span>
                <span className="landing-sideways-score-value">
                  <em>{data?.score ?? 0}</em>
                  <span className="muted"> / {data?.maxScore ?? 6}</span>
                </span>
              </div>
              <ScoreSegments score={data?.score ?? 0} max={data?.maxScore ?? 6} />
              <p className="landing-sideways-score-foot muted">4+ green → range / chop bias on {tf}</p>
            </div>

            {(adx != null || vwapDist != null || num(metrics, "vix") != null || num(metrics, "lastClose") != null) && (
              <div className="landing-sideways-quick-wrap">
                <p className="landing-sideways-quick-caption muted">At a glance</p>
                <dl className="landing-sideways-quick landing-sideways-quick--above-chart">
                  {adx != null ? (
                    <div className="landing-sideways-quick-row">
                      <dt>ADX</dt>
                      <dd title={`Average Directional Index on ${tf} bars`}>{adx.toFixed(1)}</dd>
                    </div>
                  ) : null}
                  {num(metrics, "vix") != null ? (
                    <div className="landing-sideways-quick-row">
                      <dt>VIX</dt>
                      <dd>{num(metrics, "vix")!.toFixed(2)}</dd>
                    </div>
                  ) : null}
                  {vwapDist != null ? (
                    <div className="landing-sideways-quick-row">
                      <dt>vs VWAP</dt>
                      <dd>{vwapDist.toFixed(2)}%</dd>
                    </div>
                  ) : null}
                  {num(metrics, "lastClose") != null ? (
                    <div className="landing-sideways-quick-row">
                      <dt>Last close</dt>
                      <dd>{num(metrics, "lastClose")!.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</dd>
                    </div>
                  ) : null}
                </dl>
              </div>
            )}
          </div>

          <div className="landing-sideways-factors-wrap">
            <p className="landing-sideways-factors-eyebrow muted">Drivers</p>
            <ul className="landing-sideways-factors" aria-label="Structure checks">
              {(data?.checks ?? []).map((c) => (
                <li
                  key={c.key}
                  className={`landing-sideways-tile ${c.pass ? "landing-sideways-tile--pass" : "landing-sideways-tile--fail"}`}
                >
                  <div className="landing-sideways-tile-top">
                    <span className="landing-sideways-tile-icon" aria-hidden>
                      {c.pass ? "✓" : "✗"}
                    </span>
                    <span className="landing-sideways-tile-label">{c.label}</span>
                  </div>
                  {c.key === "adx" && adx != null ? <AdxMicroBar adx={adx} /> : null}
                  {c.key === "vwap" && vwapDist != null ? <VwapDistMicroBar distPct={vwapDist} /> : null}
                  <p className="landing-sideways-tile-reading muted">{c.reading}</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
