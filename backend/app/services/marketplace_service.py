from __future__ import annotations

import json
from datetime import time

from app.db_client import execute, fetch, fetchrow


def _strategy_explainer(strategy_id: str, display_name: str, description: str) -> str:
    sid = strategy_id.lower()
    name = display_name.lower()
    if "trendsnap" in sid or "trendsnap" in name:
        return (
            "TrendSnap Momentum enters option trades when short-term momentum confirms direction "
            "with price-action continuation and risk checks; eligible strikes rank using the same CE/PE flow read as "
            "the landing page plus OI, volume, and OI change. Exits use SL, target, and breakeven rules from your settings."
        )
    if "ivr-trend-short" in sid or "ivr trend short" in name:
        return (
            "Nifty IVR Trend Short sells naked NIFTY puts in spot uptrends and naked calls in spot "
            "downtrends when chain IV rank is elevated; strikes target |delta| 0.29-0.35. "
            "Margin required; lot size, target, and stop loss come from your settings."
        )
    if "vwap" in sid or "vwap" in name:
        return (
            "VWAP Pullback waits for price to retrace near VWAP and rejoin trend direction. "
            "It aims to avoid chasing spikes and focuses on structured intraday continuation."
        )
    if "ai-gift" in sid or "ai gift" in name:
        return (
            "AI Gift combines live option-chain micro-structure (OI, OI change, IVR, VWAP, RSI, delta, volume) "
            "with directional context to rank high-quality long/short strike candidates. "
            "When eligible, signals appear in Dashboard and can auto-execute per your Paper/Live settings."
        )
    if "momentum" in sid or "oi" in sid or "momentum" in name:
        return (
            "Momentum + OI Spike ranks strikes where momentum aligns with open-interest behavior, "
            "then prioritizes higher-confidence entries."
        )
    return description or "Strategy logic configured by admin with runtime risk controls from settings."


def _parse_details(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}
    return {}


async def list_strategies_for_user(
    user_id: int,
    risk: str | None,
    status: str | None,
    sort_by: str,
    sort_dir: str,
    limit: int,
    offset: int,
) -> list[dict]:
    """Marketplace rows: 30d PnL and win rate use this user's closed trades in the last 30 days when at
    least one EXIT exists for that strategy; otherwise catalog ``performance_snapshot`` (seed/demo).
    """
    sort_map = {
        "updated_at": "sub.catalog_updated_at",
        "pnl_30d": "sub.pnl_30d",
        "win_rate": "sub.win_rate",
    }
    order_col = sort_map.get(sort_by, "sub.catalog_updated_at")
    order_dir = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    where_parts = ["c.publish_status = 'PUBLISHED'"]
    args: list[object] = [user_id]
    argn = 2
    if risk:
        where_parts.append(f"c.risk_profile = ${argn}")
        args.append(risk.upper())
        argn += 1

    where_sql = " AND ".join(where_parts)
    rows = await fetch(
        f"""
        SELECT * FROM (
            SELECT
                c.strategy_id,
                c.version,
                c.display_name,
                COALESCE(c.description, '') AS description,
                c.strategy_details_json,
                us.strategy_details_json AS user_strategy_details_json,
                c.risk_profile,
                c.publish_status,
                CASE
                    WHEN COALESCE(u30.n_exit, 0) > 0 THEN u30.sum_pnl
                    ELSE COALESCE((c.performance_snapshot->>'pnl_30d')::numeric, 0)
                END AS pnl_30d,
                CASE
                    WHEN COALESCE(u30.n_exit, 0) > 0 THEN
                        ROUND((100.0 * u30.n_win::numeric / NULLIF(u30.n_exit, 0)), 2)
                    ELSE COALESCE((c.performance_snapshot->>'win_rate_30d')::numeric, 0)
                END AS win_rate,
                COALESCE(s.status, 'NOT_SUBSCRIBED') AS subscription_status,
                c.updated_at AS catalog_updated_at
            FROM s004_strategy_catalog c
            LEFT JOIN s004_strategy_subscriptions s
                ON s.user_id = $1 AND s.strategy_id = c.strategy_id AND s.strategy_version = c.version
            LEFT JOIN s004_user_strategy_settings us
                ON us.user_id = $1 AND us.strategy_id = c.strategy_id AND us.strategy_version = c.version
            LEFT JOIN (
                SELECT
                    strategy_id,
                    strategy_version,
                    SUM(realized_pnl)::numeric AS sum_pnl,
                    COUNT(*)::bigint AS n_exit,
                    COUNT(*) FILTER (WHERE realized_pnl > 0)::bigint AS n_win
                FROM s004_live_trades
                WHERE user_id = $1
                  AND current_state = 'EXIT'
                  AND closed_at IS NOT NULL
                  AND closed_at >= (NOW() - INTERVAL '30 days')
                GROUP BY strategy_id, strategy_version
            ) u30 ON u30.strategy_id = c.strategy_id AND u30.strategy_version = c.version
            WHERE {where_sql}
        ) sub
        ORDER BY {order_col} {order_dir}, sub.catalog_updated_at DESC
        LIMIT ${argn} OFFSET ${argn + 1}
        """,
        *args,
        limit,
        offset,
    )
    out = []
    for r in rows:
        row_status = str(r["subscription_status"])
        if status and row_status != status.upper():
            continue
        details = _parse_details(r.get("strategy_details_json")) or None
        user_details = _parse_details(r.get("user_strategy_details_json"))
        pi_raw = str(user_details.get("tradeActionIntent") or user_details.get("positionIntent") or "").strip().lower()
        position_intent = pi_raw if pi_raw in {"long_premium", "short_premium"} else "long_premium"
        try:
            pnl_30 = float(r["pnl_30d"] or 0)
        except (TypeError, ValueError):
            pnl_30 = 0.0
        try:
            win_r = float(r["win_rate"] or 0)
        except (TypeError, ValueError):
            win_r = 0.0
        out.append(
            {
                "strategy_id": r["strategy_id"],
                "version": r["version"],
                "display_name": r["display_name"],
                "description": r["description"],
                "strategy_details": details,
                "strategy_explainer": _strategy_explainer(r["strategy_id"], r["display_name"], r["description"]),
                "risk_profile": r["risk_profile"],
                "status": row_status,
                "publish_status": r["publish_status"],
                "position_intent": position_intent,
                "pnl_30d": pnl_30,
                "win_rate": win_r,
            }
        )
    return out


async def ensure_user_strategy_settings(user_id: int, strategy_id: str, strategy_version: str) -> None:
    """Ensure s004_user_strategy_settings exists for this strategy. Required for recommendation generation - without it,
    _get_user_strategy falls back to a different strategy or default, so subscribed strategy won't get recommendations."""
    existing = await fetchrow(
        """
        SELECT id FROM s004_user_strategy_settings
        WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
        """,
        user_id,
        strategy_id,
        strategy_version,
    )
    if existing:
        return
    cfg = await fetchrow(
        """
        SELECT config_json FROM s004_strategy_config_versions
        WHERE strategy_id = $1 AND strategy_version = $2 AND active = TRUE
        ORDER BY config_version DESC LIMIT 1
        """,
        strategy_id,
        strategy_version,
    )
    raw = cfg.get("config_json") if cfg else {}
    default_json = raw if isinstance(raw, dict) else {}

    def def_val(k: str, d: object) -> object:
        v = default_json.get(k, d)
        return d if v is None else v

    await execute(
        """
        INSERT INTO s004_user_strategy_settings (
            user_id, strategy_id, strategy_version, lots, lot_size, banknifty_lot_size, max_strike_distance_atm,
            max_premium, min_premium, min_entry_strength_pct, sl_type, sl_points,
            breakeven_trigger_pct, target_points, trailing_sl_points, timeframe,
            trade_start, trade_end, enabled_indices, auto_pause_after_losses, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::time,$18::time,$19,$20,NOW())
        ON CONFLICT (user_id, strategy_id, strategy_version) DO NOTHING
        """,
        user_id,
        strategy_id,
        strategy_version,
        int(def_val("lots", 1)),
        int(def_val("lot_size", 65)),
        int(def_val("banknifty_lot_size", 30)),
        int(def_val("max_strike_distance_atm", 5)),
        float(def_val("max_premium", 200)),
        float(def_val("min_premium", 30)),
        float(def_val("min_entry_strength_pct", 0)),
        str(def_val("sl_type", "Fixed Points")),
        float(def_val("sl_points", 15)),
        float(def_val("breakeven_trigger_pct", 50)),
        float(def_val("target_points", 10)),
        float(def_val("trailing_sl_points", 20)),
        str(def_val("timeframe", "3-min")),
        time(9, 15, 0),
        time(15, 0, 0),
        ["NIFTY"],
        int(def_val("auto_pause_after_losses", 3)),
    )


async def upsert_subscription(
    user_id: int,
    strategy_id: str,
    strategy_version: str,
    mode: str,
    status: str,
) -> None:
    await execute(
        """
        INSERT INTO s004_strategy_subscriptions (
            user_id, strategy_id, strategy_version, mode, status, user_config, created_at, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,'{}'::jsonb,NOW(),NOW())
        ON CONFLICT (user_id, strategy_id, strategy_version) DO UPDATE SET
            mode = EXCLUDED.mode,
            status = EXCLUDED.status,
            updated_at = NOW()
        """,
        user_id,
        strategy_id,
        strategy_version,
        mode,
        status,
    )
    if status == "ACTIVE":
        # Only one ACTIVE row per (user, strategy_id): otherwise _get_user_strategy can pick an older
        # version via ORDER BY user_strategy_settings.updated_at and recommendations/logs stay on stale JSON.
        await execute(
            """
            UPDATE s004_strategy_subscriptions
            SET status = 'PAUSED', updated_at = NOW()
            WHERE user_id = $1 AND strategy_id = $2 AND strategy_version <> $3 AND status = 'ACTIVE'
            """,
            user_id,
            strategy_id,
            strategy_version,
        )
        await execute(
            """
            UPDATE s004_user_strategy_settings
            SET updated_at = NOW()
            WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
            """,
            user_id,
            strategy_id,
            strategy_version,
        )
        await ensure_user_strategy_settings(user_id, strategy_id, strategy_version)
