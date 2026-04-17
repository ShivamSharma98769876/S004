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

      <p className="landing-strategy-fit-disclaimer">{data.disclaimer}</p>
    </section>
  );
}
