"""Daily strategy metrics rollups, recommendation rules, and version merge helpers."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any
from zoneinfo import ZoneInfo

from app.db_client import execute, fetch, fetchrow


def shallow_merge_details(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = shallow_merge_details(out[key], val)  # type: ignore[arg-type]
        else:
            out[key] = val
    return out


def suggest_next_catalog_version(from_version: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", from_version.strip())
    if m:
        major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{major}.{minor}.{patch + 1}"
    if re.match(r"^v\d+$", from_version, re.I):
        n = int(from_version[1:])
        return f"v{n + 1}"
    return f"{from_version}-evo"


async def recompute_daily_metrics(
    strategy_id: str | None,
    from_date_ist: date | None,
    to_date_ist: date | None,
) -> int:
    """Upsert daily rows from closed live trades. Returns number of rows touched (insert + update)."""
    rows = await fetch(
        """
        WITH agg AS (
            SELECT
                t.strategy_id,
                t.strategy_version,
                ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date AS trade_date_ist,
                COUNT(*)::int AS closed_trades,
                COUNT(*) FILTER (WHERE t.realized_pnl > 0)::int AS winning_trades,
                COUNT(*) FILTER (WHERE t.realized_pnl < 0)::int AS losing_trades,
                COALESCE(SUM(t.realized_pnl), 0)::numeric(14, 4) AS realized_pnl,
                COALESCE(SUM(t.realized_pnl) FILTER (WHERE t.realized_pnl > 0), 0)::numeric(14, 4) AS gross_win_pnl,
                COALESCE(ABS(SUM(t.realized_pnl) FILTER (WHERE t.realized_pnl < 0)), 0)::numeric(14, 4) AS gross_loss_pnl,
                MAX(t.realized_pnl) FILTER (WHERE t.realized_pnl > 0) AS largest_win,
                MIN(t.realized_pnl) FILTER (WHERE t.realized_pnl < 0) AS largest_loss
            FROM s004_live_trades t
            WHERE t.current_state = 'EXIT'
              AND t.closed_at IS NOT NULL
              AND ($1::date IS NULL OR ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date >= $1::date)
              AND ($2::date IS NULL OR ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date <= $2::date)
              AND ($3::text IS NULL OR t.strategy_id = $3)
            GROUP BY t.strategy_id, t.strategy_version,
                ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date
        )
        INSERT INTO s004_strategy_daily_metrics (
            strategy_id, strategy_version, trade_date_ist,
            closed_trades, winning_trades, losing_trades,
            realized_pnl, gross_win_pnl, gross_loss_pnl, win_rate_pct, metrics_json, computed_at
        )
        SELECT
            a.strategy_id,
            a.strategy_version,
            a.trade_date_ist,
            a.closed_trades,
            a.winning_trades,
            a.losing_trades,
            a.realized_pnl,
            a.gross_win_pnl,
            a.gross_loss_pnl,
            CASE
                WHEN a.closed_trades > 0 THEN ROUND(100.0 * a.winning_trades / a.closed_trades, 2)
                ELSE NULL
            END,
            jsonb_build_object(
                'largest_win', COALESCE(a.largest_win, 0),
                'largest_loss', COALESCE(a.largest_loss, 0),
                'profit_factor',
                CASE
                    WHEN a.gross_loss_pnl > 0 THEN ROUND((a.gross_win_pnl / a.gross_loss_pnl)::numeric, 4)
                    WHEN a.gross_win_pnl > 0 THEN NULL
                    ELSE NULL
                END
            ),
            NOW()
        FROM agg a
        ON CONFLICT (strategy_id, strategy_version, trade_date_ist) DO UPDATE SET
            closed_trades = EXCLUDED.closed_trades,
            winning_trades = EXCLUDED.winning_trades,
            losing_trades = EXCLUDED.losing_trades,
            realized_pnl = EXCLUDED.realized_pnl,
            gross_win_pnl = EXCLUDED.gross_win_pnl,
            gross_loss_pnl = EXCLUDED.gross_loss_pnl,
            win_rate_pct = EXCLUDED.win_rate_pct,
            metrics_json = EXCLUDED.metrics_json,
            computed_at = EXCLUDED.computed_at
        RETURNING strategy_id
        """,
        from_date_ist,
        to_date_ist,
        strategy_id,
    )
    return len(rows)


def _today_ist() -> date:
    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


async def fetch_strategy_evaluation_summary(
    strategy_id: str,
    strategy_version: str | None,
    *,
    days: int = 30,
) -> dict[str, Any]:
    """
    Roll up s004_strategy_daily_metrics for finetuning: PnL, win rate, profit factor (from metrics_json).
    """
    end = _today_ist()
    start = end - timedelta(days=max(0, int(days) - 1))
    series = await fetch_daily_metrics_series(strategy_id, strategy_version, start, end)
    closed_total = sum(int(r.get("closed_trades") or 0) for r in series)
    wins = sum(int(r.get("winning_trades") or 0) for r in series)
    losses = sum(int(r.get("losing_trades") or 0) for r in series)
    pnl = sum(float(r.get("realized_pnl") or 0) for r in series)
    pf_vals: list[float] = []
    for r in series:
        mj = r.get("metrics_json") or {}
        if isinstance(mj, str):
            try:
                mj = json.loads(mj)
            except json.JSONDecodeError:
                mj = {}
        if not isinstance(mj, dict):
            continue
        pf = mj.get("profit_factor")
        if pf is not None:
            try:
                pf_vals.append(float(pf))
            except (TypeError, ValueError):
                pass
    days_with_activity = sum(1 for r in series if int(r.get("closed_trades") or 0) > 0)
    return {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "window_days": int(days),
        "from_date_ist": start.isoformat(),
        "to_date_ist": end.isoformat(),
        "rows": len(series),
        "closed_trades": closed_total,
        "winning_trades": wins,
        "losing_trades": losses,
        "aggregate_win_rate_pct": round(100.0 * wins / closed_total, 2) if closed_total > 0 else None,
        "total_realized_pnl": round(pnl, 4),
        "avg_daily_profit_factor": round(mean(pf_vals), 4) if pf_vals else None,
        "days_with_closed_trades": days_with_activity,
        "daily": [
            {
                "trade_date_ist": r["trade_date_ist"].isoformat()
                if isinstance(r.get("trade_date_ist"), date)
                else str(r.get("trade_date_ist")),
                "closed_trades": int(r.get("closed_trades") or 0),
                "realized_pnl": float(r.get("realized_pnl") or 0),
                "win_rate_pct": float(r["win_rate_pct"]) if r.get("win_rate_pct") is not None else None,
                "metrics_json": _coerce_metrics_json(r.get("metrics_json")),
            }
            for r in series
        ],
    }


def _coerce_metrics_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return o if isinstance(o, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def fetch_daily_metrics_series(
    strategy_id: str,
    strategy_version: str | None,
    from_date_ist: date | None,
    to_date_ist: date | None,
) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT
            strategy_id, strategy_version, trade_date_ist,
            closed_trades, winning_trades, losing_trades,
            realized_pnl, gross_win_pnl, gross_loss_pnl, win_rate_pct,
            metrics_json, computed_at
        FROM s004_strategy_daily_metrics
        WHERE strategy_id = $1
          AND ($2::text IS NULL OR strategy_version = $2)
          AND ($3::date IS NULL OR trade_date_ist >= $3::date)
          AND ($4::date IS NULL OR trade_date_ist <= $4::date)
        ORDER BY strategy_version, trade_date_ist ASC
        """,
        strategy_id,
        strategy_version,
        from_date_ist,
        to_date_ist,
    )
    return [dict(r) for r in rows]


async def list_catalog_strategy_ids() -> list[str]:
    rows = await fetch(
        """
        SELECT DISTINCT strategy_id FROM s004_strategy_catalog
        ORDER BY strategy_id ASC
        """
    )
    return [r["strategy_id"] for r in rows]


async def list_catalog_versions(strategy_id: str) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT strategy_id, version, display_name, publish_status, updated_at
        FROM s004_strategy_catalog
        WHERE strategy_id = $1
        ORDER BY updated_at DESC
        """,
        strategy_id,
    )
    return [dict(r) for r in rows]


async def _trailing_aggregate(strategy_id: str, version: str, days: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT
            COALESCE(SUM(closed_trades), 0)::int AS trades,
            COALESCE(SUM(realized_pnl), 0)::numeric AS pnl,
            CASE
                WHEN COALESCE(SUM(closed_trades), 0) > 0
                THEN ROUND(
                    100.0 * COALESCE(SUM(winning_trades), 0)::numeric
                    / NULLIF(SUM(closed_trades), 0),
                    2
                )
                ELSE NULL
            END AS win_rate_pct
        FROM s004_strategy_daily_metrics
        WHERE strategy_id = $1
          AND strategy_version = $2
          AND trade_date_ist >= (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date - ($3::int * INTERVAL '1 day')
        """,
        strategy_id,
        version,
        days,
    )
    return dict(row) if row else None


async def generate_rule_based_recommendations(strategy_id: str | None) -> list[int]:
    """
    Insert PENDING_REVIEW rows from simple rules. Skips duplicate code+strategy+version open rows.
    Returns list of new recommendation ids.
    """
    versions_query = """
        SELECT strategy_id, version FROM s004_strategy_catalog
        WHERE publish_status IN ('PUBLISHED', 'DRAFT')
    """
    if strategy_id:
        versions_query = """
            SELECT strategy_id, version FROM s004_strategy_catalog
            WHERE strategy_id = $1 AND publish_status IN ('PUBLISHED', 'DRAFT')
        """
        cat_rows = await fetch(versions_query, strategy_id)
    else:
        cat_rows = await fetch(versions_query)

    new_ids: list[int] = []
    for r in cat_rows:
        sid, ver = r["strategy_id"], r["version"]
        agg = await _trailing_aggregate(sid, ver, 14)
        if agg is None or int(agg.get("trades") or 0) < 5:
            continue
        trades = int(agg["trades"])
        wr = agg.get("win_rate_pct")
        pnl = float(agg["pnl"] or 0)

        async def _insert(
            code: str,
            title: str,
            rationale: dict[str, Any],
            patch: dict[str, Any],
        ) -> None:
            dup = await fetchrow(
                """
                SELECT id FROM s004_strategy_evolution_recommendations
                WHERE strategy_id = $1 AND from_version = $2 AND recommendation_code = $3
                  AND status IN ('DRAFT', 'PENDING_REVIEW', 'APPROVED')
                LIMIT 1
                """,
                sid,
                ver,
                code,
            )
            if dup:
                return
            row = await fetchrow(
                """
                INSERT INTO s004_strategy_evolution_recommendations (
                    strategy_id, from_version, recommendation_code, proposed_title,
                    rationale_json, proposed_details_patch, status, updated_at
                )
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, 'PENDING_REVIEW', NOW())
                RETURNING id
                """,
                sid,
                ver,
                code,
                title,
                json.dumps(rationale),
                json.dumps(patch),
            )
            if row:
                new_ids.append(int(row["id"]))

        if wr is not None and float(wr) < 38.0 and trades >= 8:
            await _insert(
                "WIN_RATE_LOW_14D",
                "Review entry quality — trailing 14d win rate is below 38%",
                {
                    "window_days": 14,
                    "win_rate_pct": float(wr),
                    "closed_trades": trades,
                    "hint": "Consider tightening signal filters or stops; validate on paper before live.",
                },
                {},
            )

        if pnl < 0 and trades >= 10:
            await _insert(
                "NEGATIVE_PNL_14D",
                "Trailing 14d realized P&L is negative — risk and sizing review",
                {
                    "window_days": 14,
                    "realized_pnl": pnl,
                    "closed_trades": trades,
                    "hint": "Check drawdown vs limits; consider reducing position size or pausing in weak regimes.",
                },
                {},
            )

    return new_ids


async def list_recommendations(
    strategy_id: str | None,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT id, strategy_id, from_version, recommendation_code, proposed_title,
               rationale_json, proposed_details_patch, status,
               created_at, updated_at, approved_by, approved_at, implemented_version
        FROM s004_strategy_evolution_recommendations
        WHERE ($1::text IS NULL OR strategy_id = $1)
          AND ($2::text IS NULL OR status = $2)
        ORDER BY created_at DESC
        LIMIT $3
        """,
        strategy_id,
        status,
        limit,
    )
    return [dict(r) for r in rows]


async def list_changelog(strategy_id: str | None, limit: int) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT id, strategy_id, from_version, to_version, summary, changelog_md,
               recommendation_id, created_by, created_at
        FROM s004_strategy_version_changelog
        WHERE ($1::text IS NULL OR strategy_id = $1)
        ORDER BY created_at DESC
        LIMIT $2
        """,
        strategy_id,
        limit,
    )
    return [dict(r) for r in rows]
