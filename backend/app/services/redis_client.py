"""
Optional Redis cache (set REDIS_URL). Safe no-op when unset or connection fails.
"""

from __future__ import annotations

import json
import os
from typing import Any

_redis: Any = None


async def get_redis():
    """Lazy singleton async Redis client, or None if REDIS_URL not set."""
    global _redis
    if _redis is not False:
        if _redis is not None:
            return _redis
    url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        _redis = False
        return None
    try:
        import redis.asyncio as redis_mod

        _redis = redis_mod.from_url(url, decode_responses=True)
        return _redis
    except Exception:
        _redis = False
        return None


async def cache_get_json(key: str) -> Any | None:
    r = await get_redis()
    if not r:
        return None
    try:
        raw = await r.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int = 60) -> None:
    r = await get_redis()
    if not r:
        return
    try:
        await r.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        pass


async def cache_delete(key: str) -> None:
    r = await get_redis()
    if not r:
        return
    try:
        await r.delete(key)
    except Exception:
        pass


def _sentiment_history_key(user_id: int) -> str:
    return f"s004:landing:sentiment_history:{int(user_id)}"


async def sentiment_history_redis_available() -> bool:
    """True when Redis client is connected (REDIS_URL set and connect succeeded)."""
    r = await get_redis()
    return r is not None


async def sentiment_history_append(
    user_id: int,
    record: dict[str, Any],
    *,
    max_items: int = 240,
    ttl_seconds: int | None = None,
) -> None:
    """
    Append one snapshot to a per-user capped list (no Postgres).
    Uses RPUSH + LTRIM so order is chronological (oldest → newest).
    Refreshes TTL on each write so inactive users' keys expire.
    """
    r = await get_redis()
    if not r:
        return
    if ttl_seconds is None:
        ttl_seconds = int(os.getenv("SENTIMENT_HISTORY_REDIS_TTL_SEC", str(48 * 3600)))
    key = _sentiment_history_key(user_id)
    try:
        payload = json.dumps(record, separators=(",", ":"), default=str)
        await r.rpush(key, payload)
        await r.ltrim(key, -max_items, -1)
        if ttl_seconds > 0:
            await r.expire(key, ttl_seconds)
    except Exception:
        pass


async def sentiment_history_fetch(user_id: int) -> list[dict[str, Any]]:
    """Return all stored snapshots for user in chronological order, or []."""
    r = await get_redis()
    if not r:
        return []
    key = _sentiment_history_key(user_id)
    try:
        raw_list = await r.lrange(key, 0, -1)
        out: list[dict[str, Any]] = []
        for raw in raw_list or []:
            if not isinstance(raw, str):
                continue
            try:
                out.append(json.loads(raw))
            except (TypeError, json.JSONDecodeError):
                continue
        return out
    except Exception:
        return []
