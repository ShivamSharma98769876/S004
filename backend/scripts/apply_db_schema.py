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
    evolution_file = migrations_dir / "evolution_schema.sql"
    trade_window_file = migrations_dir / "trade_window_default_0915.sql"
    trendsnap_relax_file = migrations_dir / "trendsnap_liquidity_rsi_relax.sql"
    trendsnap_flow_file = migrations_dir / "trendsnap_flow_ranking.sql"
    trendsnap_sc_bonus_file = migrations_dir / "trendsnap_flow_short_covering_bonus.sql"
    trendsnap_pin_expiry_file = migrations_dir / "trendsnap_pin_expiry_soft.sql"
    nifty_ivr_relax_file = migrations_dir / "nifty_ivr_trend_short_relax.sql"
    nifty_ivr_fewer_sls_file = migrations_dir / "nifty_ivr_trend_short_fewer_sls_1_2_0.sql"
    chain_snapshot_eod_file = migrations_dir / "trade_chain_snapshot_eod_schema.sql"
    broker_multi_file = migrations_dir / "broker_multi_adapter_schema.sql"
    sql = _read_sql(schema_file)
    auth_sql = _read_sql(auth_file)
    seed_sql = _read_sql(seed_file)
    platform_sql = _read_sql(platform_risk_file) if platform_risk_file.exists() else ""
    landing_fit_sql = _read_sql(landing_fit_file) if landing_fit_file.exists() else ""
    decision_log_sql = _read_sql(decision_log_file) if decision_log_file.exists() else ""
    evolution_sql = _read_sql(evolution_file) if evolution_file.exists() else ""
    trade_window_sql = _read_sql(trade_window_file) if trade_window_file.exists() else ""

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
        if evolution_sql:
            await conn.execute(evolution_sql)
            print(f"Applied evolution schema: {evolution_file.name}")
        if trade_window_sql:
            await conn.execute(trade_window_sql)
            print(f"Applied trade window default: {trade_window_file.name}")
        if trendsnap_relax_file.exists():
            await conn.execute(_read_sql(trendsnap_relax_file))
            print(f"Applied TrendSnap catalog patch: {trendsnap_relax_file.name}")
        if trendsnap_flow_file.exists():
            await conn.execute(_read_sql(trendsnap_flow_file))
            print(f"Applied TrendSnap flow ranking patch: {trendsnap_flow_file.name}")
        if trendsnap_sc_bonus_file.exists():
            await conn.execute(_read_sql(trendsnap_sc_bonus_file))
            print(f"Applied TrendSnap short-covering bonus patch: {trendsnap_sc_bonus_file.name}")
        if trendsnap_pin_expiry_file.exists():
            await conn.execute(_read_sql(trendsnap_pin_expiry_file))
            print(f"Applied TrendSnap expiry pin soft penalty patch: {trendsnap_pin_expiry_file.name}")
        if nifty_ivr_relax_file.exists():
            await conn.execute(_read_sql(nifty_ivr_relax_file))
            print(f"Applied Nifty IVR Trend Short relax: {nifty_ivr_relax_file.name}")
        if nifty_ivr_fewer_sls_file.exists():
            await conn.execute(_read_sql(nifty_ivr_fewer_sls_file))
            print(f"Applied Nifty IVR 1.2.0 liquidity + IVR floor: {nifty_ivr_fewer_sls_file.name}")
        if chain_snapshot_eod_file.exists():
            await conn.execute(_read_sql(chain_snapshot_eod_file))
            print(f"Applied trade chain snapshot + EOD: {chain_snapshot_eod_file.name}")
        if broker_multi_file.exists():
            await conn.execute(_read_sql(broker_multi_file))
            print(f"Applied broker multi-adapter: {broker_multi_file.name}")
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
