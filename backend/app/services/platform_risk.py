"""Platform-wide trading pause and per-user daily P&L limits (master settings)."""

from __future__ import annotations

from app.db_client import fetchrow
from app.services.ist_time_sql import IST_TODAY, closed_at_ist_date_bare

_PLATFORM_CACHE_KEY = "s004:platform:settings"


async def get_platform_trading_paused() -> tuple[bool, str | None]:
    """Return (trading_paused, pause_reason). If table missing, (False, None). Cached briefly when Redis is available."""
    from app.services.redis_client import cache_get_json, cache_set_json

    cached = await cache_get_json(_PLATFORM_CACHE_KEY)
    if isinstance(cached, dict) and "paused" in cached:
        r = cached.get("reason")
        return bool(cached["paused"]), (str(r).strip() if r else None)

    try:
        row = await fetchrow(
            "SELECT trading_paused, pause_reason FROM s004_platform_settings WHERE id = 1",
        )
    except Exception:
        return False, None
    if not row:
        return False, None
    paused = bool(row.get("trading_paused"))
    reason = str(row["pause_reason"]).strip() if row.get("pause_reason") else None
    await cache_set_json(
        _PLATFORM_CACHE_KEY,
        {"paused": paused, "reason": reason},
        ttl_seconds=3,
    )
    return paused, reason


async def invalidate_platform_settings_cache() -> None:
    from app.services.redis_client import cache_delete

    await cache_delete(_PLATFORM_CACHE_KEY)


async def user_today_realized_pnl_ist(user_id: int) -> float:
    """Sum realized_pnl for trades closed today (IST calendar date)."""
    row = await fetchrow(
        f"""
        SELECT COALESCE(SUM(realized_pnl), 0)::float AS pnl
        FROM s004_live_trades
        WHERE user_id = $1
          AND current_state = 'EXIT'
          AND closed_at IS NOT NULL
          AND {closed_at_ist_date_bare()} = {IST_TODAY}
        """,
        user_id,
    )
    if not row:
        return 0.0
    return float(row.get("pnl") or 0.0)


async def evaluate_user_daily_pnl_limits(user_id: int) -> tuple[bool, str, str]:
    """
    Daily max loss / max profit only (master settings). No platform pause.
    Returns (allowed, reason_code, message).
    """
    master = await fetchrow(
        """
        SELECT COALESCE(max_loss_day, 0)::float AS max_loss_day,
               COALESCE(max_profit_day, 0)::float AS max_profit_day
        FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    if not master:
        return True, "ALLOWED", "OK"

    max_loss = float(master.get("max_loss_day") or 0)
    max_profit = float(master.get("max_profit_day") or 0)
    daily = await user_today_realized_pnl_ist(user_id)

    if max_loss > 0 and daily <= -max_loss:
        return (
            False,
            "DAILY_LOSS_LIMIT_REACHED",
            f"Today's realized P&L ({daily:.2f}) has reached the daily loss limit ({max_loss:.2f}).",
        )

    if max_profit > 0 and daily >= max_profit:
        return (
            False,
            "DAILY_PROFIT_LIMIT_REACHED",
            f"Today's realized P&L ({daily:.2f}) has reached the daily profit cap ({max_profit:.2f}).",
        )

    return True, "ALLOWED", "OK"


async def evaluate_trade_entry_allowed(user_id: int) -> tuple[bool, str, str]:
    """Platform pause + daily P&L limits. Use before any new trade (manual or auto)."""
    paused, pause_reason = await get_platform_trading_paused()
    if paused:
        msg = "Trading is paused by platform administrator."
        if pause_reason:
            msg = f"{msg} ({pause_reason})"
        return False, "PLATFORM_TRADING_PAUSED", msg
    return await evaluate_user_daily_pnl_limits(user_id)
