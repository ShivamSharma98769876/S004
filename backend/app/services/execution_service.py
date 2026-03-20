"""Execution service: places entry/exit orders via Kite with user isolation."""
from __future__ import annotations

import json

from app.db_client import fetchrow
from app.services.kite_broker import OrderResult, place_nfo_market_order


async def _get_kite_for_user(user_id: int):
    """Get KiteConnect instance for user. Returns None if no valid credentials."""
    from kiteconnect import KiteConnect

    row = await fetchrow(
        "SELECT credentials_json FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    if not row:
        return None
    cred = row.get("credentials_json")
    if isinstance(cred, str):
        try:
            cred = json.loads(cred)
        except json.JSONDecodeError:
            return None
    if not isinstance(cred, dict):
        return None
    api_key = str(cred.get("apiKey", "")).strip()
    access_token = str(cred.get("accessToken", "")).strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


async def place_entry_order(
    user_id: int,
    symbol: str,
    side: str,
    quantity: int,
    expected_price: float,
) -> OrderResult:
    """
    Place entry (BUY) order for user. User-isolated: uses only this user's Kite credentials.
    Returns OrderResult. On TOKEN_EXPIRED, fail safe - require re-login.
    """
    kite = await _get_kite_for_user(user_id)
    if not kite:
        return OrderResult(False, None, None, "NO_CREDENTIALS", "Connect Zerodha in Settings.")
    txn = "BUY" if str(side or "BUY").upper() == "BUY" else "SELL"
    return place_nfo_market_order(kite, symbol, txn, quantity)


async def place_exit_order(
    user_id: int,
    symbol: str,
    side: str,
    quantity: int,
) -> OrderResult:
    """
    Place exit (SELL to close long) order. User-isolated.
    For BUY position, we SELL to exit.
    """
    kite = await _get_kite_for_user(user_id)
    if not kite:
        return OrderResult(False, None, None, "NO_CREDENTIALS", "Connect Zerodha in Settings.")
    orig_side = str(side or "BUY").upper()
    txn = "SELL" if orig_side == "BUY" else "BUY"
    return place_nfo_market_order(kite, symbol, txn, quantity)
