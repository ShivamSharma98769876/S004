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


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "y", "on"}


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
    trendsnap_rsi_early_1030_file = migrations_dir / "trendsnap_rsi50_early_session_1030.sql"
    trendsnap_disable_volume_gate_file = migrations_dir / "trendsnap_disable_volume_leg_score.sql"
    nifty_ivr_relax_file = migrations_dir / "nifty_ivr_trend_short_relax.sql"
    nifty_ivr_fewer_sls_file = migrations_dir / "nifty_ivr_trend_short_fewer_sls_1_2_0.sql"
    nifty_ivr_tolerance_file = migrations_dir / "nifty_ivr_trend_short_entry_tolerance.sql"
    chain_snapshot_eod_file = migrations_dir / "trade_chain_snapshot_eod_schema.sql"
    broker_multi_file = migrations_dir / "broker_multi_adapter_schema.sql"
    ai_gift_file = migrations_dir / "ai_gift_strategy.sql"
    ai_gift_relax_file = migrations_dir / "ai_gift_rsi_volume_early_session.sql"
    supertrend_trail_file = migrations_dir / "supertrend_trail_strategy.sql"
    stochastic_bnf_file = migrations_dir / "stochastic_bnf_strategy.sql"
    ps_vs_mtf_file = migrations_dir / "ps_vs_mtf_strategy.sql"
    banknifty_lot_file = migrations_dir / "banknifty_lot_size_column.sql"
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
        # Re-seeding on every apply can unexpectedly alter runtime behavior.
        # Default: seed only when users table is empty; allow explicit override via APPLY_SEED=1.
        apply_seed = _env_true("APPLY_SEED")
        if not apply_seed:
            users_count = await conn.fetchval("SELECT COUNT(*) FROM s004_users")
            apply_seed = int(users_count or 0) == 0
        if apply_seed:
            await conn.execute(seed_sql)
            print(f"Applied seed successfully: {seed_file.name}")
        else:
            print(f"Skipped seed (set APPLY_SEED=1 to force): {seed_file.name}")
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
        if trendsnap_rsi_early_1030_file.exists():
            await conn.execute(_read_sql(trendsnap_rsi_early_1030_file))
            print(f"Applied TrendSnap RSI≥50 + early session 10:30 patch: {trendsnap_rsi_early_1030_file.name}")
        if trendsnap_disable_volume_gate_file.exists():
            await conn.execute(_read_sql(trendsnap_disable_volume_gate_file))
            print(f"Applied TrendSnap volume gate disable patch: {trendsnap_disable_volume_gate_file.name}")
        if nifty_ivr_relax_file.exists():
            await conn.execute(_read_sql(nifty_ivr_relax_file))
            print(f"Applied Nifty IVR Trend Short relax: {nifty_ivr_relax_file.name}")
        if nifty_ivr_fewer_sls_file.exists():
            await conn.execute(_read_sql(nifty_ivr_fewer_sls_file))
            print(f"Applied Nifty IVR 1.2.0 liquidity + IVR floor: {nifty_ivr_fewer_sls_file.name}")
        if nifty_ivr_tolerance_file.exists():
            await conn.execute(_read_sql(nifty_ivr_tolerance_file))
            print(f"Applied Nifty IVR entry tolerance (VWAP/EMA/RSI/cross): {nifty_ivr_tolerance_file.name}")
        if chain_snapshot_eod_file.exists():
            await conn.execute(_read_sql(chain_snapshot_eod_file))
            print(f"Applied trade chain snapshot + EOD: {chain_snapshot_eod_file.name}")
        if broker_multi_file.exists():
            await conn.execute(_read_sql(broker_multi_file))
            print(f"Applied broker multi-adapter: {broker_multi_file.name}")
        if ai_gift_file.exists():
            await conn.execute(_read_sql(ai_gift_file))
            print(f"Applied AI Gift strategy: {ai_gift_file.name}")
        if ai_gift_relax_file.exists():
            await conn.execute(_read_sql(ai_gift_relax_file))
            print(f"Applied AI Gift RSI + early session patch: {ai_gift_relax_file.name}")
        if supertrend_trail_file.exists():
            await conn.execute(_read_sql(supertrend_trail_file))
            print(f"Applied SuperTrendTrail strategy: {supertrend_trail_file.name}")
        if stochastic_bnf_file.exists():
            await conn.execute(_read_sql(stochastic_bnf_file))
            print(f"Applied StochasticBNF strategy: {stochastic_bnf_file.name}")
        if ps_vs_mtf_file.exists():
            await conn.execute(_read_sql(ps_vs_mtf_file))
            print(f"Applied PS/VS MTF strategy: {ps_vs_mtf_file.name}")
        if banknifty_lot_file.exists():
            await conn.execute(_read_sql(banknifty_lot_file))
            print(f"Applied Bank Nifty lot size column: {banknifty_lot_file.name}")
        print(f"Applied schema successfully: {schema_file.name}")
        print(f"Applied auth columns: {auth_file.name}")
        if platform_sql:
            print(f"Applied platform risk: {platform_risk_file.name}")
        if landing_fit_sql:
            print(f"Applied landing strategy fit: {landing_fit_file.name}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(apply_schema())
