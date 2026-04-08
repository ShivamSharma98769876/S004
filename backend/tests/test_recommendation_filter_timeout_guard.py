from __future__ import annotations

import asyncio

import pytest

from app.services import trades_service as ts


def test_short_delta_filter_skips_vix_fetch_for_non_short_premium(monkeypatch: pytest.MonkeyPatch):
    async def _fake_score_params(_sid: str, _ver: str, _uid: int):
        return {"position_intent": "long_premium"}

    def _boom_vix(_kite):
        raise AssertionError("VIX quote should not be called for long_premium rows")

    monkeypatch.setattr(ts, "get_strategy_score_params", _fake_score_params)
    monkeypatch.setattr(ts, "_vix_from_quote", _boom_vix)

    rows = [
        {
            "strategy_id": "strat-trendsnap-momentum",
            "strategy_version": "1.0.0",
            "symbol": "NIFTY13APR202623000CE",
            "delta": 0.42,
        }
    ]

    out = asyncio.run(
        ts.filter_recommendations_short_delta_band_only(
            user_id=1,
            kite=object(),  # truthy sentinel to prove VIX would have been attempted previously
            rows=rows,
        )
    )
    assert out == rows
