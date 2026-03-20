#!/usr/bin/env python3
"""Add broker_order_id column to s004_live_trades for Live mode order tracking. Uses DATABASE_URL from .env."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
import asyncpg

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("ERROR: DATABASE_URL not set in .env")
    sys.exit(1)

MIGRATION_SQL = [
    "ALTER TABLE IF EXISTS s004_live_trades ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128)",
    "ALTER TABLE IF EXISTS s004_execution_orders ADD COLUMN IF NOT EXISTS broker_order_id VARCHAR(128)",
]


async def main():
    print("Connecting to database...")
    conn = await asyncpg.connect(DB_URL)
    try:
        for sql in MIGRATION_SQL:
            print(f"  Running: {sql[:60]}...")
            await conn.execute(sql)
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
