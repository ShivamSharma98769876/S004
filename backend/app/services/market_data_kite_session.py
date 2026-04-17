"""Broker session diagnostics for features that use shared quote resolution (user -> platform)."""

from __future__ import annotations

from app.services.broker_accounts import BROKER_FYERS, BROKER_ZERODHA, get_active_broker_code
from app.services.broker_runtime import resolve_broker_context


def market_data_session_hint(
    *,
    active_broker: str | None,
    quote_source: str,
    session_ok: bool,
    kite_present: bool,
    platform_shared_broker_code: str | None = None,
) -> str:
    """User-facing market-data session hints, broker-agnostic."""
    if quote_source == "platform_only_unavailable":
        pbc = (platform_shared_broker_code or "").lower()
        if pbc and pbc != BROKER_ZERODHA:
            if pbc == BROKER_FYERS:
                return (
                    "Admin platform shared connection is FYERS, but its market-data session is currently unavailable. "
                    "Ask admin to reconnect the shared broker under Admin -> Platform broker."
                )
            return (
                "Admin platform shared broker is configured, but market data cannot be loaded from it. "
                "Ask admin to reconnect under Admin -> Platform broker."
            )
        return (
            "Admin shared broker (paper pool) is configured but tokens there are missing or unusable. "
            "Ask an admin to reconnect under Admin -> Platform broker."
        )
    if session_ok:
        if quote_source == "platform_shared":
            if (platform_shared_broker_code or "").lower() == BROKER_FYERS:
                return "Market data session OK (admin shared FYERS — used for paper when you have no own broker login)."
            return "Market data session OK (admin shared Zerodha — used for paper when you have no own broker login)."
        if quote_source == "user_zerodha":
            return "Market data session OK (your Zerodha connection under Settings → Brokers)."
        if quote_source == "user_fyers":
            return "Market data session OK (your FYERS connection under Settings → Brokers)."
        return "Market data session OK (resolved broker market-data session)."
    if not kite_present:
        return (
            "No broker market-data session is available. Connect a broker under Settings -> Brokers, "
            "or ask an admin to configure/reconnect the shared broker for paper quotes (Admin -> Platform broker)."
        )
    return (
        "Broker market-data token is missing or expired. Reconnect under Settings -> Brokers, then refresh this page."
    )


async def get_market_data_session_bundle(user_id: int) -> dict:
    from app.services.broker_accounts import get_platform_shared_status

    active = await get_active_broker_code(user_id)
    ctx = await resolve_broker_context(user_id, mode="PAPER")
    quote_source = ctx.source
    platform_bc: str | None = None
    if quote_source in {"platform_only_unavailable", "platform_shared"}:
        st = await get_platform_shared_status()
        platform_bc = str(st.get("brokerCode") or "").strip().lower() or None
    provider = ctx.market_data
    session_ok = bool(provider and await provider.session_ok())
    kite_present = bool(provider)
    return {
        "active_broker": active,
        "market_data_quote_source": quote_source,
        "platform_shared_broker_code": platform_bc,
        "broker_session_ok": session_ok,
        "credentials_present": kite_present,
        "session_hint": market_data_session_hint(
            active_broker=active,
            quote_source=quote_source,
            session_ok=session_ok,
            kite_present=kite_present,
            platform_shared_broker_code=platform_bc,
        ),
    }
