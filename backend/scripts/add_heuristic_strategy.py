"""Add Multi-Heuristic Strike Selector strategy to s004_strategy_catalog.

Run from backend dir: python scripts/add_heuristic_strategy.py

After running, the strategy will appear in Marketplace for Admin to enable.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HEURISTIC_STRATEGY_DETAILS = {
    "strategyType": "heuristic-voting",
    "displayName": "Multi-Heuristic Strike Selector",
    "description": "Weighted scoring from multiple heuristics. Each heuristic scores 1-5; weighted average produces final score. Eligible when score >= 3.0.",
    "heuristics": {
        "oiBuildup": {"enabled": True, "weight": 1.2},
        "ivr": {"enabled": True, "weight": 1.0},
        "volumeSpike": {"enabled": True, "weight": 1.0},
        "rsi": {"enabled": True, "weight": 0.8},
        "emaAlignment": {"enabled": True, "weight": 0.9},
        "primaryVwap": {"enabled": True, "weight": 1.0},
        "deltaFit": {"enabled": True, "weight": 0.8},
        "oiChange": {"enabled": True, "weight": 0.7},
        "ltpChange": {"enabled": True, "weight": 0.6},
    },
    "strikeSelection": {
        "minOi": 10000,
        "minVolume": 500,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.35,
        "deltaPreferredPE": -0.35,
        "description": "Liquidity: min OI 10k, min volume 500.",
    },
    "heuristicEnhancements": {
        "enabled": True,
        "maxMoneynessPct": 1.2,
        "moneynessOverrideMinScore": 4.5,
        "flatSpotBandPct": 0.08,
        "flatOiPct": 0.5,
        "volumeHighRatio": 1.5,
        "oiChurnAbsPct": 0.35,
        "churnScoreMultiplier": 0.94,
        "ltpStrongPct": 2.0,
        "oiWeightWhenLtpStrong": 0.45,
        "maxLtpOiCombinedWeightShare": 0.88,
        "jointMinMult": 0.72,
        "jointMaxMult": 1.08,
        "bestPerSideMinGap": 0.35,
        "singleDirectionOnly": False,
        "singleDirectionMinSpread": 0.4,
        "ceRequiresSpotNotDown": False,
        "peRequiresSpotNotUp": False,
        "directionalGateFlatBandPct": 0.05,
        "description": "Moneyness hard cap, DTE×moneyness matrix, spot×OI joint multipliers, volume/OI churn dampening, LTP/OI decorrelation, one best CE and one best PE.",
    },
    "scoreThreshold": 3.0,
    "scoreMax": 5.0,
    "autoTradeScoreThreshold": 3.5,
    "scoreDescription": "Weighted average of 9 heuristics. Signal when score >= 3.0.",
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
        # Get admin or first user id for created_by
        admin_row = await conn.fetchrow(
            "SELECT id FROM s004_users WHERE username = 'admin' LIMIT 1"
        )
        if admin_row:
            created_by = admin_row["id"]
        else:
            any_row = await conn.fetchrow("SELECT id FROM s004_users ORDER BY id LIMIT 1")
            created_by = any_row["id"] if any_row else 1

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
            )
            VALUES (
                'strat-heuristic-voting',
                '1.0.0',
                'Multi-Heuristic Strike Selector',
                'Weighted scoring from multiple heuristics: OI buildup, IVR, volume spike, RSI, EMA, VWAP, delta fit, OI change, LTP change. No single rule dominates.',
                'MEDIUM',
                'ADMIN',
                'PUBLISHED',
                ARRAY['PAPER', 'LIVE'],
                ARRAY['NIFTY', 'BANKNIFTY', 'FINNIFTY'],
                '{"win_rate_30d": 0, "pnl_30d": 0}'::jsonb,
                $1::jsonb,
                $2
            )
            ON CONFLICT (strategy_id, version) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                risk_profile = EXCLUDED.risk_profile,
                publish_status = EXCLUDED.publish_status,
                strategy_details_json = EXCLUDED.strategy_details_json,
                updated_at = NOW()
            """,
            json.dumps(HEURISTIC_STRATEGY_DETAILS),
            created_by,
        )
        print("Multi-Heuristic Strike Selector added to Marketplace.")
        print("Refresh Marketplace page to see the new strategy.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
