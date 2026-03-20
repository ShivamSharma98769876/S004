"""Verify strategy_details_json in database. Run: python scripts/verify_strategy_json.py"""
import asyncio
import json
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
        row = await conn.fetchrow(
            "SELECT strategy_details_json FROM s004_strategy_catalog WHERE strategy_id = $1 AND version = $2",
            "strat-trendsnap-momentum",
            "1.0.0",
        )
        if row:
            val = row["strategy_details_json"]
            if isinstance(val, str):
                val = json.loads(val or "{}")
            print(json.dumps(val, indent=2))
        else:
            print("No strategy found.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
