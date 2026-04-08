import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.auth_context import require_admin
from app.services.broker_runtime import resolve_broker_context
from app.services.market_data_kite_session import get_market_data_session_bundle
from app.services.option_chain_zerodha import (
    get_expiries_for_analytics,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/config")
async def get_analytics_config(user_id: int = Depends(require_admin)) -> dict:
    refresh = int(os.getenv("OPTION_CHAIN_REFRESH_SECONDS", "30"))
    refresh = max(5, min(300, refresh))
    live_required = os.getenv("OPTION_CHAIN_REQUIRE_LIVE", "1").strip().lower() not in {"0", "false", "no"}
    from app.services.option_chain_zerodha import _window_size

    recent_window = _window_size()
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
    bundle = await get_market_data_session_bundle(user_id)
    ctx = await resolve_broker_context(user_id, mode="PAPER")
    provider = ctx.market_data
    session_ok = bundle["broker_session_ok"]
    if provider:
        try:
            expiries, source = await provider.expiries(inst)
        except Exception:
            expiries, source = get_expiries_for_analytics(None, inst)
    else:
        expiries, source = get_expiries_for_analytics(None, inst)
    return {
        "instrument": inst,
        "expiries": expiries,
        "expiry_source": source,
        "broker_session_ok": session_ok,
        "credentials_present": bundle["credentials_present"],
        "active_broker": bundle["active_broker"],
        "market_data_quote_source": bundle["market_data_quote_source"],
        "platform_shared_broker_code": bundle.get("platform_shared_broker_code"),
        "session_hint": bundle["session_hint"],
    }


@router.get("/broker-status")
async def get_broker_status(user_id: int = Depends(require_admin)) -> dict:
    """Lightweight session check for Option Chain UI (token valid vs missing/expired)."""
    bundle = await get_market_data_session_bundle(user_id)
    return {
        "credentials_present": bundle["credentials_present"],
        "broker_session_ok": bundle["broker_session_ok"],
        "active_broker": bundle["active_broker"],
        "market_data_quote_source": bundle["market_data_quote_source"],
        "platform_shared_broker_code": bundle.get("platform_shared_broker_code"),
        "session_hint": bundle["session_hint"],
        "message": bundle["session_hint"],
    }


@router.get("/indices")
async def get_indices(user_id: int = Depends(require_admin)) -> dict:
    ctx = await resolve_broker_context(user_id, mode="PAPER")
    provider = ctx.market_data
    if not provider:
        return {
            "NIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "BANKNIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "FINNIFTY": {"spot": 0.0, "spotChgPct": 0.0},
            "SENSEX": {"spot": 0.0, "spotChgPct": 0.0},
        }
    return await provider.indices()


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
        live_required = os.getenv("OPTION_CHAIN_REQUIRE_LIVE", "1").strip().lower() not in {"0", "false", "no"}
        bundle = await get_market_data_session_bundle(user_id)
        ctx = await resolve_broker_context(user_id, mode="PAPER")
        provider = ctx.market_data
        if live_required:
            if not provider or not bundle["broker_session_ok"]:
                raise HTTPException(status_code=401, detail=bundle["session_hint"])
        if not provider:
            raise HTTPException(status_code=401, detail=bundle["session_hint"])
        payload = await provider.option_chain(inst, expiry, strikes_up, strikes_down, live_required)
        if isinstance(payload, dict):
            payload.setdefault("broker_session_ok", bundle["broker_session_ok"])
            payload.setdefault("credentials_present", bundle["credentials_present"])
            payload.setdefault("active_broker", bundle["active_broker"])
            payload.setdefault("market_data_quote_source", bundle["market_data_quote_source"])
            payload.setdefault("platform_shared_broker_code", bundle.get("platform_shared_broker_code"))
            payload.setdefault("session_hint", bundle["session_hint"])
        return payload
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Option chain fetch failed: {e}")
