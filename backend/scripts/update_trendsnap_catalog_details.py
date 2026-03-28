"""Update strategy_details_json for TrendSnap Momentum (or another id/version) in s004_strategy_catalog.

Uses DATABASE_URL from .env (run from backend/).

Examples:
  python scripts/update_trendsnap_catalog_details.py
  python scripts/update_trendsnap_catalog_details.py --version 1.0.0
  python scripts/update_trendsnap_catalog_details.py --json-file ./my_trendsnap_details.json

After updating, recommendation cache clears on next settings save or wait ~25s per user;
restart API if you need immediate pick-up everywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Canonical catalog JSON (keep in sync with db/migrations/functional_seed.sql TrendSnap block).
TRENDSNAP_CATALOG_DETAILS: dict = {
    "displayName": "TrendSnap Momentum",
    "description": "Four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI 50-75, volume above 1.02x average. RSI must be in band for eligibility (not score-only). NIFTY spot trend must align: bullish for CE, bearish for PE. Early session uses relaxed min contract volume until 10:00 IST. Exits use SL, target, and breakeven from Settings.",
    "requireRsiForEligible": True,
    "longPremiumSpotAlign": True,
    "includeEmaCrossoverInScore": False,
    "strictBullishComparisons": True,
    "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {
            "bonus": 0,
            "maxCandlesSinceCross": 10,
            "description": "Not counted in score; metadata only.",
        },
        "ivr": {
            "bonus": 0,
            "maxThreshold": 20,
            "description": "IVR for reference on the chain; no score bonus.",
        },
        "rsi": {"period": 14, "min": 50, "max": 75, "description": "RSI between 50 and 75 adds one point."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {
            "minRatio": 1.02,
            "description": "Volume strictly above 1.02x recent average adds one point.",
        },
    },
    "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "minVolumeEarlySession": 120,
        "earlySessionEndHourIST": 10,
        "maxStrikeRecommendations": 2,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "description": "Liquidity: min OI 5k, min volume 300 (120 until 10:00 IST). Max 2 eligible strikes per refresh. Max 3 steps OTM. Prefer delta near 0.45 CE / -0.45 PE; rank by score and fit.",
    },
    "scoreThreshold": 3,
    "scoreMax": 4,
    "autoTradeScoreThreshold": 4,
    "scoreDescription": "Primary: latest option close must be above VWAP (otherwise no signal). Score 0-4: +1 VWAP pass, +1 EMA9 above EMA21, +1 RSI 50-75, +1 volume above 1.02x average. No crossover or IVR points. Eligible BUY when score >= 3 AND RSI in 50-75 AND NIFTY spot regime matches leg (bullish/CE, bearish/PE). Auto-execute still requires autoTradeScoreThreshold.",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Update strategy_details_json in s004_strategy_catalog")
    parser.add_argument("--strategy-id", default="strat-trendsnap-momentum", help="Catalog strategy_id")
    parser.add_argument("--version", default="1.0.0", help="Catalog version")
    parser.add_argument(
        "--json-file",
        type=Path,
        default=None,
        help="If set, replace details with this JSON object (file must contain a single JSON object)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print JSON only, do not write DB")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env", file=sys.stderr)
        sys.exit(1)

    if args.json_file:
        raw = args.json_file.read_text(encoding="utf-8")
        details = json.loads(raw)
        if not isinstance(details, dict):
            print("--json-file must contain a JSON object", file=sys.stderr)
            sys.exit(1)
    else:
        details = dict(TRENDSNAP_CATALOG_DETAILS)

    payload = json.dumps(details, ensure_ascii=False)
    if args.dry_run:
        print(payload)
        return

    import asyncpg

    conn = await asyncpg.connect(db_url)
    try:
        n = await conn.execute(
            """
            UPDATE s004_strategy_catalog
            SET strategy_details_json = $1::jsonb, updated_at = NOW()
            WHERE strategy_id = $2 AND version = $3
            """,
            payload,
            args.strategy_id,
            args.version,
        )
        # asyncpg returns e.g. "UPDATE 1"
        print(n)
        row = await conn.fetchrow(
            "SELECT display_name FROM s004_strategy_catalog WHERE strategy_id = $1 AND version = $2",
            args.strategy_id,
            args.version,
        )
        if row:
            print(f"OK: {args.strategy_id} {args.version} ({row.get('display_name')})")
        else:
            print(f"No row for {args.strategy_id} {args.version} — nothing updated.", file=sys.stderr)
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
