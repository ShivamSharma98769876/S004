from __future__ import annotations

import json
import os
from typing import Any

import asyncpg
from dotenv import load_dotenv

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> None:
    global _pool
    if _pool is not None:
        return
    load_dotenv()
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set.")
    _pool = await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=8)


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
    async with pool.acquire() as conn:
        return await conn.fetch(query, *db_args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    pool = _require_pool()
    db_args = _normalize_args(args)
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *db_args)


async def execute(query: str, *args: Any) -> str:
    pool = _require_pool()
    db_args = _normalize_args(args)
    async with pool.acquire() as conn:
        return await conn.execute(query, *db_args)


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
