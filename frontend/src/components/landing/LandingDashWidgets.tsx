"use client";

import { useId, useMemo } from "react";

/** Top-right ? on a `.landing-widget-help-host`; tooltip on hover over host or focus on ? */
export function LandingWidgetHelp({ meaning, usage }: { meaning: string; usage: string }) {
  return (
    <div className="landing-widget-help" role="presentation">
      <button type="button" className="landing-widget-help-trigger" aria-label="What this widget shows">
        ?
      </button>
      <div className="landing-widget-help-tooltip" role="tooltip">
        <p className="landing-widget-help-line">
          <span className="landing-widget-help-k">What it is</span>
          {meaning}
        </p>
        <p className="landing-widget-help-line">
          <span className="landing-widget-help-k">How to use</span>
          {usage}
        </p>
      </div>
    </div>
  );
}

/** Minimal sparkline — values in arbitrary range, auto-scaled. */
export function MiniSparkline({
  values,
  className = "",
  stroke = "var(--accent)",
  fillOpacity = 0.12,
}: {
  values: number[];
  className?: string;
  stroke?: string;
  fillOpacity?: number;
}) {
  const gid = useId().replace(/:/g, "");
  const { line, area } = useMemo(() => {
    if (values.length < 2) {
      return { line: "", area: "" };
    }
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const pad = (hi - lo) * 0.15 || 0.5;
    const a = lo - pad;
    const b = hi + pad;
    const span = Math.max(b - a, 1e-6);
    const n = values.length;
    const x = (i: number) => (i / (n - 1)) * 100;
    const y = (v: number) => 100 - ((v - a) / span) * 100;
    const pts = values.map((v, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(2)} ${y(v).toFixed(2)}`).join(" ");
    const areaD = `${pts} L 100 100 L 0 100 Z`;
    return { line: pts, area: areaD };
  }, [values]);

  if (values.length < 2) {
    return (
      <div className={`landing-spark-empty ${className}`} aria-hidden>
        <span className="landing-spark-placeholder-line" />
      </div>
    );
  }

  return (
    <svg className={`landing-spark-svg ${className}`} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
      <defs>
        <linearGradient id={`sparkFill-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity={fillOpacity + 0.15} />
          <stop offset="100%" stopColor={stroke} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#sparkFill-${gid})`} />
      <path d={line} fill="none" stroke={stroke} strokeWidth="2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Semi-circular gauge 0–100 for confidence. */
export function ConfidenceGauge({ value, loading, compact = false }: { value: number; loading: boolean; compact?: boolean }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  const angleDeg = (pct / 100) * 180 - 90;
  const rad = (angleDeg * Math.PI) / 180;
  const r = 36;
  const cx = 50;
  const cy = 52;
  const x2 = cx + r * Math.cos(rad);
  const y2 = cy + r * Math.sin(rad);
  const pathLen = 100;
  const dashFilled = (pct / 100) * pathLen;
  const gid = useId().replace(/:/g, "");

  return (
    <div
      className={`landing-gauge${compact ? " landing-gauge--compact" : ""}`}
      role="img"
      aria-label={`Confidence ${pct} percent`}
    >
      <svg viewBox="0 0 100 64" className="landing-gauge-svg" aria-hidden>
        <path
          d="M 14 52 A 36 36 0 0 1 86 52"
          fill="none"
          stroke="var(--border)"
          strokeWidth="8"
          strokeLinecap="round"
          pathLength={pathLen}
        />
        <path
          d="M 14 52 A 36 36 0 0 1 86 52"
          fill="none"
          stroke={`url(#landingGaugeGrad-${gid})`}
          strokeWidth="8"
          strokeLinecap="round"
          pathLength={pathLen}
          strokeDasharray={`${dashFilled} ${pathLen}`}
          className="landing-gauge-arc"
        />
        <line x1={cx} y1={cy} x2={x2} y2={y2} stroke="var(--text-strong)" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx={cx} cy={cy} r="4" fill="var(--surface)" stroke="var(--accent)" strokeWidth="1.5" />
        <defs>
          <linearGradient id={`landingGaugeGrad-${gid}`} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#22c55e" />
            <stop offset="50%" stopColor="#4f7cff" />
            <stop offset="100%" stopColor="#a78bfa" />
          </linearGradient>
        </defs>
      </svg>
      <div className="landing-gauge-value">{loading ? "—" : `${pct}%`}</div>
    </div>
  );
}

export function RegimeBadge({ regime, loading, compact = false }: { regime: string; loading: boolean; compact?: boolean }) {
  const r = (regime || "").toUpperCase();
  let icon = "◧";
  let tone = "landing-regime--chop";
  if (r.includes("TREND")) {
    icon = "↗";
    tone = "landing-regime--trend";
  } else if (r.includes("VOLATILE")) {
    icon = "⚡";
    tone = "landing-regime--vol";
  }
  return (
    <div className={`landing-regime-badge ${tone}${compact ? " landing-regime-badge--compact" : ""}`}>
      <span className="landing-regime-icon" aria-hidden>
        {loading ? "…" : icon}
      </span>
      <div className="landing-regime-text">
        {!compact && <span className="landing-regime-label">Regime</span>}
        <strong className="landing-regime-name">{loading ? "—" : regime || "—"}</strong>
      </div>
    </div>
  );
}

type Driver = { key: string; label: string; impact: number; direction: "bullish" | "bearish" };

/** Cumulative bridge: each segment extends from running balance by driver impact (scaled). */
export function CumulativeWaterfall({ drivers, dense = false }: { drivers: Driver[]; dense?: boolean }) {
  const model = useMemo(() => {
    if (!drivers.length) return null;
    const impacts = drivers.map((d) => d.impact);
    const total = impacts.reduce((a, b) => a + b, 0);
    const maxAbs = Math.max(20, ...impacts.map((x) => Math.abs(x)), Math.abs(total));
    let centerPct = 50;
    const bars = impacts.map((im, i) => {
      const wPct = (im / maxAbs) * 42;
      const left = wPct >= 0 ? centerPct : centerPct + wPct;
      const width = Math.abs(wPct);
      const row = { left, width, pos: im >= 0, im, key: drivers[i]!.key };
      centerPct += wPct;
      return row;
    });
    return { bars, endPct: centerPct, total, maxAbs };
  }, [drivers]);

  if (!model) {
    return <p className="muted landing-waterfall-empty">No driver data</p>;
  }

  const barRows = drivers.map((d) => {
    const w = Math.min(50, Math.abs(d.impact));
    const left = d.direction === "bullish" ? "50%" : `${50 - w}%`;
    return { ...d, w, left };
  });

  if (dense) {
    const bridgeViewH = Math.max(38, 6 + model.bars.length * 5.8 + 7);
    return (
      <div className="landing-waterfall-dense">
        <div className="landing-waterfall-dense-bridge-wrap">
          <svg
            viewBox={`0 0 100 ${bridgeViewH}`}
            className="landing-waterfall-dense-svg"
            preserveAspectRatio="none"
            aria-hidden
          >
            <line
              x1="50"
              y1="5"
              x2="50"
              y2={bridgeViewH - 4}
              stroke="var(--muted)"
              strokeOpacity="0.35"
              strokeWidth="0.35"
            />
            {model.bars.map((b, i) => (
              <rect
                key={b.key}
                x={b.left}
                y={6 + i * 5.8}
                width={Math.max(b.width, 0.55)}
                height="3.6"
                rx="0.4"
                fill={b.pos ? "rgba(43, 196, 138, 0.88)" : "rgba(255, 107, 107, 0.88)"}
              />
            ))}
            <circle cx={model.endPct} cy={bridgeViewH - 2.2} r="1.35" fill="var(--accent)" />
          </svg>
        </div>
        <div className="landing-waterfall-dense-net">
          <span className="landing-waterfall-dense-net-label">Net (drivers)</span>
          <span className={`landing-waterfall-dense-net-val ${model.total >= 0 ? "pos" : "neg"}`}>
            {model.total > 0 ? "+" : ""}
            {model.total.toFixed(1)}
          </span>
        </div>
        <ul className="landing-waterfall-dense-rows" aria-label="Driver contributions">
          {barRows.map((d) => (
            <li key={d.key} className="landing-wf-row">
              <span className={`landing-wf-row-dot ${d.direction === "bullish" ? "bull" : "bear"}`} title={d.label} />
              <span className="landing-wf-row-label" title={d.label}>
                {d.label}
              </span>
              <div className="landing-wf-row-track">
                <div className="landing-wf-row-mid" />
                <div
                  className={`landing-wf-row-bar ${d.direction === "bullish" ? "bull" : "bear"}`}
                  style={{ width: `${d.w}%`, left: d.left }}
                />
              </div>
              <span className={`landing-wf-row-val ${d.impact >= 0 ? "pos" : "neg"}`}>
                {d.impact > 0 ? "+" : ""}
                {d.impact.toFixed(1)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="landing-waterfall-bridge">
      <svg viewBox="0 0 100 32" className="landing-waterfall-bridge-svg" preserveAspectRatio="none" aria-label="Cumulative driver contribution">
        <line x1="50" y1="6" x2="50" y2="26" stroke="var(--muted)" strokeOpacity="0.4" strokeWidth="0.4" />
        {model.bars.map((b, i) => (
          <rect
            key={b.key}
            x={b.left}
            y={6 + i * 3.2}
            width={Math.max(b.width, 0.6)}
            height="2.4"
            rx="0.4"
            fill={b.pos ? "rgba(43, 196, 138, 0.85)" : "rgba(255, 107, 107, 0.85)"}
          />
        ))}
        <circle cx={model.endPct} cy="28" r="1.4" fill="var(--accent)" opacity="0.9" />
      </svg>
      <ul className="landing-waterfall-legend">
        {drivers.map((d) => (
          <li key={d.key}>
            <span className={`landing-wf-dot ${d.direction === "bullish" ? "bull" : "bear"}`} />
            <span className="landing-wf-name">{d.label}</span>
            <span className={d.impact >= 0 ? "pos" : "neg"}>
              {d.impact > 0 ? "+" : ""}
              {d.impact.toFixed(1)}
            </span>
          </li>
        ))}
        <li className="landing-wf-total">
          <span className="landing-wf-name">Net (drivers)</span>
          <span className={model.total >= 0 ? "pos" : "neg"}>
            {model.total > 0 ? "+" : ""}
            {model.total.toFixed(1)}
          </span>
        </li>
      </ul>
    </div>
  );
}

export function DirectionChip({ label, score }: { label: string; score: number }) {
  const cls =
    label === "BULLISH" ? "landing-chip--bull" : label === "BEARISH" ? "landing-chip--bear" : "landing-chip--flat";
  return (
    <span className={`landing-dir-chip ${cls}`}>
      <span className="landing-dir-chip-label">{label}</span>
      <span className="landing-dir-chip-score">{Number.isFinite(score) ? score.toFixed(1) : "—"}</span>
    </span>
  );
}
