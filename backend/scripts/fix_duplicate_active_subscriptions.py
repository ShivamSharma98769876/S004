"""Pause duplicate ACTIVE marketplace rows for the same user + strategy_id.

When two versions (e.g. Nifty IVR 1.1.0 and 1.2.0) are both ACTIVE, recommendation
evaluation can pick the wrong one (ORDER BY settings.updated_at). This script keeps
only the highest semantic-looking version ACTIVE and PAUSEs the rest.

Run from the backend directory:

    python scripts/fix_duplicate_active_subscriptions.py
    python scripts/fix_duplicate_active_subscriptions.py --dry-run
    python scripts/fix_duplicate_active_subscriptions.py --strategy-id strat-nifty-ivr-trend-short

Requires DATABASE_URL in .env (same as other scripts).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _version_key(ver: str) -> tuple:
    """Sortable key for simple x.y.z style versions (best-effort)."""
    parts: list[int | str] = []
    for seg in str(ver).strip().split("."):
        seg = seg.strip()
        if seg.isdigit():
            parts.append(int(seg))
        else:
            digits = "".join(c for c in seg if c.isdigit())
            parts.append(int(digits) if digits else seg)
    return tuple(parts)


async def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    import asyncpg

    parser = argparse.ArgumentParser(description="Dedupe ACTIVE strategy_subscriptions per user.")
    parser.add_argument("--dry-run", action="store_true", help="Print actions only; no DB writes.")
    parser.add_argument(
        "--strategy-id",
        default=None,
        help="Limit to this strategy_id (default: all strategies with duplicates).",
    )
    args = parser.parse_args()

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        sys.exit(1)

    conn = await asyncpg.connect(db_url)
    try:
        q = """
            SELECT user_id, strategy_id, strategy_version
            FROM s004_strategy_subscriptions
            WHERE status = 'ACTIVE'
            ORDER BY user_id, strategy_id, strategy_version
        """
        rows = await conn.fetch(q)
        by_pair: dict[tuple[int, str], list[str]] = {}
        for r in rows:
            uid = int(r["user_id"])
            sid = str(r["strategy_id"])
            ver = str(r["strategy_version"])
            if args.strategy_id and sid != args.strategy_id:
                continue
            by_pair.setdefault((uid, sid), []).append(ver)

        to_pause: list[tuple[int, str, str]] = []
        for (uid, sid), versions in by_pair.items():
            if len(versions) < 2:
                continue
            sorted_v = sorted(set(versions), key=_version_key)
            keep = sorted_v[-1]
            for v in sorted_v[:-1]:
                to_pause.append((uid, sid, v))

        if not to_pause:
            print("No duplicate ACTIVE subscriptions found (for filter). Nothing to do.")
            return

        keep_map: dict[tuple[int, str], str] = {}
        for (uid, sid), versions in by_pair.items():
            if len(versions) < 2:
                continue
            sorted_v = sorted(set(versions), key=_version_key)
            keep_map[(uid, sid)] = sorted_v[-1]

        print(f"Found {len(to_pause)} subscription row(s) to PAUSE (keeping newest version per user+strategy).")
        for uid, sid, ver in to_pause:
            k = keep_map.get((uid, sid), "?")
            print(f"  user_id={uid}  {sid}  @{ver}  -> PAUSED  (keep @{k})")

        if args.dry_run:
            print("(dry-run: no changes written)")
            return

        for uid, sid, ver in to_pause:
            await conn.execute(
                """
                UPDATE s004_strategy_subscriptions
                SET status = 'PAUSED', updated_at = NOW()
                WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3 AND status = 'ACTIVE'
                """,
                uid,
                sid,
                ver,
            )
        for (uid, sid), keep_ver in keep_map.items():
            await conn.execute(
                """
                UPDATE s004_user_strategy_settings
                SET updated_at = NOW()
                WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
                """,
                uid,
                sid,
                keep_ver,
            )
        print("Done.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
