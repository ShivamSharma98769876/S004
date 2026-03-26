"""Upsert Nifty IVR Trend Short strategy into catalog + default config version.

Equivalent to db/add_nifty_ivr_trend_short_strategy.sql.

Run from backend directory:
    python scripts/seed_nifty_ivr_trend_short_strategy.py

Requires DATABASE_URL in .env and an admin user (username = 'admin').
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STRATEGY_ID = "strat-nifty-ivr-trend-short"
STRATEGY_VERSION = "1.1.0"

STRATEGY_DETAILS: dict = {
    "strategyType": "rule-based",
    "positionIntent": "short_premium",
    "displayName": "Nifty IVR Trend Short",
    "description": (
        "NIFTY short premium. Regime is per strike on each option leg (not index spot): on that leg LTP series, "
        "fresh EMA9 cross above EMA21 within emaCrossover.maxCandlesSinceCross (default 5) and last close < leg VWAP "
        "→ eligible sell PE. Fresh EMA9 cross below EMA21 and last close < leg VWAP → eligible sell CE. "
        "If both legs qualify at the same strike, the more recent cross wins. Chain IVR must lie between min and max leg "
        "thresholds. No ADX. Option-leg score excludes volume spike. No min OI/volume when both are 0."
    ),
    "spotRegimeMode": "ema_cross_vwap",
    "spotRegimeSatisfiedScore": 5,
    "includeVolumeInLegScore": False,
    "indicators": {
        "ema": {
            "fast": 9,
            "slow": 21,
            "description": "EMA9 vs EMA21 on each option leg LTP series; regime uses a fresh crossover on that leg.",
        },
        "emaCrossover": {
            "bonus": 0,
            "maxCandlesSinceCross": 5,
            "description": "Fresh cross within this many candles on the leg LTP series (default 5 if unset).",
        },
        "ivr": {
            "minThreshold": 30,
            "maxLegThreshold": 55,
            "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive).",
        },
        "rsi": {
            "period": 14,
            "min": 45,
            "max": 85,
            "description": "RSI band for option-leg premium scoring; bearish leg uses mirrored lower band.",
        },
        "vwap": {
            "description": (
                "Leg last close vs leg VWAP: required LTP close < VWAP for both sell-PE and sell-CE regime paths "
                "(spotRegimeMode ema_cross_vwap)."
            ),
        },
    },
    "strikeSelection": {
        "minOi": 0,
        "minVolume": 0,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "minDteCalendarDays": 2,
        "niftyWeeklyExpiryWeekday": "TUE",
        "selectStrikeByMinGamma": True,
        "maxStrikeRecommendations": 1,
        "description": (
            "|delta| 0.29–0.35; DTE >= 2; Tuesday weekly preference. Lowest BS gamma in band. No minimum OI/volume."
        ),
    },
    "scoreThreshold": 3,
    "scoreMax": 4,
    "autoTradeScoreThreshold": 4,
    "scoreDescription": (
        "Strike-leg regime via regimeSellPe / regimeSellCe (EMA9/21 cross + LTP < leg VWAP on that leg; tie-break if both). "
        "No NIFTY spot trend score for this mode. Option leg score up to 4 (VWAP/EMA/cross/RSI; volume spike off). "
        "Leg IVR in [minThreshold, maxLegThreshold]. Auto-trade at autoTradeScoreThreshold."
    ),
}

PERFORMANCE_SNAPSHOT = {"win_rate_30d": 0, "pnl_30d": 0}

DEFAULT_CONFIG: dict = {
    "timeframe": "3-min",
    "min_entry_strength_pct": 0,
    "max_strike_distance_atm": 5,
    "target_points": 10,
    "sl_points": 15,
    "trailing_sl_points": 20,
}


async def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    import asyncpg

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            ALTER TABLE IF EXISTS s004_strategy_catalog
            ADD COLUMN IF NOT EXISTS strategy_details_json JSONB
            """
        )

        admin_id = await conn.fetchval(
            "SELECT id FROM s004_users WHERE username = 'admin' AND status = 'ACTIVE' LIMIT 1"
        )
        if admin_id is None:
            admin_id = await conn.fetchval("SELECT id FROM s004_users WHERE username = 'admin' LIMIT 1")
        if admin_id is None:
            print("No user with username 'admin' found. Create admin first.")
            sys.exit(1)

        details_json = json.dumps(STRATEGY_DETAILS)
        perf_json = json.dumps(PERFORMANCE_SNAPSHOT)
        config_json = json.dumps(DEFAULT_CONFIG)

        await conn.execute(
            """
            INSERT INTO s004_strategy_catalog (
                strategy_id,
                version,
                display_name,
                description,
                risk_profile,
                owner_type,
                publish_status,
                execution_modes,
                supported_segments,
                performance_snapshot,
                strategy_details_json,
                created_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8::text[], $9::text[], $10::jsonb, $11::jsonb, $12
            )
            ON CONFLICT (strategy_id, version) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                risk_profile = EXCLUDED.risk_profile,
                publish_status = EXCLUDED.publish_status,
                execution_modes = EXCLUDED.execution_modes,
                supported_segments = EXCLUDED.supported_segments,
                strategy_details_json = EXCLUDED.strategy_details_json,
                updated_at = NOW()
            """,
            STRATEGY_ID,
            STRATEGY_VERSION,
            "Nifty IVR Trend Short",
            "NIFTY naked short premium: per-strike leg regime (EMA9/21 cross + LTP vs leg VWAP), chain IVR band, "
            "|delta| 0.29–0.35. High risk; margin required.",
            "HIGH",
            "ADMIN",
            "PUBLISHED",
            ["PAPER", "LIVE"],
            ["NIFTY"],
            perf_json,
            details_json,
            admin_id,
        )
        print(f"Upserted s004_strategy_catalog: {STRATEGY_ID} {STRATEGY_VERSION}")

        await conn.execute(
            """
            INSERT INTO s004_strategy_config_versions (
                strategy_id,
                strategy_version,
                config_version,
                config_json,
                active,
                changed_by,
                changed_reason
            ) VALUES ($1, $2, $3, $4::jsonb, TRUE, $5, $6)
            ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING
            """,
            STRATEGY_ID,
            STRATEGY_VERSION,
            1,
            config_json,
            admin_id,
            "NIFTY short premium template",
        )
        print(f"Inserted s004_strategy_config_versions (if missing): {STRATEGY_ID} v{STRATEGY_VERSION} config 1")
        print("Done. Subscribe to this strategy in Marketplace for a user to use it.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
