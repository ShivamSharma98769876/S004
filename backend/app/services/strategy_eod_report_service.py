"""Strategy-level EOD aggregates (all users) + rule-based suggestions. One row per (IST date, strategy_id, version)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db_client import execute, fetch

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")
# Recompute at most once per clock hour after the market window (captures late closes same day).
_LAST_EOD_UPSERT_IST: tuple[date, int] | None = None
_EOD_RUN_LOCK = asyncio.Lock()


def eod_reports_enabled() -> bool:
    return os.getenv("S004_STRATEGY_EOD_REPORT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def _exit_bucket_counts(exit_counts: dict[str, int]) -> tuple[int, int, int]:
    """Map raw reason_code tallies into SL / target / manual buckets (aliases included)."""
    sl = int(exit_counts.get("SL_HIT", 0))
    tgt = int(exit_counts.get("TARGET_HIT", 0))
    manual_keys = (
        "USER_CLOSE",
        "MANUAL",
        "MANUAL_EXECUTE",
        "USER_EXIT",
        "MANUAL_CLOSE",
        "ADMIN_CLOSE",
        "ADMIN",
        "FORCED_EXIT",
    )
    manual = sum(int(exit_counts.get(k, 0)) for k in manual_keys)
    return sl, tgt, manual


def _build_suggestions(
    *,
    n: int,
    exit_counts: dict[str, int],
    avg_entry_vix: float | None,
) -> list[dict[str, Any]]:
    if n < 5:
        return []
    suggestions: list[dict[str, Any]] = []
    sl, tgt, manual = _exit_bucket_counts(exit_counts)
    sl_share = sl / n
    tgt_share = tgt / n
    manual_share = manual / n

    if sl_share >= 0.65:
        msg = (
            "Most exits are stop-loss hits. Consider widening stop-loss spacing or reviewing "
            "regime / entry filters before adjusting targets."
        )
        suggestions.append({"kind": "execution", "hint_key": "stop_loss_price", "message": msg})
        if avg_entry_vix is not None and avg_entry_vix >= 18:
            suggestions.append(
                {
                    "kind": "parameter",
                    "hint_key": "sl_points",
                    "message": f"Elevated average India VIX at entry (~{avg_entry_vix:.1f}) alongside frequent SLs — "
                    "evaluate slightly wider SL points or reduced size in high-VIX sessions.",
                }
            )

    if tgt_share >= 0.55:
        suggestions.append(
            {
                "kind": "parameter",
                "hint_key": "target_points",
                "message": "A high share of target hits may mean targets are conservative — review reward-to-risk "
                "and whether trailing stops could capture larger moves.",
            }
        )

    if manual_share >= 0.35:
        suggestions.append(
            {
                "kind": "execution",
                "hint_key": "manual_close",
                "message": "Many manual closes — check if users exit early due to UI anxiety or unclear rules; "
                "align communication with automated SL/target behavior.",
            }
        )

    if not suggestions:
        top = sorted(exit_counts.items(), key=lambda kv: -kv[1])[:6]
        mix = ", ".join(f"{k}: {v}" for k, v in top) if top else "no reason codes recorded"
        suggestions.append(
            {
                "kind": "info",
                "hint_key": "exit_mix_thresholds",
                "message": (
                    f"With {n} closed trades, no parameter hint fired: rules need ≥65% stop-loss exits, "
                    f"≥55% target hits, or ≥35% manual/admin closes (after grouping common reason codes). "
                    f"Your exit counts: {mix}."
                ),
            }
        )

    return suggestions


async def run_strategy_eod_for_ist_date(report_date: date, *, force: bool = False) -> int:
    """Build or refresh EOD rows for every strategy version that had at least one close on report_date (IST).

    When ``force`` is True (admin recompute), runs even if ``S004_STRATEGY_EOD_REPORT_ENABLED`` is off.
    """
    if not force and not eod_reports_enabled():
        return 0

    rows = await fetch(
        """
        SELECT t.strategy_id, t.strategy_version, t.mode, t.realized_pnl,
               (SELECT e.reason_code FROM s004_trade_events e
                WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason
        FROM s004_live_trades t
        WHERE t.current_state = 'EXIT'
          AND t.closed_at IS NOT NULL
          AND ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date = $1
        """,
        report_date,
    )
    if not rows:
        return 0

    vix_rows = await fetch(
        """
        SELECT t.strategy_id, t.strategy_version,
               AVG(NULLIF(s.payload->>'vix', '')::double precision) AS avg_vix
        FROM s004_live_trades t
        JOIN s004_trade_chain_snapshots s
          ON s.trade_ref = t.trade_ref AND s.phase = 'entry'
        WHERE t.current_state = 'EXIT'
          AND t.closed_at IS NOT NULL
          AND ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date = $1
        GROUP BY t.strategy_id, t.strategy_version
        """,
        report_date,
    )
    vix_by_key: dict[tuple[str, str], float] = {}
    for vr in vix_rows or []:
        sid = str(vr.get("strategy_id") or "")
        ver = str(vr.get("strategy_version") or "")
        av = vr.get("avg_vix")
        if av is not None:
            try:
                vix_by_key[(sid, ver)] = float(av)
            except (TypeError, ValueError):
                pass

    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "closed_trades": 0,
            "total_realized_pnl": 0.0,
            "wins": 0,
            "losses": 0,
            "paper_trades": 0,
            "live_trades": 0,
            "exit_reasons": defaultdict(int),
        }
    )

    for r in rows:
        sid = str(r.get("strategy_id") or "")
        ver = str(r.get("strategy_version") or "")
        key = (sid, ver)
        b = buckets[key]
        b["closed_trades"] += 1
        pnl = float(r.get("realized_pnl") or 0)
        b["total_realized_pnl"] += pnl
        if pnl > 0:
            b["wins"] += 1
        elif pnl < 0:
            b["losses"] += 1
        mode = str(r.get("mode") or "").upper()
        if mode == "PAPER":
            b["paper_trades"] += 1
        elif mode == "LIVE":
            b["live_trades"] += 1
        reason = str(r.get("exit_reason") or "UNKNOWN")
        b["exit_reasons"][reason] += 1

    n_written = 0
    for (sid, ver), b in buckets.items():
        n = b["closed_trades"]
        win_rate = round(100.0 * b["wins"] / n, 2) if n else 0.0
        exit_reasons = dict(b["exit_reasons"])
        avg_vix = vix_by_key.get((sid, ver))
        suggestions = _build_suggestions(n=n, exit_counts=exit_reasons, avg_entry_vix=avg_vix)
        payload = {
            "report_date_ist": str(report_date),
            "strategy_id": sid,
            "strategy_version": ver,
            "aggregates": {
                "closed_trades": n,
                "total_realized_pnl": round(b["total_realized_pnl"], 2),
                "wins": b["wins"],
                "losses": b["losses"],
                "win_rate_pct": win_rate,
                "exit_reasons": exit_reasons,
                "paper_trades": b["paper_trades"],
                "live_trades": b["live_trades"],
                "avg_entry_vix": round(avg_vix, 2) if avg_vix is not None else None,
            },
            "suggestions": suggestions,
        }
        await execute(
            """
            INSERT INTO s004_strategy_eod_reports (report_date_ist, strategy_id, strategy_version, payload)
            VALUES ($1, $2, $3, $4::jsonb)
            ON CONFLICT (report_date_ist, strategy_id, strategy_version)
            DO UPDATE SET payload = EXCLUDED.payload, created_at = NOW()
            """,
            report_date,
            sid,
            ver,
            json.dumps(payload),
        )
        n_written += 1

    return n_written


async def maybe_run_strategy_eod_reports() -> None:
    """Idempotent per (IST date, hour); serialized with a lock for concurrent create_task callers."""
    global _LAST_EOD_UPSERT_IST
    if not eod_reports_enabled():
        return
    now = datetime.now(_IST)
    if now.hour < 15 or (now.hour == 15 and now.minute < 25):
        return
    d = now.date()
    h = now.hour
    async with _EOD_RUN_LOCK:
        if _LAST_EOD_UPSERT_IST == (d, h):
            return
        try:
            n = await run_strategy_eod_for_ist_date(d)
        except Exception:
            logger.warning("strategy EOD report run failed", exc_info=True)
            return
        _LAST_EOD_UPSERT_IST = (d, h)
    if n:
        logger.debug("strategy EOD reports upserted: %s rows for IST date %s (hour %s)", n, d, h)


async def list_strategy_eod_reports(
    *,
    report_date: date | None = None,
    strategy_id: str | None = None,
    limit: int = 90,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    idx = 1
    if report_date is not None:
        clauses.append(f"report_date_ist = ${idx}")
        args.append(report_date)
        idx += 1
    if strategy_id and strategy_id.strip():
        clauses.append(f"strategy_id = ${idx}")
        args.append(strategy_id.strip())
        idx += 1
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    lim = max(1, min(500, limit))
    args.append(lim)
    rows = await fetch(
        f"""
        SELECT report_date_ist, strategy_id, strategy_version, payload, created_at
        FROM s004_strategy_eod_reports
        {where}
        ORDER BY report_date_ist DESC, strategy_id, strategy_version
        LIMIT ${idx}
        """,
        *args,
    )
    return [dict(r) for r in rows or []]


async def run_eod_for_date_admin(report_date: date) -> dict[str, Any]:
    """Force recompute EOD for a given IST date (admin). Ignores ``S004_STRATEGY_EOD_REPORT_ENABLED``."""
    n = await run_strategy_eod_for_ist_date(report_date, force=True)
    return {"report_date_ist": str(report_date), "rows_upserted": n}
