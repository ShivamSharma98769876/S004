"""FYERS API v3 helpers (session + profile). Option-chain parity with Zerodha is not wired yet."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from fyers_apiv3 import fyersModel


def _fyers_log_directory() -> str:
    """FYERS SDK treats log_path as a *directory* and writes fyersApi.log inside it.

    On Windows, os.devnull is ``nul``; the SDK then tries ``cwd\\nul\\fyersApi.log``, which fails.
    Use a real writable directory (system temp, isolated subfolder).
    """
    d = Path(tempfile.gettempdir()) / "s004_fyers_logs"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def fyers_generate_auth_url(*, client_id: str, secret_key: str, redirect_uri: str) -> str:
    session = fyersModel.SessionModel(
        client_id=client_id.strip(),
        secret_key=secret_key.strip(),
        redirect_uri=redirect_uri.strip(),
        response_type="code",
        grant_type="authorization_code",
    )
    return session.generate_authcode()


def fyers_exchange_auth_code_sync(
    *,
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    auth_code: str,
) -> dict[str, Any]:
    session = fyersModel.SessionModel(
        client_id=client_id.strip(),
        secret_key=secret_key.strip(),
        redirect_uri=redirect_uri.strip(),
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code.strip())
    return session.generate_token()


def fyers_get_profile_sync(*, client_id: str, access_token: str) -> Any:
    fy = fyersModel.FyersModel(
        is_async=False,
        log_path=_fyers_log_directory(),
        client_id=client_id.strip(),
        token=access_token.strip(),
        log_level="ERROR",
    )
    return fy.get_profile()


async def fyers_exchange_auth_code(
    *,
    client_id: str,
    secret_key: str,
    redirect_uri: str,
    auth_code: str,
) -> dict[str, Any]:
    return await asyncio.to_thread(
        fyers_exchange_auth_code_sync,
        client_id=client_id,
        secret_key=secret_key,
        redirect_uri=redirect_uri,
        auth_code=auth_code,
    )


async def fyers_get_profile(*, client_id: str, access_token: str) -> Any:
    return await asyncio.to_thread(
        fyers_get_profile_sync,
        client_id=client_id,
        access_token=access_token,
    )
