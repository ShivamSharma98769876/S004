"""Queries and helpers for Admin Today's Analysis (heatmaps, decision log, exports)."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db_client import fetch


def ist_day_bounds_aware(d: date) -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    start = datetime.combine(d, datetime.min.time(), tzinfo=ist)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def ist_day_bounds_aware_for_today() -> tuple[datetime, datetime]:
    from zoneinfo import ZoneInfo

    return ist_day_bounds_aware(datetime.now(ZoneInfo("Asia/Kolkata")).date())


def _win_rate(wins: int, total: int) -> float | None:
    if total <= 0:
        return None
    return round(100.0 * wins / total, 1)


def build_heatmap_from_rows(
    rows: list[dict[str, Any]],
    strategy_key: str,
    dim_key: str,
    wins_col: str = "wins",
    total_col: str = "n",
) -> dict[str, Any]:
    strategies = sorted({str(r[strategy_key]) for r in rows if r.get(strategy_key)})
    dims = sorted({str(r[dim_key]) for r in rows if r.get(dim_key) is not None})
    cells: dict[str, dict[str, dict[str, int]]] = {}
    for r in rows:
        sid = str(r.get(strategy_key) or "")
        dim = str(r.get(dim_key) if r.get(dim_key) is not None else "unknown")
        w = int(r.get(wins_col) or 0)
        n = int(r.get(total_col) or 0)
        if sid not in cells:
            cells[sid] = {}
        prev = cells[sid].get(dim, {"wins": 0, "n": 0})
        cells[sid][dim] = {"wins": prev["wins"] + w, "n": prev["n"] + n}
    matrix: list[dict[str, Any]] = []
    for sid in strategies:
        for dim in dims:
            c = (cells.get(sid) or {}).get(dim) or {"wins": 0, "n": 0}
            wr = _win_rate(c["wins"], c["n"])
            matrix.append(
                {
                    "strategy_id": sid,
                    "bucket": dim,
                    "wins": c["wins"],
                    "total": c["n"],
                    "win_rate_pct": wr,
                }
            )
    return {"strategies": strategies, "buckets": dims, "cells": matrix}


async def fetch_decision_log_for_range(start_utc: datetime, end_utc: datetime, limit: int = 250) -> list[dict[str, Any]]:
    try:
        rows = await fetch(
            """
            SELECT l.id, l.user_id, u.username, l.occurred_at, l.mode, l.strategy_id, l.strategy_version,
                   l.gate_blocked, l.gate_reason, l.cycle_summary,
                   l.auto_trade_threshold, l.score_display_threshold, l.min_confidence_threshold,
                   l.open_trades, l.trades_today, l.max_parallel, l.max_trades_day,
                   l.within_trade_window, l.has_kite_live, l.daily_pnl_ok,
                   l.market_context, l.evaluations, l.executed_recommendation_ids
            FROM s004_auto_execute_decision_log l
            JOIN s004_users u ON u.id = l.user_id
            WHERE l.occurred_at >= $1 AND l.occurred_at < $2
            ORDER BY l.occurred_at DESC
            LIMIT $3
            """,
            start_utc,
            end_utc,
            limit,
        )
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows or []:
        mc = r.get("market_context")
        ev = r.get("evaluations")
        ex = r.get("executed_recommendation_ids")
        if isinstance(mc, str):
            try:
                mc = json.loads(mc)
            except json.JSONDecodeError:
                mc = {}
        if isinstance(ev, str):
            try:
                ev = json.loads(ev)
            except json.JSONDecodeError:
                ev = []
        if isinstance(ex, str):
            try:
                ex = json.loads(ex)
            except json.JSONDecodeError:
                ex = []
        occ = r.get("occurred_at")
        out.append(
            {
                "id": int(r["id"]),
                "user_id": int(r["user_id"]),
                "username": str(r.get("username") or ""),
                "occurred_at": occ.isoformat() if hasattr(occ, "isoformat") else str(occ),
                "mode": str(r.get("mode") or ""),
                "strategy_id": str(r.get("strategy_id") or ""),
                "strategy_version": str(r.get("strategy_version") or ""),
                "gate_blocked": bool(r.get("gate_blocked")),
                "gate_reason": r.get("gate_reason"),
                "cycle_summary": str(r.get("cycle_summary") or ""),
                "thresholds": {
                    "auto_trade_score": float(r["auto_trade_threshold"]) if r.get("auto_trade_threshold") is not None else None,
                    "score_display": float(r["score_display_threshold"]) if r.get("score_display_threshold") is not None else None,
                    "min_confidence": float(r["min_confidence_threshold"]) if r.get("min_confidence_threshold") is not None else None,
                },
                "gates": {
                    "open_trades": r.get("open_trades"),
                    "trades_today": r.get("trades_today"),
                    "max_parallel": r.get("max_parallel"),
                    "max_trades_day": r.get("max_trades_day"),
                    "within_trade_window": r.get("within_trade_window"),
                    "has_kite_live": r.get("has_kite_live"),
                    "daily_pnl_ok": r.get("daily_pnl_ok"),
                },
                "market_context": mc if isinstance(mc, dict) else {},
                "evaluations": ev if isinstance(ev, list) else [],
                "executed_recommendation_ids": ex if isinstance(ex, list) else [],
            }
        )
    return out


async def fetch_open_trades_with_recommendation(limit: int = 200) -> list[dict[str, Any]]:
    try:
        rows = await fetch(
        """
        SELECT t.trade_ref, t.user_id, u.username, t.strategy_id, t.strategy_version, t.symbol, t.mode,
               t.side, t.quantity, t.entry_price, t.current_state, t.unrealized_pnl,
               t.opened_at, t.recommendation_id,
               r.reason_code, r.score AS rec_score, r.confidence_score AS rec_confidence,
               t.entry_market_snapshot
        FROM s004_live_trades t
        JOIN s004_users u ON u.id = t.user_id
        LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = t.recommendation_id
        WHERE t.current_state <> 'EXIT'
        ORDER BY t.opened_at DESC
        LIMIT $1
        """,
            limit,
        )
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for r in rows or []:
        snap = r.get("entry_market_snapshot")
        if isinstance(snap, str):
            try:
                snap = json.loads(snap)
            except json.JSONDecodeError:
                snap = {}
        oa = r.get("opened_at")
        out.append(
            {
                "trade_ref": str(r.get("trade_ref") or ""),
                "user_id": int(r["user_id"]),
                "username": str(r.get("username") or ""),
                "strategy_id": str(r.get("strategy_id") or ""),
                "strategy_version": str(r.get("strategy_version") or ""),
                "symbol": str(r.get("symbol") or ""),
                "mode": str(r.get("mode") or ""),
                "side": str(r.get("side") or ""),
                "quantity": int(r.get("quantity") or 0),
                "entry_price": float(r.get("entry_price") or 0),
                "current_state": str(r.get("current_state") or ""),
                "unrealized_pnl": float(r.get("unrealized_pnl") or 0),
                "opened_at": oa.isoformat() if hasattr(oa, "isoformat") else str(oa),
                "recommendation_id": str(r.get("recommendation_id") or ""),
                "reason_code": str(r.get("reason_code") or "") if r.get("reason_code") is not None else None,
                "score_at_entry": int(r["rec_score"]) if r.get("rec_score") is not None else None,
                "confidence_at_entry": float(r["rec_confidence"]) if r.get("rec_confidence") is not None else None,
                "entry_market_snapshot": snap if isinstance(snap, dict) else {},
            }
        )
    return out


async def fetch_heatmap_hour_strategy(days: int = 90) -> dict[str, Any]:
    d = max(7, min(int(days), 365))
    try:
        rows = await fetch(
        """
        SELECT strategy_id,
               EXTRACT(HOUR FROM (opened_at AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata'))::int AS hour_ist,
               COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
               COUNT(*)::bigint AS n
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at >= NOW() - ($1::int * INTERVAL '1 day')
        GROUP BY strategy_id, hour_ist
        ORDER BY strategy_id, hour_ist
        """,
            d,
        )
    except Exception:
        rows = []
    mapped = [
        {"strategy_id": str(r["strategy_id"]), "hour_ist": int(r["hour_ist"]), "wins": int(r["wins"]), "n": int(r["n"])}
        for r in rows or []
    ]
    hm = build_heatmap_from_rows(mapped, "strategy_id", "hour_ist")
    hm["bucket_label"] = "Hour (IST)"
    return hm


async def fetch_heatmap_pcr_strategy(days: int = 90) -> dict[str, Any]:
    d = max(7, min(int(days), 365))
    try:
        rows = await fetch(
        """
        SELECT strategy_id,
               COALESCE(NULLIF(TRIM(entry_market_snapshot->>'pcr_bucket'), ''), 'unknown') AS pcr_bucket,
               COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
               COUNT(*)::bigint AS n
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at >= NOW() - ($1::int * INTERVAL '1 day')
        GROUP BY strategy_id, pcr_bucket
        ORDER BY strategy_id, pcr_bucket
        """,
            d,
        )
    except Exception:
        rows = []
    mapped = [
        {"strategy_id": str(r["strategy_id"]), "pcr_bucket": str(r["pcr_bucket"]), "wins": int(r["wins"]), "n": int(r["n"])}
        for r in rows or []
    ]
    hm = build_heatmap_from_rows(mapped, "strategy_id", "pcr_bucket")
    hm["bucket_label"] = "PCR bucket (at entry)"
    hm["note"] = "Buckets populate when entry_market_snapshot was stored at trade open."
    return hm


async def fetch_heatmap_regime_strategy(days: int = 90) -> dict[str, Any]:
    d = max(7, min(int(days), 365))
    try:
        rows = await fetch(
        """
        SELECT strategy_id,
               COALESCE(NULLIF(TRIM(entry_market_snapshot->>'regime_label'), ''), 'unknown') AS regime_label,
               COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
               COUNT(*)::bigint AS n
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at >= NOW() - ($1::int * INTERVAL '1 day')
        GROUP BY strategy_id, regime_label
        ORDER BY strategy_id, regime_label
        """,
            d,
        )
    except Exception:
        rows = []
    mapped = [
        {"strategy_id": str(r["strategy_id"]), "regime_label": str(r["regime_label"]), "wins": int(r["wins"]), "n": int(r["n"])}
        for r in rows or []
    ]
    hm = build_heatmap_from_rows(mapped, "strategy_id", "regime_label")
    hm["bucket_label"] = "Regime (at entry)"
    hm["note"] = "From entry_market_snapshot.regime_label when present."
    return hm


async def fetch_heatmap_vix_strategy(days: int = 90) -> dict[str, Any]:
    d = max(7, min(int(days), 365))
    try:
        rows = await fetch(
        """
        SELECT strategy_id,
               CASE
                   WHEN entry_market_snapshot->>'india_vix' IS NULL
                        OR TRIM(entry_market_snapshot->>'india_vix') = '' THEN 'unknown'
                   WHEN (entry_market_snapshot->>'india_vix') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        AND (entry_market_snapshot->>'india_vix')::double precision < 12 THEN 'vix_lt_12'
                   WHEN (entry_market_snapshot->>'india_vix') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        AND (entry_market_snapshot->>'india_vix')::double precision < 18 THEN 'vix_12_18'
                   WHEN (entry_market_snapshot->>'india_vix') ~ '^-?[0-9]+(\\.[0-9]+)?$'
                        AND (entry_market_snapshot->>'india_vix')::double precision < 25 THEN 'vix_18_25'
                   WHEN (entry_market_snapshot->>'india_vix') ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN 'vix_ge_25'
                   ELSE 'unknown'
               END AS vix_bucket,
               COUNT(*) FILTER (WHERE realized_pnl > 0) AS wins,
               COUNT(*)::bigint AS n
        FROM s004_live_trades
        WHERE current_state = 'EXIT'
          AND closed_at >= NOW() - ($1::int * INTERVAL '1 day')
        GROUP BY strategy_id, vix_bucket
        ORDER BY strategy_id, vix_bucket
        """,
            d,
        )
    except Exception:
        rows = []
    mapped = [
        {"strategy_id": str(r["strategy_id"]), "vix_bucket": str(r["vix_bucket"]), "wins": int(r["wins"]), "n": int(r["n"])}
        for r in rows or []
    ]
    hm = build_heatmap_from_rows(mapped, "strategy_id", "vix_bucket")
    hm["bucket_label"] = "India VIX (at entry)"
    hm["note"] = "VIX when snapshot includes india_vix from broker quote."
    return hm


def build_analysis_csv_payload(payload: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Section", "Key", "Value"])
    ov = payload.get("overview") or {}
    w.writerow(["overview", "reportDate", ov.get("reportDate")])
    mkt = ov.get("market") or {}
    nifty = mkt.get("nifty") or {}
    w.writerow(["overview", "nifty_spot", nifty.get("spot")])
    w.writerow(["overview", "pcr", mkt.get("pcr")])
    for s in payload.get("strategies_outcome") or []:
        w.writerow(["strategy", s.get("display_name"), json.dumps(s.get("recommendations") or {})])
    for row in payload.get("decision_log") or []:
        w.writerow(
            [
                "decision_log",
                row.get("username"),
                json.dumps(
                    {
                        "at": row.get("occurred_at"),
                        "gate": row.get("gate_reason"),
                        "summary": row.get("cycle_summary"),
                        "evaluations": row.get("evaluations"),
                    },
                    default=str,
                ),
            ]
        )
    for t in payload.get("open_trades") or []:
        w.writerow(
            [
                "open_trade",
                t.get("trade_ref"),
                json.dumps(
                    {
                        "symbol": t.get("symbol"),
                        "reason_code": t.get("reason_code"),
                        "score": t.get("score_at_entry"),
                        "user": t.get("username"),
                    }
                ),
            ]
        )
    return buf.getvalue()


def build_analysis_pdf_bytes(payload: dict[str, Any]) -> bytes:
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise RuntimeError("PDF export requires fpdf2. pip install fpdf2") from e

    class PDF(FPDF):
        def header(self) -> None:
            self.set_font("Helvetica", "B", 11)
            self.cell(0, 8, "StockSage S004 - Today's Analysis", ln=True)
            self.ln(2)

    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 9)
    ov = payload.get("overview") or {}
    lines = [
        f"Report date: {ov.get('reportDate')}",
        f"NIFTY: {ov.get('market', {}).get('nifty')}",
        f"PCR: {ov.get('market', {}).get('pcr')}",
        f"Platform paused: {ov.get('platform', {}).get('trading_paused')}",
        "",
        "Improvement suggestions:",
    ]
    for x in payload.get("improvement_suggestions") or []:
        lines.append(f" - {str(x)[:220]}")
    lines.append("")
    lines.append("Open trades (sample):")
    for t in (payload.get("open_trades") or [])[:35]:
        lines.append(
            f" - {t.get('symbol')} | {t.get('username')} | reason={t.get('reason_code')} | score={t.get('score_at_entry')}"
        )
    lines.append("")
    lines.append("Recent decision log (sample):")
    for drow in (payload.get("decision_log") or [])[:30]:
        lines.append(f" - {drow.get('username')} @ {drow.get('occurred_at')}: {drow.get('gate_reason') or drow.get('cycle_summary')}")
    text = "\n".join(lines)
    for para in text.split("\n"):
        pdf.multi_cell(0, 5, (para or " ")[:800])
    out = pdf.output()
    if isinstance(out, str):
        return out.encode("latin-1", errors="replace")
    return bytes(out)
