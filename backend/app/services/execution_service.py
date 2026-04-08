"""Execution service: places entry/exit orders via Kite with user isolation."""
from __future__ import annotations

from app.services.broker_runtime import resolve_broker_context
from app.services.kite_broker import OrderResult


async def place_entry_order(
    user_id: int,
    symbol: str,
    side: str,
    quantity: int,
    expected_price: float,
) -> OrderResult:
    """Place LIVE entry via resolved user broker execution provider."""
    ctx = await resolve_broker_context(user_id, mode="LIVE")
    provider = ctx.execution
    if not provider:
        return OrderResult(False, None, None, "NO_CREDENTIALS", "Connect your broker under Settings → Brokers.")
    return await provider.place_entry(symbol, side, quantity, expected_price)


async def place_exit_order(
    user_id: int,
    symbol: str,
    side: str,
    quantity: int,
) -> OrderResult:
    """Place LIVE exit via resolved user broker execution provider."""
    ctx = await resolve_broker_context(user_id, mode="LIVE")
    provider = ctx.execution
    if not provider:
        return OrderResult(False, None, None, "NO_CREDENTIALS", "Connect your broker under Settings → Brokers.")
    return await provider.place_exit(symbol, side, quantity)
