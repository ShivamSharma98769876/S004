#!/usr/bin/env python3
"""Run user auth & approval migration (no psql required). Uses DATABASE_URL from .env."""

import asyncio
import os
import sys

# Add backend to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
import asyncpg

load_dotenv()
DB_URL = os.getenv("DATABASE_URL")
if not DB_URL:
    print("ERROR: DATABASE_URL not set in .env")
    sys.exit(1)

MIGRATION_SQL = [
    "ALTER TABLE IF EXISTS s004_users ADD COLUMN IF NOT EXISTS email VARCHAR(255) UNIQUE",
    "ALTER TABLE IF EXISTS s004_users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255)",
    "ALTER TABLE IF EXISTS s004_users ADD COLUMN IF NOT EXISTS approved_paper BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE IF EXISTS s004_users ADD COLUMN IF NOT EXISTS approved_live BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS ix_s004_users_email ON s004_users (email) WHERE email IS NOT NULL",
    "UPDATE s004_users SET approved_paper = TRUE, approved_live = TRUE WHERE role = 'ADMIN'",
]


async def main():
    print("Connecting to database...")
    conn = await asyncpg.connect(DB_URL)
    try:
        for i, sql in enumerate(MIGRATION_SQL, 1):
            print(f"  [{i}/{len(MIGRATION_SQL)}] Running...")
            await conn.execute(sql)
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Migration failed: {e}")
        sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
