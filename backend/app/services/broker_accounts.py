"""Per-user broker vault (Fernet), platform shared connection, audit, Zerodha Kite resolution."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fyers_apiv3 import fyersModel
from kiteconnect import KiteConnect

from app.db_client import execute, fetch, fetchrow
from app.services.fyers_broker import _fyers_log_directory

logger = logging.getLogger(__name__)

BROKER_ZERODHA = "zerodha"
BROKER_FYERS = "fyers"


def fernet_key_configured() -> bool:
    return bool(os.getenv("S004_CREDENTIALS_FERNET_KEY", "").strip())


def _fernet() -> Fernet:
    raw = os.getenv("S004_CREDENTIALS_FERNET_KEY", "").strip()
    if not raw:
        raise RuntimeError("S004_CREDENTIALS_FERNET_KEY is not set")
    return Fernet(raw.encode() if isinstance(raw, str) else raw)


def encrypt_vault_blob(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii")


def decrypt_vault_blob(cipher: str | None) -> dict[str, Any]:
    if not cipher or not str(cipher).strip():
        return {}
    try:
        return json.loads(_fernet().decrypt(str(cipher).strip().encode("ascii")).decode("utf-8"))
    except InvalidToken:
        logger.warning("broker vault decrypt failed (wrong key or corrupt ciphertext)")
        return {}
    except Exception:
        logger.warning("broker vault decrypt failed", exc_info=True)
        return {}


def _credentials_json_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            p = json.loads(raw)
            return dict(p) if isinstance(p, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def log_broker_audit(
    *,
    actor_user_id: int,
    subject_user_id: int | None,
    broker_code: str | None,
    action: str,
    client_ip: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    await execute(
        """
        INSERT INTO s004_broker_audit_log (actor_user_id, subject_user_id, broker_code, action, client_ip, meta)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        """,
        actor_user_id,
        subject_user_id,
        broker_code,
        action,
        client_ip or "",
        json.dumps(meta or {}),
    )


async def _fetch_master(user_id: int) -> dict[str, Any] | None:
    row = await fetchrow(
        """
        SELECT user_id, credentials_json, broker_vault_cipher, active_broker_code, broker_connected
        FROM s004_user_master_settings WHERE user_id = $1
        """,
        user_id,
    )
    return dict(row) if row else None


async def load_merged_vault(user_id: int) -> dict[str, Any]:
    """Merge encrypted vault with legacy credentials_json (Zerodha fields)."""
    row = await _fetch_master(user_id)
    if not row:
        return {}
    vault: dict[str, Any] = {}
    ciph = row.get("broker_vault_cipher")
    if ciph and fernet_key_configured():
        vault = dict(decrypt_vault_blob(str(ciph)))
    cred = _credentials_json_dict(row.get("credentials_json"))
    z = vault.get(BROKER_ZERODHA)
    if not isinstance(z, dict):
        z = {}
    if cred.get("apiKey") or cred.get("accessToken"):
        z = {
            **z,
            "apiKey": str(cred.get("apiKey") or z.get("apiKey") or ""),
            "apiSecret": str(cred.get("apiSecret") or z.get("apiSecret") or ""),
            "accessToken": str(cred.get("accessToken") or z.get("accessToken") or ""),
        }
        vault[BROKER_ZERODHA] = z
    f = vault.get(BROKER_FYERS)
    if f is not None and not isinstance(f, dict):
        vault.pop(BROKER_FYERS, None)
    return vault


async def get_active_broker_code(user_id: int) -> str | None:
    row = await _fetch_master(user_id)
    if not row:
        return None
    code = row.get("active_broker_code")
    if code and str(code).strip():
        return str(code).strip().lower()
    vault = await load_merged_vault(user_id)
    if vault.get(BROKER_FYERS) and str(vault[BROKER_FYERS].get("accessToken") or "").strip():
        if not (vault.get(BROKER_ZERODHA) and str(vault[BROKER_ZERODHA].get("accessToken") or "").strip()):
            return BROKER_FYERS
    if vault.get(BROKER_ZERODHA) and str(vault[BROKER_ZERODHA].get("accessToken") or "").strip():
        return BROKER_ZERODHA
    return None


async def get_user_role(user_id: int) -> str | None:
    row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    if not row:
        return None
    role = str(row.get("role") or "").strip().upper()
    return role or None


async def save_user_vault(
    user_id: int,
    vault: dict[str, Any],
    *,
    active_broker: str | None = None,
    broker_connected: bool | None = None,
    sync_credentials_json: bool = True,
) -> None:
    cred_out: dict[str, Any] = {}
    if sync_credentials_json:
        z = vault.get(BROKER_ZERODHA) if isinstance(vault.get(BROKER_ZERODHA), dict) else {}
        if z.get("apiKey") or z.get("accessToken"):
            cred_out = {
                "apiKey": str(z.get("apiKey") or ""),
                "apiSecret": str(z.get("apiSecret") or ""),
                "accessToken": str(z.get("accessToken") or ""),
            }
    cipher_sql: str | None = None
    if fernet_key_configured():
        cipher_sql = encrypt_vault_blob(vault)
    parts = [
        "broker_vault_cipher = $2",
        "credentials_json = $3::jsonb",
        "updated_at = NOW()",
    ]
    args: list[Any] = [user_id, cipher_sql, json.dumps(cred_out)]
    n = 4
    if active_broker is not None:
        parts.append(f"active_broker_code = ${n}")
        args.append(active_broker)
        n += 1
    if broker_connected is not None:
        parts.append(f"broker_connected = ${n}")
        args.append(broker_connected)
        n += 1
    await execute(
        f"""
        UPDATE s004_user_master_settings
        SET {", ".join(parts)}
        WHERE user_id = $1
        """,
        *args,
    )


async def user_zerodha_kite(
    user_id: int, *, env_fallback: bool = True, respect_active: bool = True
) -> KiteConnect | None:
    active = await get_active_broker_code(user_id)
    if respect_active and active == BROKER_FYERS:
        return None
    vault = await load_merged_vault(user_id)
    z = vault.get(BROKER_ZERODHA) if isinstance(vault.get(BROKER_ZERODHA), dict) else {}
    api_key = str(z.get("apiKey", "")).strip()
    access_token = str(z.get("accessToken", "")).strip()
    if env_fallback:
        api_key = api_key or os.getenv("ZERODHA_API_KEY", "").strip()
        access_token = access_token or os.getenv("ZERODHA_ACCESS_TOKEN", "").strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


async def user_fyers_client(user_id: int, *, respect_active: bool = True) -> Any | None:
    active = await get_active_broker_code(user_id)
    if respect_active and active == BROKER_ZERODHA:
        return None
    vault = await load_merged_vault(user_id)
    f = vault.get(BROKER_FYERS) if isinstance(vault.get(BROKER_FYERS), dict) else {}
    client_id = str(f.get("clientId", "")).strip()
    access_token = str(f.get("accessToken", "")).strip()
    if not client_id or not access_token:
        return None
    return fyersModel.FyersModel(
        is_async=False,
        log_path=_fyers_log_directory(),
        client_id=client_id,
        token=access_token,
        log_level="ERROR",
    )


async def platform_shared_zerodha_kite() -> KiteConnect | None:
    row = await fetchrow(
        "SELECT broker_code, vault_cipher FROM s004_platform_broker_shared WHERE id = 1",
    )
    if not row or str(row.get("broker_code") or "").lower() != BROKER_ZERODHA:
        return None
    ciph = row.get("vault_cipher")
    if not ciph or not fernet_key_configured():
        return None
    vault = decrypt_vault_blob(str(ciph))
    z = vault.get(BROKER_ZERODHA) if isinstance(vault.get(BROKER_ZERODHA), dict) else {}
    api_key = str(z.get("apiKey", "")).strip()
    access_token = str(z.get("accessToken", "")).strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


async def platform_shared_fyers_client() -> Any | None:
    row = await fetchrow(
        "SELECT broker_code, vault_cipher FROM s004_platform_broker_shared WHERE id = 1",
    )
    if not row or str(row.get("broker_code") or "").lower() != BROKER_FYERS:
        return None
    ciph = row.get("vault_cipher")
    if not ciph or not fernet_key_configured():
        return None
    vault = decrypt_vault_blob(str(ciph))
    f = vault.get(BROKER_FYERS) if isinstance(vault.get(BROKER_FYERS), dict) else {}
    client_id = str(f.get("clientId", "")).strip()
    access_token = str(f.get("accessToken", "")).strip()
    if not client_id or not access_token:
        return None
    return fyersModel.FyersModel(
        is_async=False,
        log_path=_fyers_log_directory(),
        client_id=client_id,
        token=access_token,
        log_level="ERROR",
    )


def env_fallback_kite_only() -> KiteConnect | None:
    api_key = os.getenv("ZERODHA_API_KEY", "").strip()
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN", "").strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


async def scan_any_user_zerodha_kite() -> KiteConnect | None:
    rows = await fetch(
        """
        SELECT m.user_id FROM s004_user_master_settings m
        JOIN s004_users u ON u.id = m.user_id
        WHERE m.broker_connected = TRUE OR m.credentials_json IS NOT NULL
        ORDER BY CASE WHEN u.role = 'ADMIN' THEN 0 ELSE 1 END, m.user_id
        LIMIT 25
        """,
    )
    for r in rows or []:
        kite = await user_zerodha_kite(int(r["user_id"]), env_fallback=False)
        if kite:
            return kite
    return env_fallback_kite_only()


async def get_platform_shared_status() -> dict[str, Any]:
    row = await fetchrow(
        "SELECT broker_code, vault_cipher, updated_at, updated_by_user_id FROM s004_platform_broker_shared WHERE id = 1",
    )
    if not row:
        return {"configured": False, "brokerCode": None}
    has_cipher = bool(row.get("vault_cipher")) and fernet_key_configured()
    code = str(row.get("broker_code") or "").lower() or BROKER_ZERODHA
    return {
        "configured": has_cipher,
        "brokerCode": code,
        "updatedAt": row.get("updated_at").isoformat() if row.get("updated_at") else None,
    }


async def platform_shared_slot_configured() -> bool:
    """True when admin saved credentials to the platform shared slot (paper pool for users without own Kite)."""
    st = await get_platform_shared_status()
    return bool(st.get("configured"))


async def save_platform_shared_vault(
    *,
    admin_user_id: int,
    broker_code: str,
    vault: dict[str, Any],
    client_ip: str | None,
) -> None:
    if not fernet_key_configured():
        raise RuntimeError("missing_fernet")
    cipher = encrypt_vault_blob(vault)
    await execute(
        """
        INSERT INTO s004_platform_broker_shared (id, broker_code, vault_cipher, updated_by_user_id, updated_at)
        VALUES (1, $1, $2, $3, NOW())
        ON CONFLICT (id) DO UPDATE SET
          broker_code = EXCLUDED.broker_code,
          vault_cipher = EXCLUDED.vault_cipher,
          updated_by_user_id = EXCLUDED.updated_by_user_id,
          updated_at = NOW()
        """,
        broker_code.strip().lower(),
        cipher,
        admin_user_id,
    )
    await log_broker_audit(
        actor_user_id=admin_user_id,
        subject_user_id=None,
        broker_code=broker_code,
        action="PLATFORM_SHARED_UPSERT",
        client_ip=client_ip,
        meta={"broker_code": broker_code},
    )


async def clear_platform_shared(*, admin_user_id: int, client_ip: str | None) -> None:
    await execute(
        """
        UPDATE s004_platform_broker_shared
        SET vault_cipher = NULL, updated_at = NOW(), updated_by_user_id = $1
        WHERE id = 1
        """,
        admin_user_id,
    )
    await log_broker_audit(
        actor_user_id=admin_user_id,
        subject_user_id=None,
        broker_code=None,
        action="PLATFORM_SHARED_CLEAR",
        client_ip=client_ip,
        meta={},
    )
