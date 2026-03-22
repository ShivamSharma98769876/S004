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
STRATEGY_VERSION = "1.0.0"

STRATEGY_DETAILS: dict = {
    "positionIntent": "short_premium",
    "displayName": "Nifty IVR Trend Short",
    "description": (
        "NIFTY spot + chain only. Sells naked PE when spot trend is bullish and naked CE when bearish. "
        "Requires elevated chain IVR (IV rank proxy). Strikes limited to |delta| 0.29-0.35. "
        "Lot size, target points, and stop loss from Settings apply on execution."
    ),
    "indicators": {
        "adx": {
            "period": 14,
            "minThreshold": 20,
            "description": "ADX > 20 on NIFTY spot: skip signals in weak/choppy markets.",
        },
        "ema": {
            "fast": 9,
            "slow": 21,
            "description": "NIFTY spot EMA alignment defines bullish vs bearish regime.",
        },
        "emaCrossover": {
            "bonus": 0,
            "maxCandlesSinceCross": 10,
            "description": "Fresh bullish/bearish EMA cross within last 10 spot candles contributes to score.",
        },
        "ivr": {
            "minThreshold": 55,
            "description": "Per-strike IVR (percentile within same expiry chain) must be >= 55 — elevated IV vs that chain.",
        },
        "rsi": {
            "period": 14,
            "min": 45,
            "max": 75,
            "description": "Bullish spot RSI band for uptrend score; bearish score uses mirrored lower band.",
        },
        "vwap": {"description": "NIFTY spot vs VWAP for trend direction."},
        "volumeSpike": {
            "minRatio": 1.15,
            "description": "Spot volume vs recent average on NIFTY.",
        },
    },
    "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 4,
        "deltaPreferredCE": 0.32,
        "deltaPreferredPE": -0.32,
        "deltaMinAbs": 0.29,
        "deltaMaxAbs": 0.35,
        "description": "Liquid strikes; short leg absolute delta between 0.29 and 0.35.",
    },
    "scoreThreshold": 4,
    "scoreMax": 5,
    "autoTradeScoreThreshold": 4,
    "scoreDescription": (
        "Spot trend score 0-5 on NIFTY (VWAP, EMA, crossover, RSI band, volume). "
        "Bullish regime: sell PE only. Bearish regime: sell CE only. "
        "Leg must have chain IVR >= minThreshold and |delta| in band. Signal when spot score >= 4."
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
                strategy_details_json = COALESCE(
                    EXCLUDED.strategy_details_json,
                    s004_strategy_catalog.strategy_details_json
                ),
                updated_at = NOW()
            """,
            STRATEGY_ID,
            STRATEGY_VERSION,
            "Nifty IVR Trend Short",
            "NIFTY-only naked short options when implied-vol rank (within chain) is elevated and NIFTY spot "
            "trend aligns: sell put in uptrend, sell call in downtrend. |Delta| 0.29-0.35. "
            "Requires margin; not suitable for small accounts.",
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
