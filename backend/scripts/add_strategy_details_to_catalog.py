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
    "description": "Momentum crossover option strategy. Enters when short-term momentum confirms direction with price-action continuation and risk checks; exits use SL, target, and breakeven rules from Settings.",
    "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 > EMA21 = bullish momentum (short-term above long-term)"},
        "emaCrossover": {
            "bonus": 1,
            "maxCandlesSinceCross": 3,
            "description": "Fast EMA crossed above slow EMA from lower to upper = +1 score bonus (bullish crossover). Freshness: cross within last 3 candles.",
        },
        "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI in 50-75 = not overbought, bullish zone"},
        "vwap": {"description": "Price above VWAP = bullish intraday bias"},
        "volumeSpike": {"minRatio": 1.5, "description": "Current volume > 1.5x average = confirmation"},
        "ivr": {"maxThreshold": 20, "bonus": 1, "description": "IVR < 20 = low IV (cheap options) = +1 score bonus. IVR from Option Analytics per strike."},
        "adx": {"period": 14, "minThreshold": 25, "description": "ADX > 25 = strong trend. No signals when ADX < 25 (weak/choppy market)."},
    },
    "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Liquidity: min OI 10k, min volume 500. Max 3 steps OTM to reduce theta decay. Best strike: delta ~0.35 CE / -0.35 PE. Rank by score, volume spike, OI change, delta fit, ATM distance.",
    },
    "scoreThreshold": 3,
    "scoreMax": 6,
    "autoTradeScoreThreshold": 4,
    "scoreDescription": "Score 0-6: Primary(VWAP) + EMA + RSI + Volume + EMA crossover bonus + IVR bonus (when IVR<20). Crossover freshness: cross within 3 candles. ADX filter: no signals when ADX<25. Strike selection: liquidity (min OI/vol), rank by score, volume spike, OI change, delta fit. Signal when score >= 3. Auto-trade when score >= 4.",
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
