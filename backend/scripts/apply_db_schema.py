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
    auth_file = migrations_dir / "user_auth_approval_schema.sql"
    seed_file = migrations_dir / "functional_seed.sql"
    platform_risk_file = migrations_dir / "platform_risk_schema.sql"
    landing_fit_file = migrations_dir / "landing_strategy_fit_schema.sql"
    decision_log_file = migrations_dir / "trade_decision_log_schema.sql"
    sql = _read_sql(schema_file)
    auth_sql = _read_sql(auth_file)
    seed_sql = _read_sql(seed_file)
    platform_sql = _read_sql(platform_risk_file) if platform_risk_file.exists() else ""
    landing_fit_sql = _read_sql(landing_fit_file) if landing_fit_file.exists() else ""
    decision_log_sql = _read_sql(decision_log_file) if decision_log_file.exists() else ""

    conn = await asyncpg.connect(dsn=db_url)
    try:
        # asyncpg can execute a multi-statement script for DDL.
        await conn.execute(sql)
        await conn.execute(auth_sql)
        await conn.execute(seed_sql)
        if platform_sql:
            await conn.execute(platform_sql)
        if landing_fit_sql:
            await conn.execute(landing_fit_sql)
        if decision_log_sql:
            await conn.execute(decision_log_sql)
            print(f"Applied trade decision log: {decision_log_file.name}")
        print(f"Applied schema successfully: {schema_file.name}")
        print(f"Applied auth columns: {auth_file.name}")
        print(f"Applied seed successfully: {seed_file.name}")
        if platform_sql:
            print(f"Applied platform risk: {platform_risk_file.name}")
        if landing_fit_sql:
            print(f"Applied landing strategy fit: {landing_fit_file.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(apply_schema())
