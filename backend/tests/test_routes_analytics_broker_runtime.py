from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.api import routes_analytics
from app.services.broker_runtime import ResolvedBrokerContext


class _Provider:
    async def session_ok(self) -> bool:
        return True

    async def indices(self) -> dict:
        return {"NIFTY": {"spot": 100.0, "spotChgPct": 1.0}}

    async def expiries(self, instrument: str) -> tuple[list[str], str]:
        _ = instrument
        return ["10MAR2026"], "provider"

    async def option_chain(self, instrument: str, expiry: str, strikes_up: int, strikes_down: int, require_live: bool) -> dict:
        _ = (instrument, expiry, strikes_up, strikes_down, require_live)
        return {"spot": 1.0, "spotChgPct": 0.0, "vix": None, "synFuture": None, "pcr": 1.0, "pcrVol": 1.0, "updated": None, "chain": []}


def test_indices_uses_runtime_provider(monkeypatch: pytest.MonkeyPatch):
    p = _Provider()

    async def _resolve(*_args, **_kwargs):
        return ResolvedBrokerContext("fyers", "user_fyers", p, p, "fyers", True)

    monkeypatch.setattr(routes_analytics, "resolve_broker_context", _resolve)
    out = asyncio.run(routes_analytics.get_indices(user_id=1))
    assert out["NIFTY"]["spot"] == 100.0


def test_option_chain_raises_401_when_live_required_without_provider(monkeypatch: pytest.MonkeyPatch):
    async def _resolve(*_args, **_kwargs):
        return ResolvedBrokerContext(None, "none", None, None, None, True)

    async def _bundle(*_args, **_kwargs):
        return {"broker_session_ok": False, "session_hint": "missing", "credentials_present": False}

    monkeypatch.setattr(routes_analytics, "resolve_broker_context", _resolve)
    monkeypatch.setattr(routes_analytics, "get_market_data_session_bundle", _bundle)

    with pytest.raises(HTTPException) as ex:
        asyncio.run(routes_analytics.get_option_chain("NIFTY", "10MAR2026", 10, 10, user_id=1))
    assert ex.value.status_code == 401

