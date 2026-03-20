#!/usr/bin/env python3
"""Add charges_per_trade column if missing. Run from backend dir: python scripts/add_charges_per_trade.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    from dotenv import load_dotenv
    load_dotenv()
    import asyncpg
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set in .env")
        sys.exit(1)
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            ALTER TABLE IF EXISTS s004_user_master_settings
            ADD COLUMN IF NOT EXISTS charges_per_trade NUMERIC(10,2) NOT NULL DEFAULT 20
        """)
        print("Added charges_per_trade column (or it already existed).")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
