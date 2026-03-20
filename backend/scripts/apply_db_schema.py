from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg
from dotenv import load_dotenv


def _read_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


async def apply_schema() -> None:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is missing in environment.")

    migrations_dir = Path(__file__).resolve().parent.parent / "db" / "migrations"
    schema_file = migrations_dir / "functional_core_schema.sql"
    seed_file = migrations_dir / "functional_seed.sql"
    sql = _read_sql(schema_file)
    seed_sql = _read_sql(seed_file)

    conn = await asyncpg.connect(dsn=db_url)
    try:
        # asyncpg can execute a multi-statement script for DDL.
        await conn.execute(sql)
        await conn.execute(seed_sql)
        print(f"Applied schema successfully: {schema_file.name}")
        print(f"Applied seed successfully: {seed_file.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(apply_schema())
