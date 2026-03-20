"""Add strategy_details_json column to s004_user_strategy_settings.

Run from backend dir: python scripts/add_strategy_details_json.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
            ALTER TABLE IF EXISTS s004_user_strategy_settings
            ADD COLUMN IF NOT EXISTS strategy_details_json JSONB
            """
        )
        print("Added strategy_details_json column to s004_user_strategy_settings.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
