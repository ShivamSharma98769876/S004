"""Merge Nifty IVR Trend Short catalog JSON patches (delta/VIX rules + IVR band).

Updates every row in s004_strategy_catalog for strategy_id strat-nifty-ivr-trend-short:
  - indicators.ivr minThreshold / maxLegThreshold / description
  - strikeSelection (shortPremiumDeltaVixBands, deltaOnlyStrikes, deltaMin/MaxAbs, description, asymmetric flag)

Does not replace the full strategy JSON; merges the above keys only.

Run from the backend directory:

    python scripts/patch_nifty_ivr_short_delta_vix_bands.py

Optional:

    python scripts/patch_nifty_ivr_short_delta_vix_bands.py --dry-run

Requires DATABASE_URL (e.g. backend/.env loaded via python-dotenv).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from copy import deepcopy
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STRATEGY_ID = "strat-nifty-ivr-trend-short"

# India VIX threshold: above → tighter deltas; at or below → wider deltas.
SHORT_PREMIUM_DELTA_VIX_BANDS: dict[str, Any] = {
    "threshold": 17,
    "vixAbove": {
        "deltaMinCE": 0.29,
        "deltaMaxCE": 0.35,
        "deltaMinPE": -0.35,
        "deltaMaxPE": -0.29,
    },
    "vixAtOrBelow": {
        "deltaMinCE": 0.32,
        "deltaMaxCE": 0.38,
        "deltaMinPE": -0.38,
        "deltaMaxPE": -0.32,
    },
}

STRIKE_DESCRIPTION = (
    "India VIX first; shortPremiumDeltaOnlyStrikes=true → strikes gated by VIX-resolved CE/PE delta only "
    "(not maxOtmSteps/dATM). VIX>17 → CE +0.29..+0.35, PE -0.35..-0.29; VIX≤17 → CE +0.32..+0.38, PE -0.38..-0.32. "
    "Missing VIX → deltaMinAbs/deltaMaxAbs. Diagnostics default to legs in that delta band only "
    "(env S004_SHORT_DIAGNOSTICS_INCLUDE_OOB_DELTA=1 for full chain). "
    "±strikes/side floor 12 (S004_SHORT_PREMIUM_DELTA_ONLY_STRIKES_EACH_SIDE). DTE≥2; Tue weekly; min gamma; no min OI/vol."
)

IVR_BLOCK: dict[str, Any] = {
    "minThreshold": 40,
    "maxLegThreshold": 65,
    "description": "Per-strike chain IVR must be between minThreshold and maxLegThreshold (inclusive).",
}


def _merge_nifty_ivr_short_json(details: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(details)
    strike = out.get("strikeSelection")
    if not isinstance(strike, dict):
        strike = {}
        out["strikeSelection"] = strike
    strike["deltaMinAbs"] = 0.29
    strike["deltaMaxAbs"] = 0.35
    strike["shortPremiumDeltaVixBands"] = deepcopy(SHORT_PREMIUM_DELTA_VIX_BANDS)
    strike["shortPremiumDeltaOnlyStrikes"] = True
    if "shortPremiumAsymmetricDatm" not in strike:
        strike["shortPremiumAsymmetricDatm"] = False
    strike["description"] = STRIKE_DESCRIPTION
    ind = out.get("indicators")
    if not isinstance(ind, dict):
        ind = {}
        out["indicators"] = ind
    ind["ivr"] = deepcopy(IVR_BLOCK)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="Patch Nifty IVR Trend Short strikeSelection (VIX delta bands).")
    parser.add_argument("--dry-run", action="store_true", help="Print changes only; do not write to DB.")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
    except ImportError:
        print("Install python-dotenv: pip install python-dotenv")
        sys.exit(1)
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set (check backend/.env).")
        sys.exit(1)

    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            ALTER TABLE IF EXISTS s004_strategy_catalog
            ADD COLUMN IF NOT EXISTS strategy_details_json JSONB
            """
        )
        rows = await conn.fetch(
            """
            SELECT strategy_id, version, strategy_details_json
            FROM s004_strategy_catalog
            WHERE strategy_id = $1
            ORDER BY version
            """,
            STRATEGY_ID,
        )
        if not rows:
            print(f"No catalog rows for {STRATEGY_ID}. Run seed_nifty_ivr_trend_short_strategy.py first.")
            sys.exit(1)

        for row in rows:
            ver = row["version"]
            raw = row["strategy_details_json"]
            if raw is None:
                print(f"Skip {STRATEGY_ID} @{ver}: strategy_details_json is null")
                continue
            if isinstance(raw, str):
                details = json.loads(raw)
            elif isinstance(raw, dict):
                details = dict(raw)
            else:
                print(f"Skip {STRATEGY_ID} @{ver}: unexpected JSON type {type(raw)}")
                continue

            merged = _merge_nifty_ivr_short_json(details)
            if args.dry_run:
                print(f"[dry-run] Would update {STRATEGY_ID} @{ver}")
                print("indicators.ivr:", json.dumps(merged.get("indicators", {}).get("ivr", {}), indent=2))
                print("strikeSelection:", json.dumps(merged.get("strikeSelection", {}), indent=2))
                continue

            await conn.execute(
                """
                UPDATE s004_strategy_catalog
                SET strategy_details_json = $1::jsonb, updated_at = NOW()
                WHERE strategy_id = $2 AND version = $3
                """,
                json.dumps(merged),
                STRATEGY_ID,
                ver,
            )
            print(f"Updated {STRATEGY_ID} @{ver}")
    finally:
        await conn.close()

    if args.dry_run:
        print("Dry run complete; no database writes.")
    else:
        print("Done. Restart the API to pick up in-memory caches if any.")


if __name__ == "__main__":
    asyncio.run(main())
