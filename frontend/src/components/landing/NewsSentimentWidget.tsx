"use client";

import { LandingWidgetHelp } from "@/components/landing/LandingDashWidgets";
import { formatTimeIST } from "@/lib/datetime_ist";

export type NewsSentimentItem = {
  title: string;
  link: string | null;
  publishedAt: string | null;
  itemSentiment: string;
  itemScore: number;
};

export type NewsSentimentPayload = {
  aggregateLabel: string;
  aggregateScore: number;
  headlineCount: number;
  items: NewsSentimentItem[];
  feedsQueried: string[];
  feedErrors: string[];
  cached: boolean;
  cacheTtlSec: number;
  methodologyVersion: string;
  updatedAt: string;
};

function labelClass(lab: string) {
  const u = lab.toUpperCase();
  if (u === "POSITIVE") return "pos";
  if (u === "NEGATIVE") return "neg";
  return "";
}

/** Latest item `publishedAt` by wall time (headline stamp from the feed), if any. */
export function newestHeadlinePublishedAt(items: NewsSentimentItem[]): string | null {
  let bestMs: number | null = null;
  let bestIso: string | null = null;
  for (const it of items) {
    if (!it.publishedAt) continue;
    const ms = Date.parse(it.publishedAt);
    if (!Number.isFinite(ms)) continue;
    if (bestMs === null || ms > bestMs) {
      bestMs = ms;
      bestIso = it.publishedAt;
    }
  }
  return bestIso;
}

export default function NewsSentimentWidget({
  data,
  loading,
}: {
  data: NewsSentimentPayload | null;
  loading: boolean;
}) {
  const lab = data?.aggregateLabel ?? "—";
  const score = data?.aggregateScore ?? 0;
  const items = data?.items ?? [];
  const errs = data?.feedErrors ?? [];
  const updated = data?.updatedAt;
  const newestPub = newestHeadlinePublishedAt(items);

  return (
    <section
      className="landing-news-context panel-accent-signals landing-widget-help-host"
      aria-label="News headline sentiment"
    >
      <LandingWidgetHelp
        meaning="Headlines from configured market RSS feeds, scored with a lightweight lexicon (positive / negative / neutral words)."
        usage="Overlay on options flow and TrendPulse — not a trade trigger. Planned use: sizing bias, caution tags on recommendations, and future gates when wired to execution."
      />
      <div className="landing-news-context-grid">
        <div className="landing-news-context-main">
          <header className="landing-news-context-head">
            <div>
              <span className="landing-bento-title">News pulse</span>
              <p className="landing-bento-sub landing-news-context-sub">
                Headline tone from RSS · {data?.methodologyVersion ?? "—"} · refreshes ~{data?.cacheTtlSec ?? "—"}s cache
              </p>
            </div>
            <div className="landing-news-context-badges">
              <span className={`landing-news-agg-pill ${labelClass(lab)}`}>
                {loading && !data ? "…" : lab}
              </span>
              <span className="landing-news-score" title="Average lexicon signal across headlines">
                {loading && !data ? "—" : score.toFixed(2)}
              </span>
            </div>
          </header>

          {updated ? (
            <div className="landing-news-fetch-meta" role="group" aria-label="News fetch times">
              <p className="landing-news-fetch-line">
                <span className="landing-news-fetch-k">RSS fetched (IST)</span>
                <time className="landing-news-fetch-v" dateTime={updated}>
                  {formatTimeIST(updated, { seconds: true })}
                </time>
                {data?.cached ? (
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
              Some feeds failed (showing what we could load): {errs[0]}
              {errs.length > 1 ? ` (+${errs.length - 1} more)` : ""}
            </p>
          ) : null}

          <ul className="landing-news-list" aria-label="Recent headlines">
            {loading && !data ? (
              <li className="muted">Loading headlines…</li>
            ) : !data && !loading ? (
              <li className="muted">
                News block did not load (missing <code className="landing-news-code">newsSentiment</code> on the API).
                Restart the backend with the latest code, or check the browser network tab for{" "}
                <code className="landing-news-code">/api/landing/decision-snapshot</code>.
              </li>
            ) : items.length === 0 ? (
              <li className="muted">
                No headlines parsed — outbound RSS may be blocked from your server. Set{" "}
                <code className="landing-news-code">NEWS_RSS_URLS</code> to feeds you can reach, or see feed errors above.
              </li>
            ) : (
              items.map((it, idx) => (
                <li key={`${it.title}-${idx}`} className="landing-news-li">
                  <span className={`landing-news-item-chip ${labelClass(it.itemSentiment)}`}>{it.itemSentiment}</span>
                  {it.link ? (
                    <a
                      href={it.link}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="landing-news-link"
                    >
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

        <aside className="landing-news-usage" aria-label="How news sentiment is used">
          <h3 className="landing-news-usage-title">How this will be used</h3>
          <ul className="landing-news-usage-list">
            <li>
              <strong>Today:</strong> context only on Home — compare headline tone with option-flow sentiment and TrendPulse
              bias.
            </li>
            <li>
              <strong>Next:</strong> tag recommendations when news strongly conflicts with model direction (caution, not
              auto-flip).
            </li>
            <li>
              <strong>Later:</strong> optional gates or size caps when headline risk clusters (e.g. shock / downgrade
              clusters) — configurable per strategy.
            </li>
          </ul>
          <p className="landing-news-usage-foot muted">
            Does not place trades by itself. Execution still follows your strategy rules and risk settings.
          </p>
        </aside>
      </div>
    </section>
  );
}
