from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

_pool: asyncpg.Pool | None = None
_logger = logging.getLogger("s004.db")


async def init_db_pool() -> None:
    global _pool
    if _pool is not None:
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    load_dotenv(dotenv_path=env_path, override=False)
    load_dotenv(override=False)
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set.")
    max_attempts = int(os.getenv("DB_CONNECT_MAX_ATTEMPTS", "5"))
    retry_delay_sec = float(os.getenv("DB_CONNECT_RETRY_DELAY_SEC", "2"))
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            _pool = await asyncpg.create_pool(
                dsn=db_url,
                min_size=1,
                max_size=8,
                timeout=15,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt >= max_attempts:
                break
            _logger.warning(
                "DB connect attempt %s/%s failed (%s). Retrying in %.1fs...",
                attempt,
                max_attempts,
                type(exc).__name__,
                retry_delay_sec,
            )
            await asyncio.sleep(retry_delay_sec)
    assert last_error is not None
    raise last_error


async def close_db_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not initialized.")
    return _pool


def _normalize_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    normalized: list[Any] = []
    for arg in args:
        if isinstance(arg, dict):
            normalized.append(json.dumps(arg))
        else:
            normalized.append(arg)
    return tuple(normalized)


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = _require_pool()
    db_args = _normalize_args(args)
    try:
        async with pool.acquire() as conn:
            return await conn.fetch(query, *db_args)
    except asyncpg.exceptions.InterfaceError as exc:
        if "closing" in str(exc).lower() or "closed" in str(exc).lower():
            _logger.warning("fetch skipped: DB pool shutting down (%s)", exc)
        raise


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    pool = _require_pool()
    db_args = _normalize_args(args)
    try:
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *db_args)
    except asyncpg.exceptions.InterfaceError as exc:
        if "closing" in str(exc).lower() or "closed" in str(exc).lower():
            _logger.warning("fetchrow skipped: DB pool shutting down (%s)", exc)
        raise


async def execute(query: str, *args: Any) -> str:
    pool = _require_pool()
    db_args = _normalize_args(args)
    try:
        async with pool.acquire() as conn:
            return await conn.execute(query, *db_args)
    except asyncpg.exceptions.InterfaceError as exc:
        if "closing" in str(exc).lower() or "closed" in str(exc).lower():
            _logger.warning("execute skipped: DB pool shutting down (%s)", exc)
        raise


async def ensure_user(user_id: int) -> None:
    await execute(
        """
        INSERT INTO s004_users (id, username, full_name, role, status)
        VALUES ($1, $2, $3, 'USER', 'ACTIVE')
        ON CONFLICT (id) DO NOTHING
        """,
        user_id,
        f"user{user_id}",
        f"User {user_id}",
    )
