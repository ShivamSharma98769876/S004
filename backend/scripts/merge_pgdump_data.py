"""
Merge rows from a plain-text pg_dump (.sql) into an existing S004 database.

Inserts use ON CONFLICT (id) DO NOTHING so existing rows are kept; only missing
primary keys are added. Run apply_db_schema + auth migration on the target DB first.

Usage (from backend/, DATABASE_URL points at target e.g. remote):
  python scripts/merge_pgdump_data.py db/backups/tradingpro_backup_20260319_171450.sql

By default credentials_json in s004_user_master_settings is set to {} (secrets
from backups must not be committed; rotate broker keys if a dump was ever shared).

  python scripts/merge_pgdump_data.py path/to/backup.sql --keep-credentials
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
from datetime import datetime, time
from pathlib import Path

import asyncpg
from asyncpg import UniqueViolationError
from asyncpg.exceptions import ForeignKeyViolationError
from dotenv import load_dotenv

COPY_RE = re.compile(r"^COPY public\.(\w+) \(([^)]+)\) FROM stdin;$")
SETVAL_RE = re.compile(
    r"SELECT pg_catalog\.setval\('public\.([^']+)',\s*(\d+),\s*(true|false)\);"
)


def _cell(s: str) -> str | None:
    if s == "\\N":
        return None
    return s


def _ts(s: str | None):
    if s is None:
        return None
    if "." in s:
        base, frac = s.rsplit(".", 1)
        if frac.isdigit() and len(frac) < 6:
            s = f"{base}.{(frac + '000000')[:6]}"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"bad timestamp: {s!r}")


def _tm(s: str | None) -> time | None:
    if s is None:
        return None
    for fmt in ("%H:%M:%S", "%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"bad time: {s!r}")


def _pg_text_array(s: str | None) -> list[str] | None:
    if s is None:
        return None
    s = s.strip()
    if not s.startswith("{"):
        return None
    inner = s[1:-1].strip()
    if not inner:
        return []
    return [p.strip() for p in inner.split(",")]


def extract_copy_sections(sql_text: str) -> dict[str, tuple[list[str], list[str]]]:
    """table_name -> (column names, data lines)."""
    lines = sql_text.splitlines()
    out: dict[str, tuple[list[str], list[str]]] = {}
    i = 0
    while i < len(lines):
        m = COPY_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        table = m.group(1)
        cols = [c.strip() for c in m.group(2).split(",")]
        i += 1
        data: list[str] = []
        while i < len(lines):
            if lines[i].strip() == r"\.":
                break
            data.append(lines[i])
            i += 1
        out[table] = (cols, data)
        i += 1
    return out


def extract_setvals(sql_text: str) -> list[tuple[str, int, bool]]:
    found: list[tuple[str, int, bool]] = []
    for m in SETVAL_RE.finditer(sql_text):
        seq = m.group(1)
        val = int(m.group(2))
        is_called = m.group(3) == "true"
        found.append((seq, val, is_called))
    return found


def split_row(line: str, ncols: int) -> list[str | None]:
    parts = line.split("\t")
    if len(parts) != ncols:
        raise ValueError(f"expected {ncols} columns, got {len(parts)}")
    return [_cell(p) for p in parts]


MERGE_TABLE_ORDER = [
    "s004_users",
    "s004_strategy_catalog",
    "s004_strategy_config_versions",
    "s004_user_master_settings",
    "s004_user_strategy_settings",
    "s004_strategy_subscriptions",
    "s004_trade_recommendations",
    "s004_execution_orders",
    "s004_live_trades",
    "s004_trade_events",
    "s004_dashboard_snapshots",
    "s004_option_chain",
    "s004_strike_scores",
]


INSERT_SQL: dict[str, str] = {
    "s004_users": """
        INSERT INTO s004_users (
            id, username, full_name, role, status, created_at, updated_at,
            email, password_hash, approved_paper, approved_live
        ) VALUES ($1,$2,$3,$4,$5,$6::timestamp,$7::timestamp,$8,$9,$10,$11)
        ON CONFLICT (id) DO NOTHING
    """,
    "s004_strategy_catalog": """
        INSERT INTO s004_strategy_catalog (
            id, strategy_id, version, display_name, description, risk_profile,
            owner_type, publish_status, execution_modes, supported_segments,
            performance_snapshot, created_by, created_at, updated_at, strategy_details_json
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9::text[],$10::text[],$11::jsonb,$12,$13::timestamp,$14::timestamp,$15::jsonb
        )
        ON CONFLICT (strategy_id, version) DO NOTHING
    """,
    "s004_strategy_config_versions": """
        INSERT INTO s004_strategy_config_versions (
            id, strategy_id, strategy_version, config_version, config_json,
            active, changed_by, changed_reason, created_at
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::timestamp)
        ON CONFLICT (strategy_id, strategy_version, config_version) DO NOTHING
    """,
    "s004_user_master_settings": """
        INSERT INTO s004_user_master_settings (
            id, user_id, go_live, engine_running, broker_connected,
            shared_api_connected, platform_api_online, mode, max_parallel_trades,
            max_trades_day, max_profit_day, max_loss_day, initial_capital,
            max_investment_per_trade, credentials_json, updated_by, created_at,
            updated_at, charges_per_trade
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,$17::timestamp,$18::timestamp,$19
        )
        ON CONFLICT (user_id) DO NOTHING
    """,
    "s004_user_strategy_settings": """
        INSERT INTO s004_user_strategy_settings (
            id, user_id, strategy_id, strategy_version, lots, lot_size,
            max_strike_distance_atm, max_premium, min_premium, min_entry_strength_pct,
            sl_type, sl_points, breakeven_trigger_pct, target_points, trailing_sl_points,
            timeframe, trade_start, trade_end, enabled_indices, auto_pause_after_losses,
            updated_by, created_at, updated_at, strategy_details_json
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::time,$18::time,
            $19::text[],$20,$21,$22::timestamp,$23::timestamp,$24::jsonb
        )
        ON CONFLICT (user_id, strategy_id, strategy_version) DO NOTHING
    """,
    "s004_strategy_subscriptions": """
        INSERT INTO s004_strategy_subscriptions (
            id, user_id, strategy_id, strategy_version, mode, status,
            user_config, created_at, updated_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::timestamp,$9::timestamp)
        ON CONFLICT (user_id, strategy_id, strategy_version) DO NOTHING
    """,
    "s004_trade_recommendations": """
        INSERT INTO s004_trade_recommendations (
            id, recommendation_id, strategy_id, strategy_version, user_id,
            instrument, expiry, symbol, side, entry_price, target_price,
            stop_loss_price, confidence_score, rank_value, reason_code, status,
            created_at, updated_at, score
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::timestamp,$18::timestamp,$19
        )
        ON CONFLICT (recommendation_id) DO NOTHING
    """,
    "s004_execution_orders": """
        INSERT INTO s004_execution_orders (
            id, order_ref, recommendation_id, user_id, requested_mode, side,
            quantity, requested_price, manual_execute, order_status, broker_order_id,
            order_payload, created_at, updated_at
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13::timestamp,$14::timestamp
        )
        ON CONFLICT (order_ref) DO NOTHING
    """,
    "s004_live_trades": """
        INSERT INTO s004_live_trades (
            id, trade_ref, order_ref, recommendation_id, user_id, strategy_id,
            strategy_version, symbol, mode, side, quantity, entry_price, current_price,
            target_price, stop_loss_price, current_state, realized_pnl, unrealized_pnl,
            opened_at, closed_at, created_at, updated_at, broker_order_id
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19::timestamp,
            $20::timestamp,$21::timestamp,$22::timestamp,$23
        )
        ON CONFLICT (trade_ref) DO NOTHING
    """,
    "s004_trade_events": """
        INSERT INTO s004_trade_events (
            id, trade_ref, event_type, prev_state, next_state, reason_code,
            event_payload, occurred_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::timestamp)
        ON CONFLICT (id) DO NOTHING
    """,
    "s004_dashboard_snapshots": """
        INSERT INTO s004_dashboard_snapshots (
            id, user_id, snapshot_ts, open_trades, closed_trades, gross_pnl,
            net_pnl, unrealized_pnl, win_rate_pct, data_json
        ) VALUES ($1,$2,$3::timestamp,$4,$5,$6,$7,$8,$9,$10::jsonb)
        ON CONFLICT (id) DO NOTHING
    """,
    "s004_option_chain": """
        INSERT INTO s004_option_chain (
            id, instrument, expiry, strike, option_type, ltp, ltp_change_pct,
            volume, open_interest, oi_change_pct, iv_pct, delta, theta, buildup,
            captured_at, created_at
        ) VALUES (
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::timestamp,$16::timestamp
        )
        ON CONFLICT (id) DO NOTHING
    """,
    "s004_strike_scores": """
        INSERT INTO s004_strike_scores (
            id, instrument, expiry, strike, option_type, confidence_score,
            technical_score, volume_score, oi_score, greeks_score, liquidity_score,
            rank_value, cycle_ts, model_version, created_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::timestamp,$14,$15::timestamp)
        ON CONFLICT (id) DO NOTHING
    """,
}


def row_to_args(
    table: str,
    cols: list[str],
    vals: list[str | None],
    *,
    redact_credentials: bool,
) -> tuple | None:
    """Build execute args for INSERT_SQL[table]. Returns None to skip row."""
    v = dict(zip(cols, vals))

    def ibool(x: str | None) -> bool:
        return x == "t"

    try:
        if table == "s004_users":
            return (
                int(v["id"]),
                v["username"],
                v["full_name"],
                v["role"],
                v["status"],
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                v["email"],
                v["password_hash"],
                ibool(v["approved_paper"]),
                ibool(v["approved_live"]),
            )
        if table == "s004_strategy_catalog":
            em = _pg_text_array(v["execution_modes"])
            ss = _pg_text_array(v["supported_segments"])
            return (
                int(v["id"]),
                v["strategy_id"],
                v["version"],
                v["display_name"],
                v["description"],
                v["risk_profile"],
                v["owner_type"],
                v["publish_status"],
                em,
                ss,
                v["performance_snapshot"],
                int(v["created_by"]) if v["created_by"] else None,
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                v["strategy_details_json"],
            )
        if table == "s004_strategy_config_versions":
            return (
                int(v["id"]),
                v["strategy_id"],
                v["strategy_version"],
                int(v["config_version"]),
                v["config_json"],
                ibool(v["active"]),
                int(v["changed_by"]) if v["changed_by"] else None,
                v["changed_reason"],
                _ts(v["created_at"]),
            )
        if table == "s004_user_master_settings":
            cred = v["credentials_json"]
            if redact_credentials:
                cred = "{}"
            return (
                int(v["id"]),
                int(v["user_id"]),
                ibool(v["go_live"]),
                ibool(v["engine_running"]),
                ibool(v["broker_connected"]),
                ibool(v["shared_api_connected"]),
                ibool(v["platform_api_online"]),
                v["mode"],
                int(v["max_parallel_trades"]),
                int(v["max_trades_day"]),
                v["max_profit_day"],
                v["max_loss_day"],
                v["initial_capital"],
                v["max_investment_per_trade"],
                cred,
                int(v["updated_by"]) if v["updated_by"] else None,
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                v["charges_per_trade"],
            )
        if table == "s004_user_strategy_settings":
            ei = _pg_text_array(v["enabled_indices"])
            return (
                int(v["id"]),
                int(v["user_id"]),
                v["strategy_id"],
                v["strategy_version"],
                int(v["lots"]),
                int(v["lot_size"]),
                int(v["max_strike_distance_atm"]),
                v["max_premium"],
                v["min_premium"],
                v["min_entry_strength_pct"],
                v["sl_type"],
                v["sl_points"],
                v["breakeven_trigger_pct"],
                v["target_points"],
                v["trailing_sl_points"],
                v["timeframe"],
                _tm(v["trade_start"]),
                _tm(v["trade_end"]),
                ei,
                int(v["auto_pause_after_losses"]),
                int(v["updated_by"]) if v["updated_by"] else None,
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                v["strategy_details_json"],
            )
        if table == "s004_strategy_subscriptions":
            uc = v["user_config"]
            if uc in (None, "\\N"):
                uc = "{}"
            return (
                int(v["id"]),
                int(v["user_id"]),
                v["strategy_id"],
                v["strategy_version"],
                v["mode"],
                v["status"],
                uc,
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
            )
        if table == "s004_trade_recommendations":
            return (
                int(v["id"]),
                v["recommendation_id"],
                v["strategy_id"],
                v["strategy_version"],
                int(v["user_id"]),
                v["instrument"],
                v["expiry"],
                v["symbol"],
                v["side"],
                v["entry_price"],
                v["target_price"],
                v["stop_loss_price"],
                v["confidence_score"],
                int(v["rank_value"]),
                v["reason_code"],
                v["status"],
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                int(v["score"]) if v["score"] is not None else None,
            )
        if table == "s004_execution_orders":
            return (
                int(v["id"]),
                v["order_ref"],
                v["recommendation_id"],
                int(v["user_id"]),
                v["requested_mode"],
                v["side"],
                int(v["quantity"]),
                v["requested_price"],
                ibool(v["manual_execute"]),
                v["order_status"],
                v["broker_order_id"],
                v["order_payload"],
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
            )
        if table == "s004_live_trades":
            return (
                int(v["id"]),
                v["trade_ref"],
                v["order_ref"],
                v["recommendation_id"],
                int(v["user_id"]),
                v["strategy_id"],
                v["strategy_version"],
                v["symbol"],
                v["mode"],
                v["side"],
                int(v["quantity"]),
                v["entry_price"],
                v["current_price"],
                v["target_price"],
                v["stop_loss_price"],
                v["current_state"],
                v["realized_pnl"],
                v["unrealized_pnl"],
                _ts(v["opened_at"]),
                _ts(v["closed_at"]) if v["closed_at"] else None,
                _ts(v["created_at"]),
                _ts(v["updated_at"]),
                v["broker_order_id"],
            )
        if table == "s004_trade_events":
            return (
                int(v["id"]),
                v["trade_ref"],
                v["event_type"],
                v["prev_state"],
                v["next_state"],
                v["reason_code"],
                v["event_payload"],
                _ts(v["occurred_at"]),
            )
        if table == "s004_dashboard_snapshots":
            return (
                int(v["id"]),
                int(v["user_id"]),
                _ts(v["snapshot_ts"]),
                int(v["open_trades"]),
                int(v["closed_trades"]),
                v["gross_pnl"],
                v["net_pnl"],
                v["unrealized_pnl"],
                v["win_rate_pct"],
                v["data_json"],
            )
        if table == "s004_option_chain":
            return (
                int(v["id"]),
                v["instrument"],
                v["expiry"],
                v["strike"],
                v["option_type"],
                v["ltp"],
                v["ltp_change_pct"],
                v["volume"],
                v["open_interest"],
                v["oi_change_pct"],
                v["iv_pct"],
                v["delta"],
                v["theta"],
                v["buildup"],
                _ts(v["captured_at"]),
                _ts(v["created_at"]),
            )
        if table == "s004_strike_scores":
            return (
                int(v["id"]),
                v["instrument"],
                v["expiry"],
                v["strike"],
                v["option_type"],
                v["confidence_score"],
                v["technical_score"],
                v["volume_score"],
                v["oi_score"],
                v["greeks_score"],
                v["liquidity_score"],
                int(v["rank_value"]),
                _ts(v["cycle_ts"]),
                v["model_version"],
                _ts(v["created_at"]),
            )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"{table}: {e}") from e
    return None


async def merge_file(
    conn: asyncpg.Connection,
    path: Path,
    *,
    redact_credentials: bool,
) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = extract_copy_sections(text)
    for table in MERGE_TABLE_ORDER:
        if table not in sections:
            continue
        cols, lines = sections[table]
        if not lines:
            continue
        sql = INSERT_SQL.get(table)
        if not sql:
            continue
        ncols = len(cols)
        batch: list[tuple] = []
        parse_errors = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                raw = split_row(line, ncols)
            except ValueError as e:
                print(f"  SKIP {table}: {e}", flush=True)
                parse_errors += 1
                continue
            try:
                args = row_to_args(table, cols, raw, redact_credentials=redact_credentials)
            except ValueError as e:
                print(f"  SKIP {table}: {e}", flush=True)
                parse_errors += 1
                continue
            if args is None:
                continue
            batch.append(args)

        if batch:
            try:
                stmt = await conn.prepare(sql)
                await stmt.executemany(batch)
            except (UniqueViolationError, ForeignKeyViolationError) as e:
                print(f"  {table}: batch failed ({e}), falling back to row-by-row", flush=True)
                for args in batch:
                    try:
                        await conn.execute(sql, *args)
                    except UniqueViolationError:
                        pass
                    except ForeignKeyViolationError as fke:
                        print(f"  SKIP {table} (FK): {fke}", flush=True)

        print(
            f"  {table}: upsert batch size {len(batch)}, parse_errors={parse_errors}",
            flush=True,
        )

    print("Done. Rows applied with ON CONFLICT DO NOTHING (existing natural keys kept).", flush=True)

    for seq, val, is_called in extract_setvals(text):
        if not re.match(r"^[a-zA-Z0-9_]+$", seq):
            print(f"  WARN skip setval (bad name): {seq}", flush=True)
            continue
        try:
            await conn.execute(
                f"SELECT setval('public.{seq}', $1, $2)",
                val,
                is_called,
            )
            print(f"  setval public.{seq} -> {val} (is_called={is_called})", flush=True)
        except Exception as e:
            print(f"  WARN setval {seq}: {e}", flush=True)


async def main_async(args: argparse.Namespace) -> None:
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is not set.")
    path = Path(args.dump_file).resolve()
    if not path.is_file():
        raise SystemExit(f"File not found: {path}")

    redact = not args.keep_credentials
    if redact:
        print(
            "Redacting credentials_json in s004_user_master_settings to '{}' "
            "(use --keep-credentials to disable).",
            flush=True,
        )

    conn = await asyncpg.connect(db_url)
    try:
        # No single transaction: alternate unique keys (e.g. strategy_id+version) can fail
        # while PK conflicts are handled by ON CONFLICT; errors must not abort the whole merge.
        await merge_file(conn, path, redact_credentials=redact)
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Merge pg_dump data into existing S004 DB.")
    p.add_argument("dump_file", help="Path to plain .sql pg_dump file")
    p.add_argument(
        "--keep-credentials",
        action="store_true",
        help="Import credentials_json from backup as-is (dangerous if dump is shared).",
    )
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
