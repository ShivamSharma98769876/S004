"""
Daily strategy ↔ market regime fit for landing: rank long-premium vs short-premium catalog strategies.

Persists one row per UTC day (first snapshot wins). Backfills aggregate realized PnL vs bucket median
from s004_live_trades (EXIT) for accuracy tracking.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from app.db_client import execute, fetch, fetchrow

_logger = logging.getLogger("s004.strategy_day_fit")


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_details(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}
    return {}


def position_intent(details: dict[str, Any]) -> str:
    pi = str(details.get("positionIntent") or "long_premium").strip().lower()
    return "short_premium" if pi == "short_premium" else "long_premium"


def strategy_kind(details: dict[str, Any]) -> str:
    st = str(details.get("strategyType") or "rule-based").strip().lower()
    if st == "trendpulse-z":
        return "trendpulse_z"
    if st == "heuristic-voting":
        return "heuristic_voting"
    return "rule_based"


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (float(s[mid - 1]) + float(s[mid])) / 2.0


def score_long_premium_row(
    meta: dict[str, Any],
    sentiment: dict[str, Any],
    tp: dict[str, Any],
) -> tuple[float, list[str]]:
    details = meta["details"]
    kind = strategy_kind(details)
    direction = str(sentiment.get("directionLabel") or "NEUTRAL").upper()
    regime = str(sentiment.get("regime") or "RANGE_CHOP")
    conf = float(sentiment.get("confidence") or 0)
    dir_score = float(sentiment.get("directionScore") or 0)
    risk = str(meta.get("risk_profile") or "MEDIUM").upper()

    score = 42.0
    reasons: list[str] = []

    if regime == "TRENDING":
        score += 16
        reasons.append("Trending tape favors directional long-premium setups")
    elif regime == "VOLATILE_EVENT":
        score += 8
        reasons.append("Event-style vol: long gamma can work — keep risk tight")
    else:
        score += 5
        reasons.append("Choppy regime: size down; prefer broad multi-signal entries")

    if conf >= 58:
        score += min(14.0, (conf - 50) * 0.22)
        reasons.append(f"Flow conviction ~{int(conf)}% — clearer skew in the book")

    if kind == "trendpulse_z":
        ts = (tp or {}).get("tradeSignal") or {}
        if isinstance(ts, dict) and ts.get("entryEligible"):
            score += 24
            reasons.append("TrendPulse Z is live — playbook matches today’s tape")
        htf = str((tp or {}).get("htfBias") or "").lower()
        if direction == "BULLISH" and htf == "bullish":
            score += 10
            reasons.append("Higher-timeframe bias lines up with bullish flow")
        if direction == "BEARISH" and htf == "bearish":
            score += 10
            reasons.append("Higher-timeframe bias lines up with bearish flow")
        if direction == "NEUTRAL" and not (isinstance(ts, dict) and ts.get("entryEligible")):
            reasons.append("Wait for PS/VS cross + HTF alignment for TrendPulse entries")
    elif kind == "heuristic_voting":
        score += 12
        if regime == "RANGE_CHOP":
            score += 10
            reasons.append("Heuristic blend copes well when single-factor rules whipsaw")
        else:
            reasons.append("Weighted heuristics still rank liquid strikes in trends")
    else:
        score += 10
        if regime == "TRENDING" and direction != "NEUTRAL":
            score += min(14.0, abs(dir_score) / 7.0)
            reasons.append("Momentum rules gain edge when spot trend agrees with flow")
        else:
            reasons.append("Rule-based momentum path fits moderate trend / mixed days")

    if risk == "HIGH" and regime == "RANGE_CHOP":
        score -= 6
        reasons.append("Higher catalog risk — reduce size when ADX is weak")

    score = max(0.0, min(100.0, score))
    # unique reasons, cap 4
    out_r: list[str] = []
    for r in reasons:
        if r not in out_r:
            out_r.append(r)
    return score, out_r[:4]


def score_short_premium_row(
    meta: dict[str, Any],
    sentiment: dict[str, Any],
    tp: dict[str, Any],
) -> tuple[float, list[str]]:
    del tp  # reserved for future IV / trendpulse hooks
    direction = str(sentiment.get("directionLabel") or "NEUTRAL").upper()
    regime = str(sentiment.get("regime") or "RANGE_CHOP")
    conf = float(sentiment.get("confidence") or 0)

    score = 36.0
    reasons: list[str] = []

    if regime == "RANGE_CHOP":
        score += 26
        reasons.append("Range / chop is the classic short-premium environment")
    elif regime == "TRENDING":
        score += 14
        reasons.append("Trend + IV edge can favor directional naked shorts (margin aware)")
    elif regime == "VOLATILE_EVENT":
        score -= 8
        reasons.append("Spike risk: naked shorts need wide risk — not a default lean")

    if direction in ("BULLISH", "BEARISH") and conf >= 52:
        score += 18
        reasons.append("Directional lean helps choose CE vs PE short legs")
    elif direction == "NEUTRAL":
        score += 6
        reasons.append("Neutral flow: only sell premium with clear IV / strike edge")

    if conf >= 68:
        score += 8

    if conf >= 82 and regime == "VOLATILE_EVENT":
        score -= 10
        reasons.append("Very high conviction + vol event: avoid heroic short size")

    score = max(0.0, min(100.0, score))
    out_r: list[str] = []
    for r in reasons:
        if r not in out_r:
            out_r.append(r)
    return score, out_r[:4]


def _market_fingerprint(sentiment: dict[str, Any], tp: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    ts = (tp or {}).get("tradeSignal") if isinstance(tp, dict) else None
    elig = bool(isinstance(ts, dict) and ts.get("entryEligible"))
    return {
        "directionLabel": sentiment.get("directionLabel"),
        "directionScore": sentiment.get("directionScore"),
        "confidence": sentiment.get("confidence"),
        "regime": sentiment.get("regime"),
        "spotChangePct": (market.get("nifty") or {}).get("changePct"),
        "pcr": market.get("pcr"),
        "trendpulseEntryEligible": elig,
        "htfBias": (tp or {}).get("htfBias") if isinstance(tp, dict) else None,
        "updatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _rank_catalog_rows(
    rows: list[dict[str, Any]],
    sentiment: dict[str, Any],
    tp: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    longs: list[dict[str, Any]] = []
    shorts: list[dict[str, Any]] = []
    for r in rows:
        details = r["details"]
        intent = position_intent(details)
        meta = {
            "strategy_id": r["strategy_id"],
            "version": r["version"],
            "display_name": r["display_name"],
            "description": r.get("description") or "",
            "risk_profile": r.get("risk_profile") or "MEDIUM",
            "details": details,
        }
        if intent == "short_premium":
            sc, reasons = score_short_premium_row(meta, sentiment, tp)
            kind = strategy_kind(details)
            shorts.append(
                {
                    "strategyId": meta["strategy_id"],
                    "version": meta["version"],
                    "displayName": meta["display_name"],
                    "riskProfile": meta["risk_profile"],
                    "strategyKind": kind,
                    "score": round(sc, 1),
                    "reasons": reasons,
                }
            )
        else:
            sc, reasons = score_long_premium_row(meta, sentiment, tp)
            kind = strategy_kind(details)
            longs.append(
                {
                    "strategyId": meta["strategy_id"],
                    "version": meta["version"],
                    "displayName": meta["display_name"],
                    "riskProfile": meta["risk_profile"],
                    "strategyKind": kind,
                    "score": round(sc, 1),
                    "reasons": reasons,
                }
            )

    longs.sort(key=lambda x: (-x["score"], x["displayName"]))
    shorts.sort(key=lambda x: (-x["score"], x["displayName"]))
    return longs, shorts


async def fetch_published_catalog_rows() -> list[dict[str, Any]]:
    recs = await fetch(
        """
        SELECT strategy_id, version, display_name, description, risk_profile, strategy_details_json
        FROM s004_strategy_catalog
        WHERE publish_status = 'PUBLISHED'
        ORDER BY display_name ASC
        """
    )
    out: list[dict[str, Any]] = []
    for r in recs:
        out.append(
            {
                "strategy_id": str(r["strategy_id"]),
                "version": str(r["version"]),
                "display_name": str(r["display_name"]),
                "description": str(r["description"] or ""),
                "risk_profile": str(r["risk_profile"] or "MEDIUM"),
                "details": _parse_details(r.get("strategy_details_json")),
            }
        )
    return out


def build_fit_payload(
    catalog_rows: list[dict[str, Any]],
    sentiment: dict[str, Any],
    tp: dict[str, Any],
    market: dict[str, Any],
    *,
    fit_date: date,
    from_history: bool,
) -> dict[str, Any]:
    buyer, seller = _rank_catalog_rows(catalog_rows, sentiment, tp if isinstance(tp, dict) else {})
    fp = _market_fingerprint(sentiment, tp if isinstance(tp, dict) else {}, market)
    picks_json = {"buyer": buyer, "seller": seller, "fingerprint": fp}
    top_b = buyer[0] if buyer else None
    top_s = seller[0] if seller else None
    return {
        "available": bool(buyer or seller),
        "fitDate": fit_date.isoformat(),
        "fromHistory": from_history,
        "disclaimer": (
            "Heuristic fit only — not investment advice. "
            "Accuracy tracks realized PnL vs same-bucket strategies across all subscribers’ closed trades."
        ),
        "marketFingerprint": fp,
        "buyerPick": top_b,
        "sellerPick": top_s,
        "buyerRunnersUp": buyer[1:4],
        "sellerRunnersUp": seller[1:4],
        "picksJson": picks_json,
        "buyerTopScore": top_b["score"] if top_b else None,
        "sellerTopScore": top_s["score"] if top_s else None,
    }


async def _load_stored_fit(fit_date: date) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT fit_date, market_fingerprint, buyer_strategy_id, buyer_strategy_version, buyer_score,
               seller_strategy_id, seller_strategy_version, seller_score, picks_json,
               outcome_computed_at, buyer_agg_pnl, seller_agg_pnl,
               buyer_bucket_median_pnl, seller_bucket_median_pnl,
               buyer_beat_median, seller_beat_median
        FROM s004_landing_strategy_fit_daily
        WHERE fit_date = $1
        """,
        fit_date,
    )
    if row is None:
        return None
    pj = row["picks_json"]
    if isinstance(pj, str):
        try:
            pj = json.loads(pj)
        except json.JSONDecodeError:
            pj = {}
    buyer = pj.get("buyer") or []
    seller = pj.get("seller") or []
    top_b = buyer[0] if buyer else None
    top_s = seller[0] if seller else None
    return {
        "row": row,
        "picks_json": pj,
        "buyer": buyer,
        "seller": seller,
        "top_b": top_b,
        "top_s": top_s,
    }


async def _insert_daily_fit(
    fit_date: date,
    fingerprint: dict[str, Any],
    picks_json: dict[str, Any],
    top_b: dict[str, Any] | None,
    top_s: dict[str, Any] | None,
) -> str:
    """Returns asyncpg status (e.g. INSERT 0 1 or INSERT 0 0 if conflict)."""
    return await execute(
        """
        INSERT INTO s004_landing_strategy_fit_daily (
            fit_date, market_fingerprint, buyer_strategy_id, buyer_strategy_version, buyer_score,
            seller_strategy_id, seller_strategy_version, seller_score, picks_json
        ) VALUES (
            $1::date, $2::jsonb, $3, $4, $5, $6, $7, $8, $9::jsonb
        )
        ON CONFLICT (fit_date) DO NOTHING
        """,
        fit_date,
        json.dumps(fingerprint),
        top_b["strategyId"] if top_b else None,
        top_b["version"] if top_b else None,
        float(top_b["score"]) if top_b else None,
        top_s["strategyId"] if top_s else None,
        top_s["version"] if top_s else None,
        float(top_s["score"]) if top_s else None,
        json.dumps(picks_json),
    )


async def _aggregate_day_pnl(strategy_id: str, strategy_version: str, day: date) -> tuple[float, int]:
    row = await fetchrow(
        """
        SELECT COALESCE(SUM(realized_pnl), 0)::float AS pnl, COUNT(*)::int AS n
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at IS NOT NULL
          AND (closed_at AT TIME ZONE 'UTC')::date = $1::date
          AND strategy_id = $2
          AND strategy_version = $3
        """,
        day,
        strategy_id,
        strategy_version,
    )
    if row is None:
        return 0.0, 0
    return float(row["pnl"]), int(row["n"])


async def _compute_outcome_for_row(fit_date: date, picks_json: dict[str, Any]) -> dict[str, Any]:
    buyer = picks_json.get("buyer") or []
    seller = picks_json.get("seller") or []

    async def bucket_stats(items: list[dict[str, Any]]) -> tuple[float | None, float | None, bool | None]:
        if not items:
            return None, None, None
        pnls: list[float] = []
        for it in items:
            sid = str(it.get("strategyId") or "")
            ver = str(it.get("version") or "")
            if not sid:
                continue
            pnl, n = await _aggregate_day_pnl(sid, ver, fit_date)
            if n > 0:
                pnls.append(pnl)
        pick0 = items[0]
        p0, n0 = await _aggregate_day_pnl(str(pick0.get("strategyId")), str(pick0.get("version")), fit_date)
        med = _median(pnls) if pnls else None
        if med is None or n0 == 0:
            return float(p0), med, None
        return float(p0), med, bool(p0 >= med)

    buyer_pnl, buyer_med, buyer_hit = await bucket_stats(buyer)
    seller_pnl, seller_med, seller_hit = await bucket_stats(seller)

    return {
        "buyer_agg_pnl": buyer_pnl,
        "seller_agg_pnl": seller_pnl,
        "buyer_bucket_median_pnl": buyer_med,
        "seller_bucket_median_pnl": seller_med,
        "buyer_beat_median": buyer_hit,
        "seller_beat_median": seller_hit,
    }


async def backfill_pending_outcomes(limit: int = 8) -> None:
    today = _utc_today()
    pending = await fetch(
        """
        SELECT fit_date, picks_json
        FROM s004_landing_strategy_fit_daily
        WHERE outcome_computed_at IS NULL AND fit_date < $1::date
        ORDER BY fit_date ASC
        LIMIT $2
        """,
        today,
        limit,
    )
    for r in pending:
        fd = r["fit_date"]
        pj = r["picks_json"]
        if isinstance(pj, str):
            try:
                pj = json.loads(pj)
            except json.JSONDecodeError:
                pj = {}
        stats = await _compute_outcome_for_row(fd, pj)
        await execute(
            """
            UPDATE s004_landing_strategy_fit_daily SET
                outcome_computed_at = NOW(),
                buyer_agg_pnl = $2,
                seller_agg_pnl = $3,
                buyer_bucket_median_pnl = $4,
                seller_bucket_median_pnl = $5,
                buyer_beat_median = $6,
                seller_beat_median = $7
            WHERE fit_date = $1::date AND outcome_computed_at IS NULL
            """,
            fd,
            stats["buyer_agg_pnl"],
            stats["seller_agg_pnl"],
            stats["buyer_bucket_median_pnl"],
            stats["seller_bucket_median_pnl"],
            stats["buyer_beat_median"],
            stats["seller_beat_median"],
        )


def _payload_from_stored(stored: dict[str, Any], fit_date: date) -> dict[str, Any]:
    row = stored["row"]
    pj = stored["picks_json"]
    fp = pj.get("fingerprint") if isinstance(pj, dict) else None
    if fp is None:
        fp = row.get("market_fingerprint") or {}
    if isinstance(fp, str):
        try:
            fp = json.loads(fp)
        except json.JSONDecodeError:
            fp = {}
    if not isinstance(fp, dict):
        fp = {}
    buyer = stored["buyer"]
    seller = stored["seller"]
    top_b = stored["top_b"]
    top_s = stored["top_s"]
    return {
        "available": bool(buyer or seller),
        "fitDate": fit_date.isoformat(),
        "fromHistory": True,
        "disclaimer": (
            "Heuristic fit only — not investment advice. "
            "Accuracy uses prior-day realized PnL vs same-bucket strategies (subscriber trades)."
        ),
        "marketFingerprint": fp,
        "buyerPick": top_b,
        "sellerPick": top_s,
        "buyerRunnersUp": buyer[1:4],
        "sellerRunnersUp": seller[1:4],
        "buyerTopScore": float(row["buyer_score"]) if row["buyer_score"] is not None else None,
        "sellerTopScore": float(row["seller_score"]) if row["seller_score"] is not None else None,
    }


async def fetch_accuracy_tail(limit: int = 8) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT fit_date, buyer_strategy_id, seller_strategy_id,
               buyer_beat_median, seller_beat_median,
               buyer_agg_pnl, seller_agg_pnl,
               buyer_bucket_median_pnl, seller_bucket_median_pnl,
               outcome_computed_at
        FROM s004_landing_strategy_fit_daily
        WHERE outcome_computed_at IS NOT NULL
        ORDER BY fit_date DESC
        LIMIT $1
        """,
        limit,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "fitDate": r["fit_date"].isoformat() if hasattr(r["fit_date"], "isoformat") else str(r["fit_date"]),
                "buyerStrategyId": r["buyer_strategy_id"],
                "sellerStrategyId": r["seller_strategy_id"],
                "buyerBeatMedian": r["buyer_beat_median"],
                "sellerBeatMedian": r["seller_beat_median"],
                "buyerAggPnl": float(r["buyer_agg_pnl"]) if r["buyer_agg_pnl"] is not None else None,
                "sellerAggPnl": float(r["seller_agg_pnl"]) if r["seller_agg_pnl"] is not None else None,
                "buyerBucketMedianPnl": float(r["buyer_bucket_median_pnl"])
                if r["buyer_bucket_median_pnl"] is not None
                else None,
                "sellerBucketMedianPnl": float(r["seller_bucket_median_pnl"])
                if r["seller_bucket_median_pnl"] is not None
                else None,
            }
        )
    return out


async def attach_strategy_day_fit_to_snapshot(
    *,
    sentiment: dict[str, Any],
    trendpulse: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    """Build strategyDayFit for landing; persist first UTC-day snapshot; backfill outcomes."""
    try:
        fit_date = _utc_today()
        stored = await _load_stored_fit(fit_date)
        catalog = await fetch_published_catalog_rows()
        if not catalog:
            return {
                "available": False,
                "fitDate": fit_date.isoformat(),
                "fromHistory": False,
                "disclaimer": "No published strategies in catalog.",
                "accuracyRecent": [],
                "accuracySummary": {
                    "buyerBeatMedianDays": 0,
                    "buyerScoredDays": 0,
                    "sellerBeatMedianDays": 0,
                    "sellerScoredDays": 0,
                },
            }

        if stored:
            payload = _payload_from_stored(stored, fit_date)
        else:
            payload = build_fit_payload(
                catalog, sentiment, trendpulse, market, fit_date=fit_date, from_history=False
            )
            pj = payload.pop("picksJson", None)
            if not isinstance(pj, dict):
                pj = {"buyer": [], "seller": [], "fingerprint": _market_fingerprint(sentiment, trendpulse, market)}
            fp = pj.get("fingerprint") or _market_fingerprint(sentiment, trendpulse, market)
            top_b = payload.get("buyerPick")
            top_s = payload.get("sellerPick")
            ins_status = await _insert_daily_fit(fit_date, fp, pj, top_b, top_s)
            if ins_status == "INSERT 0 0":
                stored2 = await _load_stored_fit(fit_date)
                if stored2:
                    payload = _payload_from_stored(stored2, fit_date)

        await backfill_pending_outcomes(limit=8)
        payload["accuracyRecent"] = await fetch_accuracy_tail(limit=8)

        hits_b = sum(1 for x in payload["accuracyRecent"] if x.get("buyerBeatMedian") is True)
        hits_s = sum(1 for x in payload["accuracyRecent"] if x.get("sellerBeatMedian") is True)
        counted_b = sum(1 for x in payload["accuracyRecent"] if x.get("buyerBeatMedian") is not None)
        counted_s = sum(1 for x in payload["accuracyRecent"] if x.get("sellerBeatMedian") is not None)
        payload["accuracySummary"] = {
            "buyerBeatMedianDays": hits_b,
            "buyerScoredDays": counted_b,
            "sellerBeatMedianDays": hits_s,
            "sellerScoredDays": counted_s,
        }
        return payload
    except Exception as exc:
        _logger.warning("strategy_day_fit unavailable: %s", exc, exc_info=True)
        return {
            "available": False,
            "fitDate": _utc_today().isoformat(),
            "fromHistory": False,
            "disclaimer": "Strategy fit widget unavailable (database or catalog).",
            "error": str(exc),
            "accuracyRecent": [],
            "accuracySummary": {
                "buyerBeatMedianDays": 0,
                "buyerScoredDays": 0,
                "sellerBeatMedianDays": 0,
                "sellerScoredDays": 0,
            },
        }
