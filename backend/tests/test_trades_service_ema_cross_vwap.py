"""Short premium + spotRegimeMode ema_cross_vwap: candidates use per-leg regimeSellPe / regimeSellCe only."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import trades_service as ts


async def _to_thread_sync(func, /, *args, **kwargs):
    """Avoid thread-pool + asyncio.run hangs in tests; chain fetch stays synchronous."""
    return func(*args, **kwargs)


def _call_leg(**kw: object) -> dict:
    d: dict = {
        "oi": 20000,
        "volume": 1000,
        "ltp": 150.0,
        "delta": 0.35,
        "ivr": 40.0,
        "regimeSellCe": False,
        "signalEligible": True,
        "score": 4,
        "primaryOk": True,
        "emaOk": True,
        "rsiOk": True,
        "volumeOk": True,
        "emaCrossoverOk": True,
        "vwap": 140.0,
        "ema9": 148.0,
        "ema21": 145.0,
        "rsi": 55.0,
        "avgVolume": 800.0,
        "volumeSpikeRatio": 1.2,
        "oiChgPct": 1.0,
        "tradingsymbol": "NIFTY26MAR22000CE",
    }
    d.update(kw)
    return d


def _put_leg(**kw: object) -> dict:
    d: dict = {
        "oi": 20000,
        "volume": 1000,
        "ltp": 120.0,
        "delta": -0.35,
        "ivr": 40.0,
        "regimeSellPe": False,
        "signalEligible": True,
        "score": 4,
        "primaryOk": True,
        "emaOk": True,
        "rsiOk": True,
        "volumeOk": True,
        "emaCrossoverOk": True,
        "vwap": 110.0,
        "ema9": 118.0,
        "ema21": 115.0,
        "rsi": 55.0,
        "avgVolume": 800.0,
        "volumeSpikeRatio": 1.2,
        "oiChgPct": 1.0,
        "tradingsymbol": "NIFTY26MAR22000PE",
    }
    d.update(kw)
    return d


def _chain_payload(*, put_regime: bool, call_regime: bool) -> dict:
    return {
        "spot": 22000.0,
        "spotRegime": None,
        "spotBullishScore": 0,
        "spotBearishScore": 0,
        "chain": [
            {
                "strike": 22000,
                "call": _call_leg(regimeSellCe=call_regime),
                "put": _put_leg(regimeSellPe=put_regime),
            }
        ],
    }


@pytest.fixture
def mock_chain():
    with patch.object(ts.asyncio, "to_thread", new=_to_thread_sync):
        with patch.object(ts, "pick_primary_expiry_str", return_value="26MAR2026"):
            with patch.object(ts, "fetch_option_chain_sync") as m_fetch:
                yield m_fetch


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "put_regime,call_regime,expect_pe,expect_ce",
    [
        (True, False, True, False),
        (False, True, False, True),
        (False, False, False, False),
        (True, True, True, True),
    ],
)
def test_ema_cross_vwap_respects_strike_leg_flags(
    mock_chain, put_regime, call_regime, expect_pe, expect_ce
):
    mock_chain.return_value = _chain_payload(put_regime=put_regime, call_regime=call_regime)
    gen, _scan = _run(
        ts._get_live_candidates(
            None,
            10,
            position_intent="short_premium",
            spot_regime_mode="ema_cross_vwap",
            strike_min_oi=0,
            strike_min_volume=0,
            score_threshold=3,
        )
    )
    recs = gen
    pe = [r for r in recs if r["option_type"] == "PE"]
    ce = [r for r in recs if r["option_type"] == "CE"]
    eligible_pe = [r for r in pe if r.get("signal_eligible")]
    eligible_ce = [r for r in ce if r.get("signal_eligible")]
    assert bool(eligible_pe) is expect_pe
    assert bool(eligible_ce) is expect_ce


def test_legacy_short_premium_skips_when_spot_regime_unset(mock_chain):
    """Without ema_cross_vwap, missing spotRegime skips legs even if strike flags are true."""
    mock_chain.return_value = _chain_payload(put_regime=True, call_regime=True)
    gen, _scan = _run(
        ts._get_live_candidates(
            None,
            10,
            position_intent="short_premium",
            spot_regime_mode="",
            strike_min_oi=0,
            strike_min_volume=0,
            score_threshold=3,
        )
    )
    recs = gen
    eligible = [r for r in recs if r.get("signal_eligible")]
    assert eligible == []
