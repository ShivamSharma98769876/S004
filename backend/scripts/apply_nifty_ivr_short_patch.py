"""Merge canonical strikeSelection + indicators for strat-nifty-ivr-trend-short (all catalog versions).

Symmetry: PE regime = CE regime (EMA9 cross below EMA21 + LTP<VWAP) is enforced in Python (option_chain_zerodha).
This script only updates stored strategy_details_json to match seed/SQL.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from copy import deepcopy
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import asyncpg

STRATEGY_ID = "strat-nifty-ivr-trend-short"
SHORT_PREMIUM_DELTA_VIX_BANDS: dict[str, Any] = {
    "threshold": 17,
    "vixAbove": {
        "deltaMinCE": 0.25,
        "deltaMaxCE": 0.40,
        "deltaMinPE": -0.40,
        "deltaMaxPE": -0.25,
    },
    "vixAtOrBelow": {
        "deltaMinCE": 0.25,
        "deltaMaxCE": 0.40,
        "deltaMinPE": -0.40,
        "deltaMaxPE": -0.25,
    },
}
STRIKE_DESCRIPTION = (
    "India VIX first; delta-only strike ladder. VIX bands widened to CE +0.25..+0.40 / PE -0.40..-0.25. "
    "Regime keeps EMA weakness with relaxed VWAP eligibility buffer and no mandatory RSI decreasing check. "
    "±strikes/side floor 12 (env S004_SHORT_PREMIUM_DELTA_ONLY_STRIKES_EACH_SIDE). DTE≥2; Tue weekly; min gamma; three_factor + skew/PCR."
)
IVR_BLOCK: dict[str, Any] = {
    "minThreshold": 40,
    "maxLegThreshold": 100,
    "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive).",
}
RSI_BLOCK: dict[str, Any] = {
    "period": 14,
    "min": 0,
    "max": 100,
    "description": "Option-leg RSI on LTP series (period). Leg RSI must be below shortPremiumRsiBelow; falling-vs-prior bar is optional via shortPremiumRsiDecreasing.",
}
EMA_CROSS_BLOCK: dict[str, Any] = {
    "bonus": 0,
    "maxCandlesSinceCross": 8,
    "description": "Fresh cross within this many candles on the leg LTP series.",
}
TOP_DESCRIPTION = (
    "NIFTY short premium. Per-leg regime on option LTP: fresh EMA9 cross below EMA21 and premium-weakness context with relaxed "
    "VWAP eligibility buffer. VIX→delta via widened shortPremiumDeltaVixBands. Leg RSI uses shortPremiumRsiBelow without mandatory "
    "falling-vs-prior bar. Per-strike chain IVR in [ivr.minThreshold, maxLegThreshold]."
)
SCORE_DESC = (
    "Symmetric sell CE/PE with widened VIX delta bands. Regime uses EMA weakness and relaxed VWAP eligibility buffer; "
    "RSI must be below shortPremiumRsiBelow without mandatory decreasing filter. "
    "Leg IVR in [ivr.minThreshold, maxLegThreshold]. three_factor technical up to 3 points + skew/PCR bonuses. "
    "Auto-trade at autoTradeScoreThreshold."
)


def merge(details: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(details)
    out["description"] = TOP_DESCRIPTION
    out["scoreDescription"] = SCORE_DESC
    strike = out.get("strikeSelection")
    if not isinstance(strike, dict):
        strike = {}
        out["strikeSelection"] = strike
    strike["deltaMinAbs"] = 0.29
    strike["deltaMaxAbs"] = 0.35
    strike["shortPremiumDeltaVixBands"] = deepcopy(SHORT_PREMIUM_DELTA_VIX_BANDS)
    strike["shortPremiumDeltaOnlyStrikes"] = True
    strike["shortPremiumRsiDirectBand"] = False
    strike["shortPremiumRsiDecreasing"] = False
    strike["shortPremiumVwapEligibleBufferPct"] = 0.3
    strike["shortPremiumThreeFactorRequireLtpBelowVwapForEligible"] = False
    strike["shortPremiumRsiBelow"] = 80
    strike["selectStrikeByMinGamma"] = True
    strike["maxStrikeRecommendations"] = 3
    if "shortPremiumAsymmetricDatm" not in strike:
        strike["shortPremiumAsymmetricDatm"] = False
    strike["description"] = STRIKE_DESCRIPTION
    ind = out.get("indicators")
    if not isinstance(ind, dict):
        ind = {}
        out["indicators"] = ind
    ind["ivr"] = deepcopy(IVR_BLOCK)
    ind["rsi"] = deepcopy(RSI_BLOCK)
    ind["emaCrossover"] = deepcopy(EMA_CROSS_BLOCK)
    return out


async def main() -> None:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL in backend/.env")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            """
            ALTER TABLE IF EXISTS s004_strategy_catalog
            ADD COLUMN IF NOT EXISTS strategy_details_json JSONB
            """
        )
        rows = await conn.fetch(
            "SELECT version, strategy_details_json FROM s004_strategy_catalog WHERE strategy_id = $1 ORDER BY version",
            STRATEGY_ID,
        )
        if not rows:
            raise SystemExit(f"No rows for {STRATEGY_ID}")
        for r in rows:
            raw = r["strategy_details_json"]
            if raw is None:
                print(f"Skip @{r['version']}: null JSON")
                continue
            d = json.loads(raw) if isinstance(raw, str) else dict(raw)
            merged = merge(d)
            await conn.execute(
                "UPDATE s004_strategy_catalog SET strategy_details_json = $1::jsonb, updated_at = NOW() "
                "WHERE strategy_id = $2 AND version = $3",
                json.dumps(merged),
                STRATEGY_ID,
                r["version"],
            )
            print(f"Updated {STRATEGY_ID} @ {r['version']}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
