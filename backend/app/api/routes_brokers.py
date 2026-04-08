"""Hub APIs for multi-broker connections (Zerodha + FYERS) and active-broker selection."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth_context import get_user_id
from app.db_client import ensure_user, execute, fetchrow
from app.services import broker_accounts as ba
from app.services.fyers_broker import fyers_exchange_auth_code, fyers_generate_auth_url, fyers_get_profile

router = APIRouter(prefix="/settings/brokers", tags=["brokers"])


def _client_ip(request: Request) -> str | None:
    if request.client:
        return request.client.host
    return None


class SetActiveBrokerPayload(BaseModel):
    brokerCode: str = Field(..., description="zerodha or fyers")


class FyersAuthUrlPayload(BaseModel):
    clientId: str
    secretKey: str
    redirectUri: str


class FyersConnectPayload(BaseModel):
    clientId: str
    secretKey: str
    redirectUri: str
    authCode: str


@router.get("")
async def get_brokers_hub(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    await ensure_user(user_id)
    vault = await ba.load_merged_vault(user_id)
    active = await ba.get_active_broker_code(user_id)
    z = vault.get(ba.BROKER_ZERODHA) if isinstance(vault.get(ba.BROKER_ZERODHA), dict) else {}
    f = vault.get(ba.BROKER_FYERS) if isinstance(vault.get(ba.BROKER_FYERS), dict) else {}
    row = await fetchrow(
        "SELECT broker_connected FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    broker_connected = bool(row and row.get("broker_connected"))
    z_ok = bool(str(z.get("accessToken") or "").strip())
    f_ok = bool(str(f.get("accessToken") or "").strip())
    platform = await ba.get_platform_shared_status()
    return {
        "activeBroker": active,
        "encryptionReady": ba.fernet_key_configured(),
        "brokerConnectedFlag": broker_connected,
        "paperSharedAvailable": platform.get("configured") is True,
        "platformShared": platform,
        "brokers": [
            {
                "code": ba.BROKER_ZERODHA,
                "label": "Zerodha Kite",
                "connected": z_ok,
                "chainSupported": True,
                "liveOrdersSupported": True,
            },
            {
                "code": ba.BROKER_FYERS,
                "label": "FYERS",
                "connected": f_ok,
                "chainSupported": True,
                "liveOrdersSupported": True,
                "note": "FYERS market-data and order routing are enabled when FYERS is active.",
            },
        ],
    }


@router.put("/active")
async def put_active_broker(
    payload: SetActiveBrokerPayload,
    request: Request,
    user_id: int = Depends(get_user_id),
) -> dict[str, Any]:
    await ensure_user(user_id)
    code = payload.brokerCode.strip().lower()
    if code not in (ba.BROKER_ZERODHA, ba.BROKER_FYERS):
        raise HTTPException(status_code=400, detail="brokerCode must be zerodha or fyers")
    vault = await ba.load_merged_vault(user_id)
    if code == ba.BROKER_ZERODHA:
        z = vault.get(ba.BROKER_ZERODHA) if isinstance(vault.get(ba.BROKER_ZERODHA), dict) else {}
        if not str(z.get("accessToken") or "").strip():
            raise HTTPException(status_code=400, detail="Connect Zerodha before setting it active.")
    else:
        f = vault.get(ba.BROKER_FYERS) if isinstance(vault.get(ba.BROKER_FYERS), dict) else {}
        if not str(f.get("accessToken") or "").strip():
            raise HTTPException(status_code=400, detail="Connect FYERS before setting it active.")
    await execute(
        """
        UPDATE s004_user_master_settings
        SET active_broker_code = $2, updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
        code,
    )
    await ba.log_broker_audit(
        actor_user_id=user_id,
        subject_user_id=user_id,
        broker_code=code,
        action="SET_ACTIVE_BROKER",
        client_ip=_client_ip(request),
        meta={"brokerCode": code},
    )
    return {"ok": True, "activeBroker": code}


@router.post("/fyers/auth-url")
async def post_fyers_auth_url(
    payload: FyersAuthUrlPayload,
    user_id: int = Depends(get_user_id),
) -> dict[str, str]:
    await ensure_user(user_id)
    if not payload.clientId.strip() or not payload.secretKey.strip() or not payload.redirectUri.strip():
        raise HTTPException(status_code=400, detail="clientId, secretKey, and redirectUri are required.")
    try:
        url = fyers_generate_auth_url(
            client_id=payload.clientId,
            secret_key=payload.secretKey,
            redirect_uri=payload.redirectUri,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not build FYERS login URL: {exc}") from exc
    return {"authUrl": url}


@router.post("/fyers/connect")
async def post_fyers_connect(
    payload: FyersConnectPayload,
    request: Request,
    user_id: int = Depends(get_user_id),
) -> dict[str, Any]:
    await ensure_user(user_id)
    if not ba.fernet_key_configured():
        raise HTTPException(
            status_code=503,
            detail="Server must set S004_CREDENTIALS_FERNET_KEY to store FYERS credentials.",
        )
    try:
        tok = await fyers_exchange_auth_code(
            client_id=payload.clientId,
            secret_key=payload.secretKey,
            redirect_uri=payload.redirectUri,
            auth_code=payload.authCode,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"FYERS token exchange failed: {exc}") from exc
    access = str((tok or {}).get("access_token") or "").strip()
    if not access:
        raise HTTPException(status_code=400, detail="FYERS did not return access_token.")
    try:
        profile = await fyers_get_profile(client_id=payload.clientId.strip(), access_token=access)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"FYERS profile check failed: {exc}") from exc
    vault = await ba.load_merged_vault(user_id)
    vault[ba.BROKER_FYERS] = {
        "clientId": payload.clientId.strip(),
        "secretKey": payload.secretKey.strip(),
        "redirectUri": payload.redirectUri.strip(),
        "accessToken": access,
    }
    await ba.save_user_vault(
        user_id,
        vault,
        active_broker=ba.BROKER_FYERS,
        broker_connected=True,
        sync_credentials_json=True,
    )
    await ba.log_broker_audit(
        actor_user_id=user_id,
        subject_user_id=user_id,
        broker_code=ba.BROKER_FYERS,
        action="CONNECT",
        client_ip=_client_ip(request),
        meta={"profile_ok": True},
    )
    return {"status": "connected", "activeBroker": ba.BROKER_FYERS, "profile": profile}


@router.post("/fyers/disconnect")
async def post_fyers_disconnect(request: Request, user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    await ensure_user(user_id)
    row_prev = await fetchrow(
        "SELECT active_broker_code FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    prev_active = str(row_prev.get("active_broker_code") or "").strip().lower() if row_prev else ""
    vault = await ba.load_merged_vault(user_id)
    if ba.BROKER_FYERS in vault:
        del vault[ba.BROKER_FYERS]
    z = vault.get(ba.BROKER_ZERODHA) if isinstance(vault.get(ba.BROKER_ZERODHA), dict) else {}
    has_z = bool(str(z.get("accessToken") or "").strip())
    if prev_active == ba.BROKER_FYERS:
        next_active = ba.BROKER_ZERODHA if has_z else None
    else:
        next_active = prev_active or None
    if next_active is None and has_z:
        next_active = ba.BROKER_ZERODHA
    await ba.save_user_vault(
        user_id,
        vault,
        active_broker=next_active,
        broker_connected=has_z,
        sync_credentials_json=True,
    )
    if not has_z:
        await execute(
            """
            UPDATE s004_user_master_settings SET broker_connected = FALSE WHERE user_id = $1
            """,
            user_id,
        )
    await ba.log_broker_audit(
        actor_user_id=user_id,
        subject_user_id=user_id,
        broker_code=ba.BROKER_FYERS,
        action="DISCONNECT",
        client_ip=_client_ip(request),
        meta={},
    )
    return {"status": "disconnected", "activeBroker": next_active}
