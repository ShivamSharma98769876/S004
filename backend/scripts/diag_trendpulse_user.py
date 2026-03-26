"""One-off diagnostic: user + auto-trade settings + recommendation row. Run: python scripts/diag_trendpulse_user.py"""
from __future__ import annotations

import asyncio
import json
import sys

from app.db_client import close_db_pool, fetch, fetchrow, init_db_pool


async def main() -> None:
    email = (sys.argv[1] if len(sys.argv) > 1 else "trendpulse@gmail.com").strip()
    symbol = (sys.argv[2] if len(sys.argv) > 2 else "NIFTY2632423000CE").strip().upper()

    await init_db_pool()
    try:
        u = await fetchrow(
            """
            SELECT id, username, email, role, status
            FROM s004_users
            WHERE LOWER(COALESCE(email, '')) = LOWER($1) OR LOWER(username) = LOWER($1)
            """,
            email,
        )
        if not u:
            print("USER_NOT_FOUND:", email)
            return

        uid = int(u["id"])
        print("=== USER ===")
        print(dict(u))

        m = await fetchrow(
            """
            SELECT user_id, go_live, engine_running, broker_connected, shared_api_connected,
                   mode, max_parallel_trades, max_trades_day, max_profit_day, max_loss_day,
                   initial_capital, max_investment_per_trade, updated_at
            FROM s004_user_master_settings
            WHERE user_id = $1
            """,
            uid,
        )
        print("=== MASTER SETTINGS ===")
        print(dict(m) if m else "MISSING_ROW")

        subs = await fetch(
            """
            SELECT strategy_id, strategy_version, mode, status, updated_at
            FROM s004_strategy_subscriptions
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            uid,
        )
        print("=== SUBSCRIPTIONS ===")
        for s in subs:
            print(dict(s))

        ss_rows = await fetch(
            """
            SELECT strategy_id, strategy_version, trade_start, trade_end, max_strike_distance_atm,
                   lot_size, updated_at
            FROM s004_user_strategy_settings
            WHERE user_id = $1
            ORDER BY updated_at DESC
            """,
            uid,
        )
        print("=== USER STRATEGY SETTINGS ===")
        for row in ss_rows:
            print(dict(row))

        rec = await fetchrow(
            """
            SELECT recommendation_id, strategy_id, strategy_version, symbol, status,
                   rank_value, confidence_score, score, created_at, details_json
            FROM s004_trade_recommendations
            WHERE user_id = $1 AND UPPER(symbol) = $2 AND status = 'GENERATED'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            uid,
            symbol,
        )
        print("=== LATEST GENERATED REC ===", symbol)
        if not rec:
            print("None")
        else:
            r = dict(rec)
            dj = r.pop("details_json", None)
            print("row:", json.dumps(r, default=str, indent=2))
            if isinstance(dj, dict):
                subset = {k: dj.get(k) for k in (
                    "signal_eligible", "failed_conditions", "spot_price", "timeframe",
                    "trendpulse", "atm_distance", "score", "ema9", "ema21", "rsi", "vwap",
                ) if k in dj}
                print("details_subset:", json.dumps(subset, default=str, indent=2))
            else:
                print("details_json:", dj)

        open_n = await fetchrow(
            """
            SELECT COUNT(*) AS n
            FROM s004_live_trades
            WHERE user_id = $1 AND current_state <> 'EXIT'
            """,
            uid,
        )
        print("=== OPEN POSITIONS ===", int(open_n["n"] or 0))

        # Effective strategy for _get_user_strategy (JOIN active subscription + settings)
        active = await fetchrow(
            """
            SELECT sset.strategy_id, sset.strategy_version, sub.mode AS sub_mode, sub.status AS sub_status
            FROM s004_user_strategy_settings sset
            JOIN s004_strategy_subscriptions sub
              ON sub.user_id = sset.user_id
             AND sub.strategy_id = sset.strategy_id
             AND sub.strategy_version = sset.strategy_version
            WHERE sset.user_id = $1 AND sub.status = 'ACTIVE'
            ORDER BY sset.updated_at DESC
            LIMIT 1
            """,
            uid,
        )
        print("=== ACTIVE STRATEGY (settings + ACTIVE sub) ===")
        print(dict(active) if active else "None — auto-execute uses fallback subscription path")

        from app.services.platform_risk import (
            evaluate_user_daily_pnl_limits,
            get_platform_trading_paused,
        )
        from app.services.trades_service import get_strategy_score_params

        paused, _pm = await get_platform_trading_paused()
        print("=== PLATFORM ===")
        print({"trading_paused": paused})
        daily_ok, _, daily_msg = await evaluate_user_daily_pnl_limits(uid)
        print("=== DAILY PNL GATE ===", {"ok": daily_ok, "detail": daily_msg})

        sid = str(active["strategy_id"]) if active else "strat-trendpulse-z"
        ver = str(active["strategy_version"]) if active else "1.0.0"
        params = await get_strategy_score_params(sid, ver, uid)
        print("=== EFFECTIVE SCORE PARAMS (catalog + user settings) ===")
        print(
            {
                "strategy_type": params.get("strategy_type"),
                "score_threshold": params.get("score_threshold"),
                "score_max": params.get("score_max"),
                "auto_trade_score_threshold": params.get("auto_trade_score_threshold"),
            }
        )

    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
