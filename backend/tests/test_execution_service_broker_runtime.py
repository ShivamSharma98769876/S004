from __future__ import annotations

import asyncio

import pytest

from app.services import execution_service
from app.services.broker_runtime import ResolvedBrokerContext
from app.services.kite_broker import OrderResult


class _DummyExecProvider:
    broker_code = "fyers"

    async def place_entry(self, symbol: str, side: str, quantity: int, expected_price: float) -> OrderResult:
        _ = (symbol, side, quantity, expected_price)
        return OrderResult(True, "oid-1", None, None, None)

    async def place_exit(self, symbol: str, side: str, quantity: int) -> OrderResult:
        _ = (symbol, side, quantity)
        return OrderResult(True, "oid-2", None, None, None)


def test_place_entry_uses_resolved_execution_provider(monkeypatch: pytest.MonkeyPatch):
    async def _resolve(*_args, **_kwargs):
        p = _DummyExecProvider()
        return ResolvedBrokerContext("fyers", "user_fyers", p, p, "fyers", False)

    monkeypatch.setattr(execution_service, "resolve_broker_context", _resolve)
    out = asyncio.run(execution_service.place_entry_order(1, "NIFTY2411022000CE", "BUY", 50, 120.0))
    assert out.success is True
    assert out.order_id == "oid-1"


def test_place_exit_no_provider_returns_no_credentials(monkeypatch: pytest.MonkeyPatch):
    async def _resolve(*_args, **_kwargs):
        return ResolvedBrokerContext(None, "none", None, None, None, False)

    monkeypatch.setattr(execution_service, "resolve_broker_context", _resolve)
    out = asyncio.run(execution_service.place_exit_order(1, "NIFTY2411022000CE", "BUY", 50))
    assert out.success is False
    assert out.error_code == "NO_CREDENTIALS"

