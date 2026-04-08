"""Apply TrendSnap catalog SQL patches only (no full schema re-seed).

Runs these migrations in order (idempotent / safe to re-run):
  1. trendsnap_flow_ranking.sql          — merge flowRanking into strikeSelection
  2. trendsnap_flow_short_covering_bonus.sql — add shortCoveringBonus if missing
  3. trendsnap_pin_expiry_soft.sql       — add expiry-day pin soft-penalty settings

Prerequisites:
  - PostgreSQL reachable
  - DATABASE_URL in environment or backend/.env (e.g. postgresql://user:pass@host:5432/dbname)

Usage (recommended from the ``backend`` folder):

    python scripts/apply_trendsnap_db_patches.py

Or full schema + all patches (heavier):

    python scripts/apply_db_schema.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

PATCH_FILES = [
    "trendsnap_flow_ranking.sql",
    "trendsnap_flow_short_covering_bonus.sql",
    "trendsnap_pin_expiry_soft.sql",
]


def _read_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


async def main() -> None:
    backend_dir = Path(__file__).resolve().parent.parent
    load_dotenv(backend_dir / ".env")
    load_dotenv()  # cwd .env if present

    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("ERROR: DATABASE_URL is not set. Add it to backend/.env or export it.", file=sys.stderr)
        sys.exit(1)

    migrations_dir = backend_dir / "db" / "migrations"
    conn = await asyncpg.connect(dsn=db_url)
    try:
        for name in PATCH_FILES:
            path = migrations_dir / name
            if not path.exists():
                print(f"SKIP (missing): {name}")
                continue
            await conn.execute(_read_sql(path))
            print(f"OK: {name}")
        print("Done. TrendSnap strategy row updated if strategy_id=strat-trendsnap-momentum version=1.0.0 exists.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
