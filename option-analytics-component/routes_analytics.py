import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from kiteconnect import KiteConnect
from kiteconnect.exceptions import NetworkException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import S004Trade, S004UserBrokerAccount
from app.db.retry import execute_with_retry
from app.db.session import get_session
from app.services.option_chain_zerodha import fetch_indices_spot_sync, fetch_option_chain_sync, get_expiries_for_instrument

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Last successful option-chain response per (instrument, expiry, strikes_up, strikes_down).
# On 429 we return this so the UI can keep showing data instead of an error.
_option_chain_cache: dict[tuple[str, str, int, int], dict[str, Any]] = {}


def _get_user_id() -> int:
    return 1


@router.get("/config")
async def get_analytics_config() -> dict:
    """Public config for analytics/option chain UI (e.g. refresh interval, expiry rules)."""
    refresh = max(5, min(300, settings.OPTION_CHAIN_REFRESH_SECONDS))
    # Expiry rules: NIFTY/BANKNIFTY/SENSEX/FINNIFTY (weekday 0=Mon..4=Fri, type weekly/monthly)
    expiry_config = {
        "NIFTY": settings.get_expiry_config("NIFTY"),
        "BANKNIFTY": settings.get_expiry_config("BANKNIFTY"),
        "SENSEX": settings.get_expiry_config("SENSEX"),
        "FINNIFTY": settings.get_expiry_config("FINNIFTY"),
    }
    return {
        "option_chain_refresh_seconds": refresh,
        "expiry_config": expiry_config,
    }


@router.get("/expiries")
async def get_expiries(
    instrument: str = Query("NIFTY", description="Underlying: NIFTY, BANKNIFTY, FINNIFTY, SENSEX"),
) -> dict:
    """
    Return list of option expiries for the instrument from Zerodha NFO (cached).
    Expiries are matched to Zerodha API; use these when fetching option chain.
    Requires NFO cache to be populated (bootstrap_nfo_cache or a prior option-chain call).
    """
    instrument = instrument.strip().upper()
    expiries = get_expiries_for_instrument(instrument)
    return {"instrument": instrument, "expiries": expiries}


@router.get("/summary")
async def get_summary(session: AsyncSession = Depends(get_session)) -> dict:
    # Closed trades
    stmt_closed = select(S004Trade).where(S004Trade.state == "EXIT")
    closed_result = await execute_with_retry(session, stmt_closed)
    closed = closed_result.scalars().all()

    # Open trades
    stmt_open = select(func.count()).select_from(S004Trade).where(
        S004Trade.state != "EXIT"
    )
    open_count = (await execute_with_retry(session, stmt_open)).scalar_one()

    realized_pnl = float(
        sum(float(t.realized_pnl or 0.0) for t in closed)
    )
    total_trades = len(closed)
    winners = len([t for t in closed if (t.realized_pnl or 0) > 0])
    losers = len([t for t in closed if (t.realized_pnl or 0) < 0])

    stmt_live = select(func.count()).select_from(S004Trade).where(
        S004Trade.mode == "LIVE"
    )
    stmt_paper = select(func.count()).select_from(S004Trade).where(
        S004Trade.mode == "PAPER"
    )
    live_trades = (await execute_with_retry(session, stmt_live)).scalar_one()
    paper_trades = (await execute_with_retry(session, stmt_paper)).scalar_one()

    return {
        "total_closed_trades": total_trades,
        "winners": winners,
        "losers": losers,
        "realized_pnl": realized_pnl,
        "open_trades": open_count,
        "live_trades": live_trades,
        "paper_trades": paper_trades,
    }


@router.get("/indices")
async def get_indices(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Spot and % change for NIFTY 50, BANK NIFTY, SENSEX (for NSE MARKET strip).
    Single lightweight Kite quote call; no option chain.
    """
    user_id = _get_user_id()
    stmt = select(S004UserBrokerAccount).where(
        S004UserBrokerAccount.user_id == user_id,
        S004UserBrokerAccount.broker_name == "zerodha",
        S004UserBrokerAccount.status == "CONNECTED",
    )
    result = await session.execute(stmt)
    broker_row = result.scalar_one_or_none()
    if not broker_row or not broker_row.access_token:
        return {"NIFTY": {"spot": 0, "spotChgPct": 0}, "BANKNIFTY": {"spot": 0, "spotChgPct": 0}, "SENSEX": {"spot": 0, "spotChgPct": 0}}
    if not settings.ZERODHA_API_KEY:
        return {"NIFTY": {"spot": 0, "spotChgPct": 0}, "BANKNIFTY": {"spot": 0, "spotChgPct": 0}, "SENSEX": {"spot": 0, "spotChgPct": 0}}
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    kite.set_access_token(broker_row.access_token)
    try:
        data = await asyncio.to_thread(fetch_indices_spot_sync, kite)
        return data if data else {"NIFTY": {"spot": 0, "spotChgPct": 0}, "BANKNIFTY": {"spot": 0, "spotChgPct": 0}, "SENSEX": {"spot": 0, "spotChgPct": 0}}
    except Exception as e:
        logger.warning("get_indices failed: %s", e)
        return {"NIFTY": {"spot": 0, "spotChgPct": 0}, "BANKNIFTY": {"spot": 0, "spotChgPct": 0}, "SENSEX": {"spot": 0, "spotChgPct": 0}}


@router.get("/option-chain")
async def get_option_chain(
    instrument: str = Query("NIFTY", description="Underlying: NIFTY, BANKNIFTY, SENSEX"),
    expiry: str = Query(..., description="Expiry in DDMMMYYYY e.g. 10MAR2026"),
    strikes_up: int = Query(10, ge=1, le=50, description="Number of strikes above ATM to include"),
    strikes_down: int = Query(10, ge=1, le=50, description="Number of strikes below ATM to include"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Live option chain from connected Zerodha broker (Kite API).
    Uses stored api_key + access_token for the current user. Returns only strikes in range:
    ATM - strikes_down to ATM + strikes_up. Same layout/columns as frontend.
    """
    user_id = _get_user_id()
    stmt = select(S004UserBrokerAccount).where(
        S004UserBrokerAccount.user_id == user_id,
        S004UserBrokerAccount.broker_name == "zerodha",
        S004UserBrokerAccount.status == "CONNECTED",
    )
    result = await session.execute(stmt)
    broker_row = result.scalar_one_or_none()
    if not broker_row or not broker_row.access_token:
        raise HTTPException(
            status_code=400,
            detail="Zerodha not connected. Connect broker with API key and access token first.",
        )
    if not settings.ZERODHA_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ZERODHA_API_KEY not configured on server.",
        )
    kite = KiteConnect(api_key=settings.ZERODHA_API_KEY)
    kite.set_access_token(broker_row.access_token)
    key = (instrument.strip().upper(), expiry.strip(), strikes_up, strikes_down)
    try:
        payload = await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            key[0],
            key[1],
            strikes_up,
            strikes_down,
        )
        _option_chain_cache[key] = {"payload": payload, "cached_at": datetime.now(timezone.utc).isoformat()}
        return payload
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NetworkException as e:
        msg = str(e).lower()
        if "too many requests" in msg or (hasattr(e, "code") and getattr(e, "code") == 429):
            logger.warning("option-chain: Kite rate limit (429)")
            cached = _option_chain_cache.get(key)
            if cached:
                out = {**cached["payload"], "from_cache": True, "cached_at": cached["cached_at"]}
                return out
            raise HTTPException(
                status_code=429,
                detail=(
                    "Kite API rate limit. Wait 1–2 minutes and try again, or run once (when not rate limited): "
                    "cd backend && uv run python -m app.scripts.bootstrap_nfo_cache"
                ),
            )
        logger.exception("option-chain fetch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Broker error: {e}")
    except Exception as e:
        logger.exception("option-chain fetch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Broker option chain failed: {e}")

