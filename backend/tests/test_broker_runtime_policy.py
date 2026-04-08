from __future__ import annotations

import asyncio

import pytest

from app.services import broker_runtime as br


def test_resolve_prefers_user_connection(monkeypatch: pytest.MonkeyPatch):
    class DummyProvider:
        pass

    async def _user_provider(_user_id: int):
        return DummyProvider(), "fyers"

    monkeypatch.setattr(br.ba, "get_user_role", lambda _u: _async_value("USER"))
    monkeypatch.setattr(br.ba, "get_active_broker_code", lambda _u: _async_value("fyers"))
    monkeypatch.setattr(br, "_resolve_user_provider", _user_provider)

    ctx = asyncio.run(br.resolve_broker_context(11, mode="PAPER"))
    assert ctx.source == "user_fyers"
    assert ctx.market_data is not None
    assert ctx.execution is not None


def test_resolve_non_admin_paper_uses_platform_shared(monkeypatch: pytest.MonkeyPatch):
    class DummyProvider:
        pass

    async def _none_user(_user_id: int):
        return None, None

    async def _shared():
        return DummyProvider(), "zerodha"

    monkeypatch.setattr(br.ba, "get_user_role", lambda _u: _async_value("USER"))
    monkeypatch.setattr(br.ba, "get_active_broker_code", lambda _u: _async_value(None))
    monkeypatch.setattr(br, "_resolve_user_provider", _none_user)
    monkeypatch.setattr(br, "_resolve_platform_shared_provider", _shared)

    ctx = asyncio.run(br.resolve_broker_context(12, mode="PAPER"))
    assert ctx.source == "platform_shared"
    assert ctx.broker_code == "zerodha"


def test_resolve_non_admin_live_does_not_use_platform_shared(monkeypatch: pytest.MonkeyPatch):
    async def _none_user(_user_id: int):
        return None, None

    monkeypatch.setattr(br.ba, "get_user_role", lambda _u: _async_value("USER"))
    monkeypatch.setattr(br.ba, "get_active_broker_code", lambda _u: _async_value(None))
    monkeypatch.setattr(br, "_resolve_user_provider", _none_user)
    monkeypatch.setattr(br.ba, "platform_shared_slot_configured", lambda: _async_value(True))

    ctx = asyncio.run(br.resolve_broker_context(13, mode="LIVE"))
    assert ctx.source == "none"
    assert ctx.execution is None


async def _async_value(v):
    return v


def test_fyers_expiries_are_sorted_chronologically():
    class DummyFyers:
        def optionchain(self, _payload):
            return {
                "data": {
                    "expiryData": [
                        {"date": "24APR2026"},
                        {"date": "10APR2026"},
                        {"date": "17APR2026"},
                        {"date": "10APR2026"},
                    ]
                }
            }

    provider = br.FyersProvider(DummyFyers())
    expiries, source = asyncio.run(provider.expiries("NIFTY"))

    assert source == "fyers_optionchain"
    assert expiries == ["10APR2026", "17APR2026", "24APR2026"]


def test_fyers_expiries_fallback_to_estimated_weeklies_when_nearest_is_far(monkeypatch: pytest.MonkeyPatch):
    class DummyFyers:
        def optionchain(self, _payload):
            return {
                "data": {
                    "expiryData": [
                        {"date": "28APR2026"},
                        {"date": "26MAY2026"},
                        {"date": "30JUN2026"},
                    ]
                }
            }

    monkeypatch.setattr(
        br,
        "get_expiries_for_analytics",
        lambda _kite, _instrument: (["10APR2026", "17APR2026", "24APR2026"], "estimated_weeklies"),
    )
    provider = br.FyersProvider(DummyFyers())
    expiries, source = asyncio.run(provider.expiries("NIFTY"))

    assert source == "estimated_weeklies_fyers_fallback"
    assert expiries == ["10APR2026", "17APR2026", "24APR2026"]

