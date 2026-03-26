"""Add strategy_details_json column to s004_strategy_catalog and seed TrendSnap.

Run from backend dir: python scripts/add_strategy_details_to_catalog.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_DETAILS = {
    "displayName": "TrendSnap Momentum",
    "description": "Simple four-factor option read: close above VWAP (gate), EMA9 above EMA21, RSI 50-75, volume above 1.1x average. Signal when at least three of four pass.",
    "includeEmaCrossoverInScore": False,
    "strictBullishComparisons": True,
    "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {
            "bonus": 0,
            "maxCandlesSinceCross": 3,
            "description": "Not counted in score; metadata only.",
        },
        "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI between 50 and 75 adds one point."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {"minRatio": 1.1, "description": "Volume strictly above 1.1x recent average adds one point."},
        "ivr": {"maxThreshold": 20, "bonus": 0, "description": "IVR for reference; no score bonus."},
    },
    "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Liquidity: min OI 5k, min volume 300. Max 3 steps OTM. Prefer delta near 0.35 CE / -0.35 PE.",
    },
    "scoreThreshold": 3,
    "scoreMax": 4,
    "autoTradeScoreThreshold": 4,
    "scoreDescription": "Primary: close must be above VWAP. Score 0-4: VWAP, EMA9>EMA21, RSI 50-75, volume>1.1x avg. No crossover or IVR points. BUY CE/PE when score >= 3.",
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
        await conn.execute(
            """
            UPDATE s004_strategy_catalog
            SET strategy_details_json = $1::jsonb, updated_at = NOW()
            WHERE strategy_id = 'strat-trendsnap-momentum' AND version = '1.0.0'
            """,
            json.dumps(DEFAULT_DETAILS),
        )
        print("Added strategy_details_json to s004_strategy_catalog and seeded TrendSnap.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
