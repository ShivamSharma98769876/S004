"""Admin user management endpoints."""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, EmailStr

from app.auth_utils import hash_password
from app.db_client import execute, fetch, fetchrow
from app.services.admin_todays_analysis import (
    build_analysis_csv_payload,
    build_analysis_pdf_bytes,
    fetch_decision_log_for_range,
    fetch_heatmap_hour_strategy,
    fetch_heatmap_pcr_strategy,
    fetch_heatmap_regime_strategy,
    fetch_heatmap_vix_strategy,
    fetch_open_trades_with_recommendation,
    ist_day_bounds_aware,
)
from app.services.platform_risk import invalidate_platform_settings_cache
from app.services.sentiment_engine import compute_sentiment_snapshot
from app.services import broker_accounts as broker_accounts_service
from app.services.strategy_eod_report_service import list_strategy_eod_reports, run_eod_for_date_admin
from app.services.trades_service import get_kite_for_quotes
from app.api.auth_context import require_admin
from app.api.routes_landing import _fetch_nifty_market_and_chain, _pcr_sentiment, _spot_trend_label

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""


class UpdateApprovalPayload(BaseModel):
    approved_paper: bool | None = None
    approved_live: bool | None = None


class PlatformRiskPayload(BaseModel):
    trading_paused: bool
    pause_reason: str | None = None


class PlatformBrokerSharedPayload(BaseModel):
    """Single shared broker session for non-admin paper users without own broker connection."""

    brokerCode: str = "zerodha"
    zerodhaApiKey: str = ""
    zerodhaAccessToken: str = ""
    fyersClientId: str = ""
    fyersAccessToken: str = ""


def _parse_active_strategies(raw: object) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


@router.get("/users")
async def list_users(admin_id: int = Depends(require_admin)) -> list[dict]:
    """List all users (admin only), including ACTIVE marketplace strategy subscriptions per user."""
    rows = await fetch(
        """
        SELECT u.id, u.username, u.email, u.full_name, u.role, u.status, u.approved_paper, u.approved_live, u.created_at,
               COALESCE(m.engine_running, FALSE) AS engine_running,
               COALESCE(NULLIF(TRIM(BOTH FROM m.mode), ''), 'PAPER') AS engine_mode,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'strategy_id', s.strategy_id,
                           'strategy_version', s.strategy_version,
                           'display_name', COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version)
                       )
                       ORDER BY COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version)
                   ) FILTER (WHERE s.strategy_id IS NOT NULL),
                   '[]'::json
               ) AS active_strategies
        FROM s004_users u
        LEFT JOIN s004_user_master_settings m ON m.user_id = u.id
        LEFT JOIN s004_strategy_subscriptions s
            ON s.user_id = u.id AND s.status = 'ACTIVE'
        LEFT JOIN s004_strategy_catalog c
            ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
        GROUP BY u.id, u.username, u.email, u.full_name, u.role, u.status, u.approved_paper, u.approved_live, u.created_at,
                 m.engine_running, m.mode
        ORDER BY u.created_at DESC
        """
    )
    out: list[dict] = []
    for r in rows:
        subs = _parse_active_strategies(r.get("active_strategies"))
        normalized = [
            {
                "strategy_id": str(x.get("strategy_id") or ""),
                "strategy_version": str(x.get("strategy_version") or ""),
                "display_name": str(x.get("display_name") or ""),
            }
            for x in subs
            if isinstance(x, dict)
        ]
        out.append(
            {
                "id": int(r["id"]),
                "username": str(r.get("username") or ""),
                "email": str(r.get("email") or ""),
                "full_name": str(r.get("full_name") or ""),
                "role": str(r.get("role", "USER")),
                "status": str(r.get("status", "ACTIVE")),
                "approved_paper": bool(r.get("approved_paper")),
                "approved_live": bool(r.get("approved_live")),
                "engine_running": bool(r.get("engine_running")),
                "engine_mode": str(r.get("engine_mode") or "PAPER").upper(),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "active_strategies": normalized,
            }
        )
    return out


@router.post("/users")
async def create_user(
    payload: CreateUserPayload,
    admin_id: int = Depends(require_admin),
) -> dict:
    """Create a new user (admin only)."""
    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    existing = await fetchrow(
        "SELECT id FROM s004_users WHERE LOWER(email) = $1",
        email,
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    password_hash = hash_password(payload.password)
    username = email[:80]  # Use email as username (unique)
    full_name = (payload.full_name or "").strip() or email.split("@")[0]

    await execute(
        """
        INSERT INTO s004_users (username, email, full_name, role, status, password_hash, approved_paper, approved_live)
        VALUES ($1, $2, $3, 'USER', 'ACTIVE', $4, FALSE, FALSE)
        """,
        username,
        email,
        full_name,
        password_hash,
    )
    row = await fetchrow(
        "SELECT id, username, email, full_name, role, status, approved_paper, approved_live FROM s004_users WHERE email = $1",
        email,
    )
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "email": str(row["email"]),
        "full_name": str(row.get("full_name") or ""),
        "role": str(row.get("role", "USER")),
        "status": str(row.get("status", "ACTIVE")),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }


@router.put("/users/{user_id}/approval")
async def update_user_approval(
    user_id: int,
    payload: UpdateApprovalPayload,
    admin_id: int = Depends(require_admin),
) -> dict:
    """Update user's Paper/Live approval (admin only)."""
    updates = []
    params = []
    idx = 1
    if payload.approved_paper is not None:
        updates.append(f"approved_paper = ${idx}")
        params.append(payload.approved_paper)
        idx += 1
    if payload.approved_live is not None:
        updates.append(f"approved_live = ${idx}")
        params.append(payload.approved_live)
        idx += 1
    if not updates:
        raise HTTPException(status_code=400, detail="Provide approved_paper and/or approved_live.")

    params.append(user_id)
    await execute(
        f"""
        UPDATE s004_users
        SET {", ".join(updates)}, updated_at = NOW()
        WHERE id = ${idx}
        """,
        *params,
    )
    row = await fetchrow(
        "SELECT id, username, email, approved_paper, approved_live FROM s004_users WHERE id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "id": int(row["id"]),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }


@router.get("/platform")
async def get_platform_risk(admin_id: int = Depends(require_admin)) -> dict:
    """Global trading pause (kill switch). Requires s004_platform_settings (run platform_risk_schema.sql)."""
    try:
        row = await fetchrow(
            """
            SELECT trading_paused, pause_reason, updated_at
            FROM s004_platform_settings
            WHERE id = 1
            """,
        )
    except Exception:
        return {
            "trading_paused": False,
            "pause_reason": None,
            "updated_at": None,
            "schema_ready": False,
        }
    if not row:
        return {
            "trading_paused": False,
            "pause_reason": None,
            "updated_at": None,
            "schema_ready": False,
        }
    return {
        "trading_paused": bool(row.get("trading_paused")),
        "pause_reason": str(row["pause_reason"]).strip() if row.get("pause_reason") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "schema_ready": True,
    }


@router.put("/platform")
async def set_platform_risk(
    payload: PlatformRiskPayload,
    admin_id: int = Depends(require_admin),
) -> dict:
    """Pause or resume all new trades platform-wide."""
    reason = (payload.pause_reason or "").strip() or None
    await execute(
        """
        INSERT INTO s004_platform_settings (id, trading_paused, pause_reason, updated_at, updated_by)
        VALUES (1, $1, $2, NOW(), $3)
        ON CONFLICT (id) DO UPDATE SET
            trading_paused = EXCLUDED.trading_paused,
            pause_reason = EXCLUDED.pause_reason,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        payload.trading_paused,
        reason,
        admin_id,
    )
    row = await fetchrow(
        "SELECT trading_paused, pause_reason, updated_at FROM s004_platform_settings WHERE id = 1",
    )
    await invalidate_platform_settings_cache()
    return {
        "trading_paused": bool(row.get("trading_paused")) if row else payload.trading_paused,
        "pause_reason": str(row["pause_reason"]).strip() if row and row.get("pause_reason") else None,
        "updated_at": row["updated_at"].isoformat() if row and row.get("updated_at") else None,
    }


def _ist_calendar_day_utc_naive_bounds(d: date) -> tuple[datetime, datetime]:
    """Inclusive start, exclusive end in UTC-naive timestamps for IST calendar day `d`."""
    ist = ZoneInfo("Asia/Kolkata")
    start_ist = datetime.combine(d, datetime.min.time(), tzinfo=ist)
    end_ist = start_ist + timedelta(days=1)
    return (
        start_ist.astimezone(timezone.utc).replace(tzinfo=None),
        end_ist.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _num(v: object) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _build_improvement_suggestions(
    *,
    platform_paused: bool,
    engines_running: int,
    total_generated_today: int,
    total_accepted_today: int,
    strategy_14d: dict[str, dict[str, float]],
) -> list[str]:
    out: list[str] = []
    if platform_paused:
        out.append(
            "Platform trading is paused (kill switch). New auto-executions are blocked until you resume in Admin → platform settings."
        )
    if engines_running == 0:
        out.append(
            "No users currently have the engine running, so the auto-execute loop will not place trades regardless of signals."
        )
    if total_generated_today > 0 and total_accepted_today == 0 and not platform_paused and engines_running > 0:
        out.append(
            "Recommendations were generated today but none were auto-accepted. Typical filters: confidence < 80, score below auto-trade threshold, "
            "signal not Eligible vs score threshold, outside per-user trade window, max parallel/day reached, or LIVE without a valid broker session."
        )
    elif total_generated_today > 5 and total_accepted_today > 0:
        ratio = total_accepted_today / total_generated_today
        if ratio < 0.15:
            out.append(
                "A low share of generated recommendations became trades. Consider reviewing catalog auto-trade score thresholds and user trade windows for fit with current volatility."
            )
    for sid, stats in strategy_14d.items():
        total = int(stats.get("total") or 0)
        if total < 5:
            continue
        wins = int(stats.get("wins") or 0)
        wr = wins / total if total else 0.0
        if wr < 0.35:
            out.append(
                f"Strategy `{sid}`: ~{wr:.0%} win rate on closed trades in the last 14 days ({wins}/{total} wins). "
                "Tighten risk (targets/stops) or raise entry quality thresholds if drawdowns are unacceptable."
            )
        elif wr > 0.65 and total >= 10:
            out.append(
                f"Strategy `{sid}`: strong recent win rate (~{wr:.0%} over {total} closed trades). "
                "Document what regime this favors so expectations stay realistic when conditions change."
            )
    if not out:
        out.append(
            "Collect a few weeks of tagged outcomes (regime, PCR bucket, time-of-day) to tune thresholds with evidence rather than intraday noise."
        )
    return out


def _gate_hints_for_strategy_from_logs(log_rows: list[dict[str, Any]], strategy_id: str) -> list[str]:
    """Surface factual gate / eligibility lines from auto-execute decision log."""
    hints: list[str] = []
    for row in log_rows:
        if str(row.get("strategy_id") or "") != strategy_id:
            continue
        uname = str(row.get("username") or row.get("user_id") or "")
        if row.get("gate_blocked") and row.get("gate_reason"):
            hints.append(f"{uname}: gate → {row['gate_reason']}")
        for ev in (row.get("evaluations") or [])[:6]:
            if not isinstance(ev, dict):
                continue
            if ev.get("eligible_for_auto_execute"):
                continue
            sym = str(ev.get("symbol") or "")
            br = ev.get("block_reasons") or []
            br_s = ", ".join(str(x) for x in br) if br else "blocked"
            hints.append(f"{uname}: {sym or ev.get('recommendation_id', '')} → {br_s}")
    seen: set[str] = set()
    out: list[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:10]


async def _assemble_todays_analysis_payload(admin_id: int) -> dict[str, Any]:
    """Shared JSON payload for Today's Analysis page and CSV/PDF export."""
    ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    report_date: date = ist_now.date()
    day_start, day_end = _ist_calendar_day_utc_naive_bounds(report_date)
    day_start_tz, day_end_tz = ist_day_bounds_aware(report_date)

    platform_paused = False
    pause_reason: str | None = None
    try:
        pr = await fetchrow(
            "SELECT trading_paused, pause_reason FROM s004_platform_settings WHERE id = 1",
        )
        if pr:
            platform_paused = bool(pr.get("trading_paused"))
            pause_reason = str(pr["pause_reason"]).strip() if pr.get("pause_reason") else None
    except Exception:
        pass

    eng_row = await fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE engine_running) AS running,
               COUNT(*) AS total
        FROM s004_user_master_settings
        """
    )
    engines_running = int(eng_row["running"] or 0) if eng_row else 0
    engine_users_total = int(eng_row["total"] or 0) if eng_row else 0

    subscribed_rows = await fetch(
        """
        SELECT COUNT(DISTINCT user_id) AS n
        FROM s004_strategy_subscriptions
        WHERE status = 'ACTIVE'
        """
    )
    subscribed_users = int(subscribed_rows[0]["n"] or 0) if subscribed_rows else 0

    kite = await get_kite_for_quotes(admin_id)
    nifty_spot, nifty_chg, pcr, chain_payload = await _fetch_nifty_market_and_chain(kite)
    sentiment = compute_sentiment_snapshot(
        chain_payload=chain_payload,
        spot_chg_pct=nifty_chg,
        trendpulse_signal=None,
    )
    market = {
        "nifty": {"spot": round(nifty_spot, 2), "changePct": round(nifty_chg, 2)},
        "pcr": round(pcr, 2) if pcr is not None else None,
        "sentimentLabel": sentiment.get("sentimentLabel") or _pcr_sentiment(pcr),
        "intradayTrendLabel": _spot_trend_label(nifty_chg),
    }

    trade_rows = await fetch(
        """
        SELECT strategy_id, strategy_version,
               COUNT(*) AS trades_total,
               COUNT(*) FILTER (WHERE current_state = 'EXIT') AS closed_n,
               COUNT(*) FILTER (WHERE current_state <> 'EXIT') AS open_n,
               COALESCE(SUM(CASE WHEN current_state = 'EXIT' THEN realized_pnl ELSE 0 END), 0) AS realized_pnl,
               COALESCE(SUM(CASE WHEN current_state <> 'EXIT' THEN unrealized_pnl ELSE 0 END), 0) AS open_unrealized_pnl
        FROM s004_live_trades
        WHERE opened_at >= $1 AND opened_at < $2
        GROUP BY strategy_id, strategy_version
        ORDER BY strategy_id, strategy_version
        """,
        day_start,
        day_end,
    )

    rec_rows = await fetch(
        """
        SELECT strategy_id, strategy_version, status,
               COUNT(*)::bigint AS n,
               AVG(confidence_score) AS avg_confidence,
               AVG(score) AS avg_score
        FROM s004_trade_recommendations
        WHERE updated_at >= $1 AND updated_at < $2
        GROUP BY strategy_id, strategy_version, status
        ORDER BY strategy_id, strategy_version, status
        """,
        day_start,
        day_end,
    )

    hist_rows = await fetch(
        """
        SELECT strategy_id,
               COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
               COUNT(*) FILTER (WHERE realized_pnl < 0) AS losses,
               COUNT(*) FILTER (WHERE realized_pnl = 0) AS flat,
               COUNT(*) AS total
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at >= NOW() - INTERVAL '14 days'
        GROUP BY strategy_id
        """,
    )

    catalog_rows = await fetch(
        """
        SELECT strategy_id, version AS strategy_version, display_name
        FROM s004_strategy_catalog
        """
    )
    catalog_map: dict[tuple[str, str], str] = {}
    for c in catalog_rows or []:
        catalog_map[(str(c["strategy_id"]), str(c["strategy_version"]))] = str(c.get("display_name") or "")

    decision_log_rows = await fetch_decision_log_for_range(day_start_tz, day_end_tz, 300)
    open_trades_rows = await fetch_open_trades_with_recommendation(200)
    heatmap_days = 90
    heatmaps = {
        "time_of_day_ist": await fetch_heatmap_hour_strategy(heatmap_days),
        "pcr_bucket": await fetch_heatmap_pcr_strategy(heatmap_days),
        "regime": await fetch_heatmap_regime_strategy(heatmap_days),
        "india_vix": await fetch_heatmap_vix_strategy(heatmap_days),
        "lookback_days": heatmap_days,
    }

    strategy_14d: dict[str, dict[str, float]] = {}
    for h in hist_rows or []:
        sid = str(h["strategy_id"])
        strategy_14d[sid] = {
            "wins": float(h["wins"] or 0),
            "losses": float(h["losses"] or 0),
            "flat": float(h.get("flat") or 0),
            "total": float(h["total"] or 0),
        }

    rec_by_strat: dict[tuple[str, str], dict[str, Any]] = {}
    total_generated = 0
    total_accepted = 0
    for r in rec_rows or []:
        key = (str(r["strategy_id"]), str(r["strategy_version"]))
        st = str(r["status"] or "")
        n = int(r["n"] or 0)
        if key not in rec_by_strat:
            rec_by_strat[key] = {
                "generated": 0,
                "accepted": 0,
                "rejected": 0,
                "skipped": 0,
                "expired": 0,
                "avg_confidence_generated": None,
                "avg_score_generated": None,
            }
        bucket = rec_by_strat[key]
        if st == "GENERATED":
            bucket["generated"] += n
            total_generated += n
            bucket["avg_confidence_generated"] = _num(r["avg_confidence"])
            sc = r.get("avg_score")
            bucket["avg_score_generated"] = _num(sc) if sc is not None else None
        elif st == "ACCEPTED":
            bucket["accepted"] += n
            total_accepted += n
        elif st == "REJECTED":
            bucket["rejected"] += n
        elif st == "SKIPPED":
            bucket["skipped"] += n
        elif st == "EXPIRED":
            bucket["expired"] += n

    trade_by_strat: dict[tuple[str, str], dict[str, Any]] = {}
    for t in trade_rows or []:
        key = (str(t["strategy_id"]), str(t["strategy_version"]))
        trade_by_strat[key] = {
            "trades_total": int(t["trades_total"] or 0),
            "closed_n": int(t["closed_n"] or 0),
            "open_n": int(t["open_n"] or 0),
            "realized_pnl": _num(t["realized_pnl"]),
            "open_unrealized_pnl": _num(t["open_unrealized_pnl"]),
        }

    all_keys = set(rec_by_strat.keys()) | set(trade_by_strat.keys())
    for c in catalog_rows or []:
        all_keys.add((str(c["strategy_id"]), str(c["strategy_version"])))
    strategies_outcome: list[dict[str, Any]] = []

    auto_exec_criteria = (
        "Auto-execute (when enabled) takes GENERATED recommendations where confidence ≥ 80, score ≥ per-strategy auto-trade threshold, "
        "the signal is Eligible (typically score ≥ display threshold), the user is inside their trade window, under max parallel and max trades/day, "
        "platform is not paused, and LIVE mode has a valid broker session."
    )

    for key in sorted(all_keys):
        sid, ver = key
        disp = catalog_map.get(key) or f"{sid} {ver}"
        rec = rec_by_strat.get(key, {})
        tr = trade_by_strat.get(key, {})
        gen = int(rec.get("generated") or 0)
        acc = int(rec.get("accepted") or 0)
        trades_total = int(tr.get("trades_total") or 0)
        commentary_parts: list[str] = []
        if gen > 0 or acc > 0:
            commentary_parts.append(
                f"Today (IST): {gen} recommendation(s) still or were in GENERATED; {acc} marked ACCEPTED (usually after execution)."
            )
        else:
            commentary_parts.append("No recommendations recorded for this strategy in today’s IST window yet.")
        if trades_total > 0:
            commentary_parts.append(
                f"Trades opened today: {trades_total} (closed: {tr.get('closed_n', 0)}, still open: {tr.get('open_n', 0)}). "
                f"Realized PnL from closed: {_num(tr.get('realized_pnl')):.2f}; open unrealized (approx.): {_num(tr.get('open_unrealized_pnl')):.2f}."
            )
        else:
            commentary_parts.append("No trades opened today for this strategy in the book.")
        why_no_trade: list[str] = []
        if platform_paused:
            why_no_trade.append("Platform trading is paused — auto-execute exits immediately.")
        if engines_running == 0:
            why_no_trade.append("No engine-running users — auto-execute does not run for anyone.")
        log_hints = _gate_hints_for_strategy_from_logs(decision_log_rows, sid)
        if log_hints:
            why_no_trade.extend(log_hints)
        elif gen > 0 and acc == 0 and trades_total == 0 and not platform_paused and engines_running > 0:
            why_no_trade.append(
                "No matching decision log lines yet for this strategy today, or filters blocked execution. "
                "See the Decision log table for per-user thresholds and eligibility rows (populates when the engine runs)."
            )
        if gen == 0 and trades_total == 0 and not log_hints:
            why_no_trade.append(
                "No recommendation activity recorded today (IST) for this strategy — pipeline may not have produced candidates."
            )

        strategies_outcome.append(
            {
                "strategy_id": sid,
                "strategy_version": ver,
                "display_name": disp,
                "recommendations": {
                    "generated": gen,
                    "accepted": acc,
                    "rejected": int(rec.get("rejected") or 0),
                    "skipped": int(rec.get("skipped") or 0),
                    "expired": int(rec.get("expired") or 0),
                    "avg_confidence_generated": rec.get("avg_confidence_generated"),
                    "avg_score_generated": rec.get("avg_score_generated"),
                },
                "trades": tr,
                "commentary": " ".join(commentary_parts),
                "why_no_trade_hints": why_no_trade,
            }
        )

    improvements = _build_improvement_suggestions(
        platform_paused=platform_paused,
        engines_running=engines_running,
        total_generated_today=total_generated,
        total_accepted_today=total_accepted,
        strategy_14d=strategy_14d,
    )

    overview = {
        "reportDate": report_date.isoformat(),
        "reportTimezone": "Asia/Kolkata",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": {
            "trading_paused": platform_paused,
            "pause_reason": pause_reason,
        },
        "activity": {
            "users_with_engine_running": engines_running,
            "users_with_master_settings_rows": engine_users_total,
            "users_with_active_strategy_subscription": subscribed_users,
        },
        "market": market,
        "sentiment": {
            "directionLabel": sentiment.get("directionLabel"),
            "directionScore": sentiment.get("directionScore"),
            "confidence": sentiment.get("confidence"),
            "regimeLabel": sentiment.get("regimeLabel"),
            "drivers": sentiment.get("drivers"),
        },
        "broker_connected_for_snapshot": bool(kite),
        "auto_execute_criteria_summary": auto_exec_criteria,
        "recommendation_counts_note": "Recommendation counts use rows whose updated_at falls in today’s IST window (status changes and touch-ups).",
    }

    return {
        "overview": overview,
        "strategies_outcome": strategies_outcome,
        "improvement_suggestions": improvements,
        "historical_14d_exit_stats_by_strategy": [
            {
                "strategy_id": sid,
                "wins": int(v["wins"]),
                "losses": int(v["losses"]),
                "breakeven": int(v["flat"]),
                "total": int(v["total"]),
            }
            for sid, v in sorted(strategy_14d.items(), key=lambda x: x[0])
        ],
        "decision_log": decision_log_rows,
        "decision_log_note": "One row per user every ~50s when the engine runs; includes thresholds and per-recommendation eligibility.",
        "open_trades": open_trades_rows,
        "heatmaps": heatmaps,
    }


@router.get("/todays-analysis")
async def todays_analysis(admin_id: int = Depends(require_admin)) -> dict[str, Any]:
    """Admin daily brief with decision log, open-trade linkage, and regime/PCR/VIX heatmaps."""
    return await _assemble_todays_analysis_payload(admin_id)


@router.get("/todays-analysis/export")
async def todays_analysis_export(
    admin_id: int = Depends(require_admin),
    export_format: str = Query("csv", alias="format"),
) -> Response:
    """Download today's analysis as CSV or PDF (journal / advisor)."""
    payload = await _assemble_todays_analysis_payload(admin_id)
    fmt = (export_format or "csv").lower().strip()
    if fmt == "csv":
        body = build_analysis_csv_payload(payload)
        return Response(
            content="\ufeff" + body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="s004-todays-analysis.csv"'},
        )
    if fmt == "pdf":
        try:
            pdf_bytes = build_analysis_pdf_bytes(payload)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="s004-todays-analysis.pdf"'},
        )
    raise HTTPException(status_code=400, detail='format must be "csv" or "pdf"')


@router.get("/strategy-eod-reports")
async def admin_list_strategy_eod_reports(
    admin_id: int = Depends(require_admin),
    report_date: date | None = Query(default=None, description="IST calendar date; omit for latest rows"),
    strategy_id: str | None = Query(default=None),
    limit: int = Query(default=90, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Aggregated strategy-level EOD payloads (all users), with rule-based suggestions."""
    return await list_strategy_eod_reports(
        report_date=report_date,
        strategy_id=strategy_id,
        limit=limit,
    )


@router.post("/strategy-eod-reports/run")
async def admin_run_strategy_eod_reports(
    admin_id: int = Depends(require_admin),
    report_date: date = Query(..., description="IST date to aggregate closed trades for"),
) -> dict[str, Any]:
    """Recompute and upsert EOD report rows for the given IST date."""
    return await run_eod_for_date_admin(report_date)


def _admin_client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None


@router.get("/platform-broker")
async def admin_platform_broker_get(admin_id: int = Depends(require_admin)) -> dict[str, Any]:
    _ = admin_id
    return await broker_accounts_service.get_platform_shared_status()


@router.put("/platform-broker")
async def admin_platform_broker_put(
    payload: PlatformBrokerSharedPayload,
    request: Request,
    admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    code = payload.brokerCode.strip().lower()
    if code not in {broker_accounts_service.BROKER_ZERODHA, broker_accounts_service.BROKER_FYERS}:
        raise HTTPException(status_code=400, detail="brokerCode must be zerodha or fyers.")
    if not broker_accounts_service.fernet_key_configured():
        raise HTTPException(status_code=503, detail="Set S004_CREDENTIALS_FERNET_KEY on the server.")

    if code == broker_accounts_service.BROKER_ZERODHA:
        if not payload.zerodhaApiKey.strip() or not payload.zerodhaAccessToken.strip():
            raise HTTPException(status_code=400, detail="zerodhaApiKey and zerodhaAccessToken are required.")
        vault = {
            broker_accounts_service.BROKER_ZERODHA: {
                "apiKey": payload.zerodhaApiKey.strip(),
                "apiSecret": "",
                "accessToken": payload.zerodhaAccessToken.strip(),
            }
        }
    else:
        if not payload.fyersClientId.strip() or not payload.fyersAccessToken.strip():
            raise HTTPException(status_code=400, detail="fyersClientId and fyersAccessToken are required.")
        vault = {
            broker_accounts_service.BROKER_FYERS: {
                "clientId": payload.fyersClientId.strip(),
                "secretKey": "",
                "redirectUri": "",
                "accessToken": payload.fyersAccessToken.strip(),
            }
        }
    try:
        await broker_accounts_service.save_platform_shared_vault(
            admin_user_id=admin_id,
            broker_code=code,
            vault=vault,
            client_ip=_admin_client_ip(request),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, **(await broker_accounts_service.get_platform_shared_status())}


@router.delete("/platform-broker")
async def admin_platform_broker_delete(
    request: Request,
    admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    await broker_accounts_service.clear_platform_shared(
        admin_user_id=admin_id,
        client_ip=_admin_client_ip(request),
    )
    return {"ok": True}
