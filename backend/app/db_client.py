from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path
from typing import Any

import asyncpg
from dotenv import load_dotenv

_pool: asyncpg.Pool | None = None
_pool_init_lock = asyncio.Lock()
_pool_reset_lock = asyncio.Lock()
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
    if isinstance(last_error, socket.gaierror):
        _logger.error(
            "DATABASE_URL host could not be resolved (DNS / getaddrinfo). "
            "If the DB is remote: check internet, VPN, and that the hostname is still valid. "
            "For local Postgres use 127.0.0.1 or localhost in DATABASE_URL."
        )
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


async def _require_pool_async() -> asyncpg.Pool:
    """Best-effort lazy pool init for transient startup/reload races."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_init_lock:
        if _pool is None:
            await init_db_pool()
    if _pool is None:
        raise RuntimeError("Database pool is not initialized.")
    return _pool


def _is_transient_db_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    if isinstance(exc, ConnectionResetError):
        return True
    if isinstance(exc, OSError) and "forcibly closed" in msg:
        return True
    if isinstance(exc, asyncpg.exceptions.InterfaceError):
        return any(
            k in msg
            for k in (
                "connection was closed",
                "pool is closed",
                "closed",
                "another operation is in progress",
            )
        )
    return any(
        k in msg
        for k in (
            "connection was closed",
            "connection reset",
            "forcibly closed",
            "terminating connection",
            "server closed the connection unexpectedly",
        )
    )


async def _reset_pool_after_error(exc: Exception) -> None:
    global _pool
    async with _pool_reset_lock:
        cur = _pool
        _pool = None
        if cur is not None:
            try:
                await cur.close()
            except Exception:
                pass
        _logger.warning("DB pool reset after transient error: %s", exc)


def _normalize_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    normalized: list[Any] = []
    for arg in args:
        if isinstance(arg, dict):
            normalized.append(json.dumps(arg))
        else:
            normalized.append(arg)
    return tuple(normalized)


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    db_args = _normalize_args(args)
    attempts = 2
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        pool = await _require_pool_async()
        try:
            async with pool.acquire() as conn:
                return await conn.fetch(query, *db_args)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_db_error(exc) or attempt >= attempts:
                raise
            await _reset_pool_after_error(exc)
            await asyncio.sleep(0.1 * attempt)
    assert last_exc is not None
    raise last_exc


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    db_args = _normalize_args(args)
    attempts = 2
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        pool = await _require_pool_async()
        try:
            async with pool.acquire() as conn:
                return await conn.fetchrow(query, *db_args)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_db_error(exc) or attempt >= attempts:
                raise
            await _reset_pool_after_error(exc)
            await asyncio.sleep(0.1 * attempt)
    assert last_exc is not None
    raise last_exc


async def execute(query: str, *args: Any) -> str:
    db_args = _normalize_args(args)
    attempts = 2
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        pool = await _require_pool_async()
        try:
            async with pool.acquire() as conn:
                return await conn.execute(query, *db_args)
        except Exception as exc:
            last_exc = exc
            if not _is_transient_db_error(exc) or attempt >= attempts:
                raise
            await _reset_pool_after_error(exc)
            await asyncio.sleep(0.1 * attempt)
    assert last_exc is not None
    raise last_exc


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
