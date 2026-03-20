"""
Backup all database objects (schema + data) before reinstalling DB.

Tries pg_dump first. If not available, falls back to Python/asyncpg export.

Usage:
  cd backend
  python db/backup_db.py

Output: db/backups/tradingpro_backup_YYYYMMDD_HHMMSS.sql
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def parse_db_url(url: str) -> dict:
    """Parse postgresql://user:pass@host:port/dbname into components."""
    u = urlparse(url)
    if u.scheme not in ("postgresql", "postgres"):
        raise ValueError("Expected postgresql:// URL")
    host = u.hostname or "127.0.0.1"
    port = u.port or 5432
    user = u.username or "postgres"
    password = u.password or ""
    dbname = (u.path or "/postgres").lstrip("/") or "postgres"
    return {"host": host, "port": port, "user": user, "password": password, "dbname": dbname}


def _find_pg_dump() -> str | None:
    """Find pg_dump on Windows (common install paths) or assume it's on PATH."""
    import shutil
    exe = shutil.which("pg_dump")
    if exe:
        return exe
    if sys.platform == "win32":
        for base in [
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")),
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
        ]:
            pg_dir = base / "PostgreSQL"
            if pg_dir.exists():
                for ver_dir in sorted(pg_dir.iterdir(), reverse=True):
                    exe_path = ver_dir / "bin" / "pg_dump.exe"
                    if exe_path.exists():
                        return str(exe_path)
    return None


def run_pg_dump_backup(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    output_path: Path,
    schema_only: bool = False,
    pg_dump_path: str | None = None,
) -> bool:
    """Run pg_dump and write to output_path. Returns True on success."""
    env = os.environ.copy()
    if password:
        env["PGPASSWORD"] = password

    pg_dump_exe = pg_dump_path or _find_pg_dump()
    if not pg_dump_exe:
        return False

    args = [
        pg_dump_exe,
        "-h", host,
        "-p", str(port),
        "-U", user,
        "-d", dbname,
        "-F", "p",
        "-f", str(output_path),
        "--no-owner",
        "--no-acl",
    ]
    if schema_only:
        args.append("--schema-only")

    try:
        subprocess.run(args, env=env, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False


async def run_python_backup(dsn: str, output_path: Path, schema_only: bool) -> bool:
    """Fallback: use asyncpg to export schema + data when pg_dump unavailable."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    lines: list[str] = []
    try:
        # Get all tables (public schema, s004_* tables)
        tables = await conn.fetch("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public' AND tablename LIKE 's004_%'
            ORDER BY tablename
        """)
        table_names = [r["tablename"] for r in tables]

        # Get views
        views = await conn.fetch("""
            SELECT viewname, definition FROM pg_views
            WHERE schemaname = 'public' AND viewname LIKE 's004_%'
        """)

        for t in table_names:
            # Get CREATE TABLE
            create = await conn.fetchrow(
                "SELECT pg_get_tabledef($1::regclass) AS def",
                f"public.{t}"
            )
            # pg_get_tabledef may not exist in older PG; fallback to simpler approach
            col_info = await conn.fetch("""
                SELECT column_name, data_type, column_default, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                ORDER BY ordinal_position
            """, t)
            if not col_info:
                continue
            cols = []
            for c in col_info:
                dt = c["data_type"].upper()
                if dt in ("character varying", "varchar"):
                    dt = "VARCHAR"
                elif dt == "numeric":
                    dt = "NUMERIC"
                elif dt == "timestamp without time zone":
                    dt = "TIMESTAMP"
                elif dt == "bigint":
                    dt = "BIGINT"
                elif dt == "integer":
                    dt = "INTEGER"
                elif dt == "boolean":
                    dt = "BOOLEAN"
                elif dt == "text":
                    dt = "TEXT"
                elif dt == "jsonb":
                    dt = "JSONB"
                elif dt == "time without time zone":
                    dt = "TIME"
                elif dt == "timestamp with time zone":
                    dt = "TIMESTAMPTZ"
                null = "" if c["is_nullable"] == "YES" else " NOT NULL"
                default = f" DEFAULT {c['column_default']}" if c["column_default"] else ""
                cols.append(f'  "{c["column_name"]}" {dt}{null}{default}')
            lines.append(f"CREATE TABLE IF NOT EXISTS {t} (\n" + ",\n".join(cols) + "\n);\n")

        for v in views:
            lines.append(f"CREATE OR REPLACE VIEW {v['viewname']} AS\n{v['definition']};\n")

        if not schema_only:
            for t in table_names:
                rows = await conn.fetch(f'SELECT * FROM "{t}"')
                if not rows:
                    continue
                cols = list(rows[0].keys())
                col_list = ", ".join(f'"{c}"' for c in cols)
                for row in rows:
                    vals = []
                    for c in cols:
                        v = row[c]
                        if v is None:
                            vals.append("NULL")
                        elif isinstance(v, bool):
                            vals.append("TRUE" if v else "FALSE")
                        elif isinstance(v, (int, float)):
                            vals.append(str(v))
                        elif isinstance(v, dict):
                            vals.append("'" + json.dumps(v).replace("'", "''") + "'::jsonb")
                        elif hasattr(v, "isoformat"):  # datetime, date, time
                            vals.append(f"'{v.isoformat()}'")
                        elif isinstance(v, (list, tuple)):
                            vals.append("'" + json.dumps(list(v)).replace("'", "''") + "'")
                        else:
                            vals.append("'" + str(v).replace("'", "''").replace("\\", "\\\\") + "'")
                    lines.append(f'INSERT INTO "{t}" ({col_list}) VALUES ({", ".join(vals)});')

        output_path.write_text("\n".join(lines), encoding="utf-8")
        return True
    except Exception as e:
        print(f"Python backup failed: {e}", file=sys.stderr)
        return False
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup database for reinstall")
    parser.add_argument("--schema-only", action="store_true", help="Backup schema only, no data")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: db/backups)")
    parser.add_argument("--pg-dump", default=None, help="Path to pg_dump.exe if not on PATH")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    backend_dir = script_dir.parent
    os.chdir(backend_dir)

    from dotenv import load_dotenv
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("DATABASE_URL not set in .env", file=sys.stderr)
        return 1

    try:
        params = parse_db_url(url)
    except ValueError as e:
        print(f"Invalid DATABASE_URL: {e}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir) if args.out_dir else script_dir / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_schema_only" if args.schema_only else ""
    out_path = out_dir / f"{params['dbname']}_backup{suffix}_{ts}.sql"

    print(f"Backing up {params['dbname']} to {out_path}...")

    ok = run_pg_dump_backup(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname=params["dbname"],
        output_path=out_path,
        schema_only=args.schema_only,
        pg_dump_path=args.pg_dump,
    )

    if not ok:
        print("pg_dump not found or failed. Trying Python/asyncpg fallback...")
        try:
            ok = asyncio.run(run_python_backup(url, out_path, args.schema_only))
        except Exception as e:
            print(f"Fallback failed: {e}", file=sys.stderr)

    if ok:
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"Backup complete: {out_path} ({size_mb:.2f} MB)")
        return 0

    print("Backup failed. If pg_dump not on PATH, run:", file=sys.stderr)
    print('  python db/backup_db.py --pg-dump "C:\\Program Files\\PostgreSQL\\15\\bin\\pg_dump.exe"', file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
