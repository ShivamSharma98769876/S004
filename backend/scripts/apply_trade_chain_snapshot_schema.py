"""
Apply chain snapshot + strategy EOD tables (idempotent).

Usage (from repo root or backend):
  cd backend
  python scripts/apply_trade_chain_snapshot_schema.py

Requires DATABASE_URL in environment or backend/.env (same as the API).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


async def main() -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    load_dotenv(override=False)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing.")

    sql_path = (
        Path(__file__).resolve().parent.parent
        / "db"
        / "migrations"
        / "trade_chain_snapshot_eod_schema.sql"
    )
    if not sql_path.exists():
        raise FileNotFoundError(sql_path)

    sql = sql_path.read_text(encoding="utf-8")
    conn = await asyncpg.connect(dsn=db_url)
    try:
        await conn.execute(sql)
        print(f"Applied: {sql_path.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
