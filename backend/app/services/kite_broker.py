"""Kite broker adapter for placing NFO options orders (MIS, Market)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kiteconnect import KiteConnect


@dataclass
class OrderResult:
    """Result of a broker order placement."""
    success: bool
    order_id: str | None
    fill_price: float | None
    error_code: str | None  # e.g. "INVALID_TOKEN", "INSUFFICIENT_MARGIN"
    error_message: str | None


def _symbol_to_tradingsymbol(symbol: str) -> str:
    """Convert stored symbol (NIFTY2631723250CE) to Kite tradingsymbol format."""
    return str(symbol or "").replace(" ", "").upper()


def place_nfo_market_order(
    kite: "KiteConnect",
    tradingsymbol: str,
    transaction_type: str,
    quantity: int,
) -> OrderResult:
    """
    Place NFO MIS market order. Returns order_id on success.
    On token expiry / auth error, returns OrderResult with success=False, error_code set.
    """
    if not tradingsymbol or quantity <= 0:
        return OrderResult(False, None, None, "INVALID_PARAMS", "Symbol and quantity required")

    ts = _symbol_to_tradingsymbol(tradingsymbol)
    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NFO,
            tradingsymbol=ts,
            transaction_type=transaction_type.upper(),
            quantity=quantity,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_MARKET,
            validity=kite.VALIDITY_DAY,
        )
        return OrderResult(success=True, order_id=str(order_id), fill_price=None, error_code=None, error_message=None)
    except Exception as e:
        err = str(e).upper()
        if "INVALID_REQUEST" in err or "TOKEN" in err or "SESSION" in err or "UNAUTHORIZED" in err or "LOGIN" in err:
            return OrderResult(False, None, None, "TOKEN_EXPIRED", str(e))
        if "MARGIN" in err or "INSUFFICIENT" in err:
            return OrderResult(False, None, None, "INSUFFICIENT_MARGIN", str(e))
        if "RATE" in err or "LIMIT" in err:
            return OrderResult(False, None, None, "RATE_LIMITED", str(e))
        return OrderResult(False, None, None, "ORDER_FAILED", str(e))
