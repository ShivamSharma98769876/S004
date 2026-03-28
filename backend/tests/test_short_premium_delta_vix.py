"""Short-premium signed CE/PE delta gates and optional India VIX switching."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services import trades_service as ts


def _bands() -> dict:
    raw = {
        "shortPremiumDeltaVixBands": {
            "threshold": 17,
            "vixAbove": {
                "deltaMinCE": 0.29,
                "deltaMaxCE": 0.35,
                "deltaMinPE": -0.35,
                "deltaMaxPE": -0.29,
            },
            "vixAtOrBelow": {
                "deltaMinCE": 0.32,
                "deltaMaxCE": 0.38,
                "deltaMinPE": -0.38,
                "deltaMaxPE": -0.32,
            },
        }
    }
    b = ts._normalize_short_premium_delta_vix_bands(raw)
    assert b is not None
    return b


def test_normalize_short_premium_delta_vix_bands_parses() -> None:
    assert _bands()["threshold"] == 17.0


def test_resolve_delta_high_vix_uses_vix_above_band() -> None:
    ce_lo, ce_hi, pe_lo, pe_hi, note = ts._resolve_short_premium_delta_corners(
        strike_delta_min_abs=0.29,
        strike_delta_max_abs=0.35,
        short_premium_delta_vix_bands=_bands(),
        vix=18.5,
    )
    assert (ce_lo, ce_hi) == (0.29, 0.35)
    assert (pe_lo, pe_hi) == (-0.35, -0.29)
    assert "18.50" in note and ">" in note


def test_resolve_delta_low_vix_uses_vix_at_or_below_band() -> None:
    ce_lo, ce_hi, pe_lo, pe_hi, _note = ts._resolve_short_premium_delta_corners(
        strike_delta_min_abs=0.29,
        strike_delta_max_abs=0.35,
        short_premium_delta_vix_bands=_bands(),
        vix=17.0,
    )
    assert (ce_lo, ce_hi) == (0.32, 0.38)
    assert (pe_lo, pe_hi) == (-0.38, -0.32)


def test_resolve_delta_missing_vix_falls_back_to_delta_min_max() -> None:
    ce_lo, ce_hi, pe_lo, pe_hi, note = ts._resolve_short_premium_delta_corners(
        strike_delta_min_abs=0.29,
        strike_delta_max_abs=0.35,
        short_premium_delta_vix_bands=_bands(),
        vix=None,
    )
    assert (ce_lo, ce_hi) == (0.29, 0.35)
    assert (pe_lo, pe_hi) == (-0.35, -0.29)
    assert "unavailable" in note.lower()


def test_signed_delta_ok_rejects_wrong_sign() -> None:
    assert not ts._short_premium_signed_delta_ok(
        -0.31, "CE", ce_lo=0.29, ce_hi=0.35, pe_lo=-0.35, pe_hi=-0.29
    )
    assert ts._short_premium_signed_delta_ok(
        0.31, "CE", ce_lo=0.29, ce_hi=0.35, pe_lo=-0.35, pe_hi=-0.29
    )
    assert not ts._short_premium_signed_delta_ok(
        0.31, "PE", ce_lo=0.29, ce_hi=0.35, pe_lo=-0.35, pe_hi=-0.29
    )
    assert ts._short_premium_signed_delta_ok(
        -0.31, "PE", ce_lo=0.29, ce_hi=0.35, pe_lo=-0.35, pe_hi=-0.29
    )


async def _to_thread_sync(func, /, *args, **kwargs):
    return func(*args, **kwargs)


def _call_leg(**kw: object) -> dict:
    d: dict = {
        "oi": 20000,
        "volume": 1000,
        "ltp": 150.0,
        "delta": 0.34,
        "ivr": 40.0,
        "regimeSellCe": True,
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
        "tradingsymbol": "NIFTY26MAR22100CE",
    }
    d.update(kw)
    return d


def _chain_payload(*, vix: float, ce_delta: float) -> dict:
    return {
        "spot": 22000.0,
        "vix": vix,
        "spotRegime": None,
        "spotBullishScore": 0,
        "spotBearishScore": 0,
        "chain": [
            {
                "strike": 22100,
                "call": _call_leg(delta=ce_delta, regimeSellCe=True),
                "put": {
                    "oi": 20000,
                    "volume": 1000,
                    "ltp": 120.0,
                    "delta": -0.34,
                    "ivr": 40.0,
                    "regimeSellPe": True,
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
                    "tradingsymbol": "NIFTY26MAR22100PE",
                },
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


def test_live_candidates_vix_low_accepts_wider_ce_delta(mock_chain) -> None:
    """VIX 16 → CE band up to 0.38; delta 0.34 should pass."""
    mock_chain.return_value = _chain_payload(vix=16.0, ce_delta=0.34)
    gen, _scan, meta = _run(
        ts._get_live_candidates(
            None,
            10,
            position_intent="short_premium",
            spot_regime_mode="ema_cross_vwap",
            strike_min_oi=0,
            strike_min_volume=0,
            score_threshold=3,
            short_premium_delta_vix_bands=_bands(),
        )
    )
    assert any(r["option_type"] == "CE" and r.get("signal_eligible") for r in gen)
    assert "0.32" in meta.get("short_premium_delta_abs", "")


def test_live_candidates_vix_high_rejects_ce_outside_tight_band(mock_chain) -> None:
    """VIX 20 → CE max 0.35; delta 0.37 should not produce eligible CE."""
    mock_chain.return_value = _chain_payload(vix=20.0, ce_delta=0.37)
    gen, _scan, _meta = _run(
        ts._get_live_candidates(
            None,
            10,
            position_intent="short_premium",
            spot_regime_mode="ema_cross_vwap",
            strike_min_oi=0,
            strike_min_volume=0,
            score_threshold=3,
            short_premium_delta_vix_bands=_bands(),
        )
    )
    assert not any(r["option_type"] == "CE" and r.get("signal_eligible") for r in gen)
