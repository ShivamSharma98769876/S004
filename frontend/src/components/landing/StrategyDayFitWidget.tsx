"use client";

import Link from "next/link";

export type StrategyFitPick = {
  strategyId: string;
  version: string;
  displayName: string;
  riskProfile: string;
  strategyKind: string;
  score: number;
  reasons: string[];
};

export type StrategyDayFitAccuracyRow = {
  fitDate: string;
  buyerStrategyId: string | null;
  sellerStrategyId: string | null;
  buyerBeatMedian: boolean | null;
  sellerBeatMedian: boolean | null;
  buyerAggPnl: number | null;
  sellerAggPnl: number | null;
  buyerBucketMedianPnl: number | null;
  sellerBucketMedianPnl: number | null;
};

export type StrategyDayFitPayload = {
  available: boolean;
  fitDate: string;
  fromHistory?: boolean;
  disclaimer: string;
  error?: string;
  buyerPick?: StrategyFitPick | null;
  sellerPick?: StrategyFitPick | null;
  buyerRunnersUp?: StrategyFitPick[];
  sellerRunnersUp?: StrategyFitPick[];
  buyerTopScore?: number | null;
  sellerTopScore?: number | null;
  marketFingerprint?: Record<string, unknown>;
  accuracyRecent?: StrategyDayFitAccuracyRow[];
  accuracySummary?: {
    buyerBeatMedianDays: number;
    buyerScoredDays: number;
    sellerBeatMedianDays: number;
    sellerScoredDays: number;
  };
};

function fmtPnl(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function HitDot({ hit }: { hit: boolean | null | undefined }) {
  if (hit === true) return <span className="landing-fit-hit landing-fit-hit--yes" title="Beat bucket median" />;
  if (hit === false) return <span className="landing-fit-hit landing-fit-hit--no" title="Below bucket median" />;
  return <span className="landing-fit-hit landing-fit-hit--na" title="Insufficient closed trades" />;
}

export default function StrategyDayFitWidget({
  data,
  loading,
}: {
  data: StrategyDayFitPayload | null;
  loading: boolean;
}) {
  if (loading && !data) {
    return (
      <section className="landing-strategy-fit panel-accent-signals" aria-busy aria-label="Strategy fit">
        <header className="landing-strategy-fit-head">
          <h2 className="landing-strategy-fit-title">Today&apos;s strategy fit</h2>
        </header>
        <p className="muted">Loading picks…</p>
      </section>
    );
  }

  if (!data || data.available === false) {
    return (
      <section className="landing-strategy-fit landing-strategy-fit--muted panel-accent-signals" aria-label="Strategy fit">
        <header className="landing-strategy-fit-head">
          <h2 className="landing-strategy-fit-title">Today&apos;s strategy fit</h2>
        </header>
        <p className="muted">{data?.error ?? data?.disclaimer ?? "Strategy suggestions unavailable."}</p>
      </section>
    );
  }

  const acc = data.accuracySummary;
  const recent = data.accuracyRecent ?? [];

  return (
    <section className="landing-strategy-fit panel-accent-signals" aria-label="Market-strategy fit suggestions">
      <header className="landing-strategy-fit-head">
        <div>
          <h2 className="landing-strategy-fit-title">Today&apos;s strategy fit</h2>
          <p className="landing-strategy-fit-sub">
            Option buyer vs seller picks from live sentiment + regime · one snapshot per UTC day · outcomes from subscriber
            exits
          </p>
        </div>
        <div className="landing-strategy-fit-actions">
          <Link href="/marketplace" className="landing-strategy-fit-link">
            Marketplace →
          </Link>
          {data.fromHistory ? (
            <span className="landing-strategy-fit-pill" title="First landing load today locked ranks for consistency">
              Daily lock
            </span>
          ) : null}
        </div>
      </header>

      <div className="landing-strategy-fit-grid">
        <article className="landing-strategy-fit-card landing-strategy-fit-card--buyer">
          <div className="landing-strategy-fit-card-label">Best for option buyers</div>
          {data.buyerPick ? (
            <>
              <h3 className="landing-strategy-fit-name">{data.buyerPick.displayName}</h3>
              <div className="landing-strategy-fit-meta">
                <span className="landing-strategy-fit-score">{data.buyerPick.score}% match</span>
                <span className="landing-strategy-fit-risk">{data.buyerPick.riskProfile} risk</span>
              </div>
              <ul className="landing-strategy-fit-reasons">
                {data.buyerPick.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">No long-premium strategies published.</p>
          )}
          {(data.buyerRunnersUp?.length ?? 0) > 0 && (
            <div className="landing-strategy-fit-runners">
              <span className="landing-strategy-fit-runners-label">Also consider</span>
              <ul>
                {data.buyerRunnersUp!.map((p) => (
                  <li key={`${p.strategyId}-${p.version}`}>
                    {p.displayName}{" "}
                    <span className="landing-strategy-fit-runners-score">{p.score}%</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </article>

        <article className="landing-strategy-fit-card landing-strategy-fit-card--seller">
          <div className="landing-strategy-fit-card-label">Best for option sellers</div>
          {data.sellerPick ? (
            <>
              <h3 className="landing-strategy-fit-name">{data.sellerPick.displayName}</h3>
              <div className="landing-strategy-fit-meta">
                <span className="landing-strategy-fit-score">{data.sellerPick.score}% match</span>
                <span className="landing-strategy-fit-risk">{data.sellerPick.riskProfile} risk</span>
              </div>
              <ul className="landing-strategy-fit-reasons">
                {data.sellerPick.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">No short-premium strategies published.</p>
          )}
          {(data.sellerRunnersUp?.length ?? 0) > 0 && (
            <div className="landing-strategy-fit-runners">
              <span className="landing-strategy-fit-runners-label">Also consider</span>
              <ul>
                {data.sellerRunnersUp!.map((p) => (
                  <li key={`${p.strategyId}-${p.version}`}>
                    {p.displayName}{" "}
                    <span className="landing-strategy-fit-runners-score">{p.score}%</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </article>
      </div>

      <div className="landing-strategy-fit-accuracy">
        <div className="landing-strategy-fit-accuracy-head">
          <span className="landing-strategy-fit-accuracy-title">Prediction check</span>
          <span className="landing-strategy-fit-accuracy-hint">
            Prior UTC days: pick&apos;s realized PnL vs median of same bucket (all users, EXIT trades)
          </span>
        </div>
        {acc && (acc.buyerScoredDays > 0 || acc.sellerScoredDays > 0) ? (
          <p className="landing-strategy-fit-accuracy-summary">
            Buyers beat median on{" "}
            <strong>
              {acc.buyerBeatMedianDays}/{acc.buyerScoredDays}
            </strong>{" "}
            scored days · Sellers{" "}
            <strong>
              {acc.sellerBeatMedianDays}/{acc.sellerScoredDays}
            </strong>
          </p>
        ) : (
          <p className="muted landing-strategy-fit-accuracy-summary">Building history — need completed trade days after first picks.</p>
        )}
        {recent.length > 0 ? (
          <div className="landing-strategy-fit-accuracy-table-wrap">
            <table className="landing-strategy-fit-accuracy-table">
              <thead>
                <tr>
                  <th>Day (UTC)</th>
                  <th>Buyer PnL</th>
                  <th title="vs median of long-premium bucket">Δ</th>
                  <th>Seller PnL</th>
                  <th title="vs median of short-premium bucket">Δ</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((row) => (
                  <tr key={row.fitDate}>
                    <td>{row.fitDate}</td>
                    <td>{fmtPnl(row.buyerAggPnl)}</td>
                    <td>
                      <HitDot hit={row.buyerBeatMedian} />
                    </td>
                    <td>{fmtPnl(row.sellerAggPnl)}</td>
                    <td>
                      <HitDot hit={row.sellerBeatMedian} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>

      <p className="landing-strategy-fit-disclaimer">{data.disclaimer}</p>
    </section>
  );
}
