"""
Bump strat-trendpulse-z and strat-nifty-ivr-trend-short from catalog version 1.0.0 → 1.1.0.

Mirrors: db/migrations/strategy_bump_trendpulse_ivr_to_1_1_0.sql
Idempotent: safe to re-run.

Run from backend directory:
    python scripts/bump_strategy_versions_trendpulse_ivr_1_1_0.py
    python scripts/bump_strategy_versions_trendpulse_ivr_1_1_0.py --archive-old

Requires DATABASE_URL in .env and asyncpg.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _statements() -> list[str]:
    """SQL statements in order (no comments). Keep in sync with strategy_bump_trendpulse_ivr_to_1_1_0.sql."""
    return [
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
SELECT
    c.strategy_id,
    '1.1.0',
    c.display_name,
    c.description,
    c.risk_profile,
    c.owner_type,
    c.publish_status,
    c.execution_modes,
    c.supported_segments,
    c.performance_snapshot,
    c.strategy_details_json,
    c.created_by
FROM s004_strategy_catalog c
WHERE c.strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND c.version = '1.0.0'
ON CONFLICT (strategy_id, version) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    description = EXCLUDED.description,
    risk_profile = EXCLUDED.risk_profile,
    publish_status = EXCLUDED.publish_status,
    execution_modes = EXCLUDED.execution_modes,
    supported_segments = EXCLUDED.supported_segments,
    performance_snapshot = EXCLUDED.performance_snapshot,
    strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
    updated_at = NOW()
""".strip(),
        """
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    v.strategy_id,
    '1.1.0',
    v.config_version,
    v.config_json,
    v.active,
    v.changed_by,
    'Bump strategy version 1.0.0 → 1.1.0'
FROM s004_strategy_config_versions v
WHERE v.strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND v.strategy_version = '1.0.0'
  AND v.config_version = 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO UPDATE SET
    config_json = EXCLUDED.config_json,
    active = EXCLUDED.active,
    changed_reason = EXCLUDED.changed_reason
""".strip(),
        """
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    'strat-nifty-ivr-trend-short',
    '1.1.0',
    1,
    '{"timeframe": "3-min", "min_entry_strength_pct": 0, "max_strike_distance_atm": 5, "target_points": 10, "sl_points": 15, "trailing_sl_points": 20}'::jsonb,
    TRUE,
    u.id,
    'Bump to 1.1.0 (default execution template)'
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING
""".strip(),
        """
INSERT INTO s004_strategy_config_versions (
    strategy_id,
    strategy_version,
    config_version,
    config_json,
    active,
    changed_by,
    changed_reason
)
SELECT
    'strat-trendpulse-z',
    '1.1.0',
    1,
    '{"timeframe": "3-min", "min_entry_strength_pct": 0, "max_strike_distance_atm": 5, "target_points": 10, "sl_points": 15, "trailing_sl_points": 20}'::jsonb,
    TRUE,
    u.id,
    'Bump to 1.1.0 (default execution template)'
FROM s004_users u
WHERE u.username = 'admin'
LIMIT 1
ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING
""".strip(),
        """
UPDATE s004_strategy_subscriptions
SET strategy_version = '1.1.0', updated_at = NOW()
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0'
""".strip(),
        """
UPDATE s004_user_strategy_settings
SET strategy_version = '1.1.0', updated_at = NOW()
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0'
""".strip(),
        """
DELETE FROM s004_trade_recommendations
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND strategy_version = '1.0.0'
  AND status = 'GENERATED'
""".strip(),
    ]


ARCHIVE_OLD = """
UPDATE s004_strategy_catalog
SET publish_status = 'ARCHIVED', updated_at = NOW()
WHERE strategy_id IN ('strat-trendpulse-z', 'strat-nifty-ivr-trend-short')
  AND version = '1.0.0'
""".strip()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bump TrendPulse Z + Nifty IVR Trend Short to catalog 1.1.0")
    parser.add_argument(
        "--archive-old",
        action="store_true",
        help="Set publish_status=ARCHIVED on catalog rows still at version 1.0.0",
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv()
    import asyncpg

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        for i, stmt in enumerate(_statements(), start=1):
            await conn.execute(stmt)
            print(f"OK step {i}/{len(_statements())}")
        if args.archive_old:
            await conn.execute(ARCHIVE_OLD)
            print("OK archived catalog rows at 1.0.0")
        print("Done. Open Settings and re-save strategy if the UI still shows 1.0.0.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
