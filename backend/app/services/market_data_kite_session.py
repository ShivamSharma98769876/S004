"""Zerodha Kite session diagnostics for features that use shared quote resolution (user → platform)."""

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
    """User-facing copy: option chain / indices use Zerodha Kite market data, not FYERS yet."""
    if quote_source == "platform_only_unavailable":
        pbc = (platform_shared_broker_code or "").lower()
        if pbc and pbc != BROKER_ZERODHA:
            if pbc == BROKER_FYERS:
                return (
                    "Admin platform shared connection is FYERS; option analytics still need Zerodha market data. "
                    "Set Zerodha on the shared slot under Admin → Platform broker, or connect your own Zerodha under Settings → Brokers."
                )
            return (
                "Admin platform shared broker is configured, but Zerodha market data cannot be loaded from it. "
                "Use Zerodha on Admin → Platform broker or connect Zerodha under Settings → Brokers."
            )
        return (
            "Admin shared broker (paper pool) is configured but Zerodha tokens there are missing or unusable. "
            "Ask an admin to reconnect under Admin → Platform broker."
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
    if (active_broker or "").lower() == BROKER_FYERS:
        return (
            "FYERS is your active broker, but option analytics still use Zerodha market data. "
            "Connect Zerodha under Settings → Brokers, or ask an admin to set the shared Zerodha connection for paper quotes."
        )
    if not kite_present:
        return (
            "No Zerodha market-data session. Connect Zerodha under Settings → Brokers, "
            "or ask an admin to configure the shared Zerodha connection for paper (Admin → Platform broker)."
        )
    return (
        "Zerodha market-data token is missing or expired (Kite tokens expire daily). "
        "Reconnect under Settings → Brokers, then refresh this page."
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
