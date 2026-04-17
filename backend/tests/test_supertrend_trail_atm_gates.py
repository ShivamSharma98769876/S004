"""SuperTrendTrail: ATM short leg skips IVR/OI/volume gates (spot signal + LTP>0 only)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from app.services import trades_service as ts


def _ist_candles(n: int = 50) -> list[dict]:
    ist = ZoneInfo("Asia/Kolkata")
    base = datetime(2026, 4, 16, 9, 15, tzinfo=ist)
    out: list[dict] = []
    for i in range(n):
        t = base + timedelta(minutes=3 * i)
        c = 24140.0 + i * 0.2
        out.append(
            {
                "time": t.isoformat(),
                "open": c,
                "high": c + 2.0,
                "low": c - 2.0,
                "close": c,
                "volume": 1000.0,
            }
        )
    return out


def _chain_atm_pe_high_ivr(*, spot: float = 24146.75) -> dict:
    step = 50
    atm = int(round(spot / step) * step)
    chain: list[dict] = []
    for off in (-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5):
        st = atm + off * step
        is_atm = st == atm
        chain.append(
            {
                "strike": st,
                "call": {
                    "oi": 100,
                    "volume": 10,
                    "ltp": 50.0,
                    "delta": 0.45,
                    "ivr": 95.0,
                    "tradingsymbol": f"NIFTY264212{st}CE",
                    "vwap": 48.0,
                    "volumeSpikeRatio": 1.0,
                    "oiChgPct": 0.0,
                },
                "put": {
                    "oi": 100 if is_atm else 5000,
                    "volume": 10 if is_atm else 500,
                    "ltp": 199.25 if is_atm else 80.0,
                    "delta": -0.48,
                    "ivr": 92.6,
                    "tradingsymbol": f"NIFTY264212{st}PE",
                    "vwap": 185.0,
                    "volumeSpikeRatio": 1.01,
                    "oiChgPct": 0.0,
                },
            }
        )
    return {"spot": spot, "chain": chain, "vix": 15.0}


async def _to_thread_stub(func, /, *args, **kwargs):
    name = getattr(func, "__name__", "")
    if name == "fetch_index_candles_sync":
        return _ist_candles(50)
    if name == "fetch_option_chain_sync":
        return _chain_atm_pe_high_ivr()
    return func(*args, **kwargs)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def st_patches():
    ev = {
        "ok": True,
        "direction": "bull",
        "reason": "bull_pullback_close_below_slow_ema_sell_pe",
        "metrics": {
            "ema10": 24156.30,
            "ema20": 24150.64,
            "supertrend_upper": 24213.07,
            "supertrend_lower": 24120.44,
            "close": 24146.75,
            "close_prev": 24145.0,
            "low": 24140.0,
            "high": 24148.0,
            "inside_run": 1,
        },
    }
    snap = {"st_direction": 1, "close": 24146.75, "ema10": 24156.30, "ema20": 24150.64}
    with patch.object(ts.asyncio, "to_thread", new=_to_thread_stub):
        with patch.object(ts, "pick_expiry_with_min_calendar_dte", return_value="16APR2026"):
            with patch.object(ts, "evaluate_supertrend_trail_signal", return_value=ev):
                with patch.object(ts, "snapshot_supertrend_state", return_value=snap):
                    yield


def test_supertrend_trail_atm_eligible_despite_high_ivr_low_oi_volume(st_patches) -> None:
    """Regression: old gates would reject ATM PE with ivr 92.6 and OI/vol below catalog mins."""
    score_params = {
        "settings_timeframe": "3-min",
        "score_max": 5,
        "strike_min_oi": 10000,
        "strike_min_volume": 500,
        "ivr_max_threshold": 20.0,
        "supertrend_trail_config": {
            "minDteCalendarDays": 2,
            "niftyWeeklyExpiryWeekday": "TUE",
        },
    }
    gen, scan, _spot = _run(
        ts._get_live_candidates_supertrend_trail(object(), None, 10, score_params)
    )
    atm_rows = [r for r in scan if r.get("distance_to_atm") == 0 and r.get("option_type") == "PE"]
    assert atm_rows, "expected ATM PE scan row"
    assert atm_rows[0].get("signal_eligible") is True, atm_rows[0].get("failed_conditions")
    assert "ivr" not in str(atm_rows[0].get("failed_conditions") or "").lower()
    assert gen, "expected one persistable recommendation"
    assert gen[0].get("option_type") == "PE"
    assert gen[0].get("signal_eligible") is True
