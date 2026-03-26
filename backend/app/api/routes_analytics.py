import asyncio
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from kiteconnect import KiteConnect

from app.api.auth_context import get_user_id, require_admin
from app.db_client import fetchrow
from app.services.option_chain_zerodha import (
    fetch_indices_spot_sync,
    fetch_option_chain_sync,
    get_expiries_for_analytics,
    verify_kite_session_sync,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def _get_kite_client_or_none(user_id: int) -> KiteConnect | None:
    row = await fetchrow(
        """
        SELECT credentials_json FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    cred = row["credentials_json"] if row else None
    if isinstance(cred, str):
        import json

        try:
            cred = json.loads(cred)
        except json.JSONDecodeError:
            cred = {}
    if not isinstance(cred, dict):
        cred = {}

    api_key = str(cred.get("apiKey", "")).strip() or os.getenv("ZERODHA_API_KEY")
    access_token = str(cred.get("accessToken", "")).strip() or os.getenv("ZERODHA_ACCESS_TOKEN")
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


@router.get("/config")
async def get_analytics_config(user_id: int = Depends(require_admin)) -> dict:
    refresh = int(os.getenv("OPTION_CHAIN_REFRESH_SECONDS", "15"))
    refresh = max(5, min(300, refresh))
    live_required = os.getenv("OPTION_CHAIN_REQUIRE_LIVE", "1").strip().lower() not in {"0", "false", "no"}
    try:
        recent_window = int(os.getenv("OPTION_CHAIN_RECENT_WINDOW", "10"))
    except ValueError:
        recent_window = 10
    recent_window = max(5, min(10, recent_window))
    return {
        "option_chain_refresh_seconds": refresh,
        "require_live_broker": live_required,
        "recent_window_fetches": recent_window,
        "expiry_config": {
            "NIFTY": {"weekday": 1, "type": "weekly"},
            "BANKNIFTY": {"weekday": 1, "type": "weekly"},
            "FINNIFTY": {"weekday": 1, "type": "weekly"},
            "SENSEX": {"weekday": 3, "type": "weekly"},
        },
    }


@router.get("/expiries")
async def get_expiries(
    instrument: str = Query("NIFTY", description="Underlying: NIFTY, BANKNIFTY, FINNIFTY, SENSEX"),
    user_id: int = Depends(require_admin),
) -> dict:
    inst = instrument.strip().upper()
    kite = await _get_kite_client_or_none(user_id)
    session_ok = await asyncio.to_thread(verify_kite_session_sync, kite)
    # Invalid/expired token: do not use broker expiry list (would mislead); use estimated fallback.
    kite_for_list = kite if session_ok else None
    expiries, source = get_expiries_for_analytics(kite_for_list, inst)
    creds_present = bool(kite)
    return {
        "instrument": inst,
        "expiries": expiries,
        "expiry_source": source,
        "broker_session_ok": session_ok,
        "credentials_present": creds_present,
    }


@router.get("/broker-status")
async def get_broker_status(user_id: int = Depends(require_admin)) -> dict:
    """Lightweight session check for Option Chain UI (token valid vs missing/expired)."""
    kite = await _get_kite_client_or_none(user_id)
    session_ok = await asyncio.to_thread(verify_kite_session_sync, kite)
    return {
        "credentials_present": bool(kite),
        "broker_session_ok": session_ok,
        "message": (
            "Zerodha session is active."
            if session_ok
            else (
                "API credentials or access token missing. Add them in Settings."
                if not kite
                else "Access token invalid or expired. Reconnect Zerodha in Settings."
            )
        ),
    }


@router.get("/indices")
async def get_indices(user_id: int = Depends(require_admin)) -> dict:
    kite = await _get_kite_client_or_none(user_id)
    data = await asyncio.to_thread(fetch_indices_spot_sync, kite)
    return data


@router.get("/summary")
async def get_summary(user_id: int = Depends(require_admin)) -> dict:
    return {
        "total_closed_trades": 0,
        "winners": 0,
        "losers": 0,
        "realized_pnl": 0.0,
        "open_trades": 0,
        "live_trades": 0,
        "paper_trades": 0,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/option-chain")
async def get_option_chain(
    instrument: str = Query("NIFTY", description="Underlying: NIFTY, BANKNIFTY, FINNIFTY, SENSEX"),
    expiry: str = Query(..., description="Expiry in DDMMMYYYY e.g. 10MAR2026"),
    strikes_up: int = Query(10, ge=1, le=50),
    strikes_down: int = Query(10, ge=1, le=50),
    user_id: int = Depends(require_admin),
) -> dict:
    inst = instrument.strip().upper()
    if inst not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}:
        raise HTTPException(status_code=400, detail="Invalid instrument.")
    try:
        kite = await _get_kite_client_or_none(user_id)
        if kite and not await asyncio.to_thread(verify_kite_session_sync, kite):
            live_required = os.getenv("OPTION_CHAIN_REQUIRE_LIVE", "1").strip().lower() not in {"0", "false", "no"}
            if live_required:
                raise HTTPException(
                    status_code=401,
                    detail="Zerodha access token is missing, invalid, or expired. Open Settings and reconnect Kite, then refresh.",
                )
            kite = None
        return await asyncio.to_thread(fetch_option_chain_sync, kite, inst, expiry, strikes_up, strikes_down)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Option chain fetch failed: {e}")
