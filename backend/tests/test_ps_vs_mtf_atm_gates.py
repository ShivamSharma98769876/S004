"""PS_VS_MTF: ATM trade should execute once spot signal is valid."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services import trades_service as ts


def _ist_candles(n: int = 70) -> list[dict]:
    ist = ZoneInfo("Asia/Kolkata")
    base = datetime(2026, 4, 16, 9, 15, tzinfo=ist)
    out: list[dict] = []
    for i in range(n):
        c = 51300.0 + i * 0.4
        t = base + timedelta(minutes=3 * i)
        out.append(
            {
                "time": t.isoformat(),
                "open": c - 1.0,
                "high": c + 2.0,
                "low": c - 2.0,
                "close": c,
                "volume": 1000.0,
            }
        )
    return out


def _chain_atm_pe_high_ivr_low_liq(*, spot: float = 51322.0) -> dict:
    step = 100
    atm = int(round(spot / step) * step)
    chain: list[dict] = []
    for off in (-3, -2, -1, 0, 1, 2, 3):
        strike = atm + off * step
        is_atm = strike == atm
        chain.append(
            {
                "strike": strike,
                "call": {
                    "oi": 50000,
                    "volume": 2500,
                    "ltp": 90.0,
                    "delta": 0.51,
                    "ivr": 8.0,
                    "tradingsymbol": f"BANKNIFTY26423{strike}CE",
                    "vwap": 89.0,
                    "volumeSpikeRatio": 1.0,
                    "oiChgPct": 0.0,
                },
                "put": {
                    "oi": 180 if is_atm else 42000,
                    "volume": 11 if is_atm else 2500,
                    "ltp": 110.2 if is_atm else 84.0,
                    "delta": -0.52,
                    "ivr": 97.1,
                    "tradingsymbol": f"BANKNIFTY26423{strike}PE",
                    "vwap": 108.3,
                    "volumeSpikeRatio": 1.01,
                    "oiChgPct": 0.0,
                },
            }
        )
    return {"spot": spot, "chain": chain, "vix": 15.0}


async def _to_thread_stub(func, /, *args, **kwargs):
    name = getattr(func, "__name__", "")
    if name == "fetch_index_candles_sync":
        return _ist_candles(70)
    if name == "fetch_option_chain_sync":
        return _chain_atm_pe_high_ivr_low_liq()
    return func(*args, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def test_ps_vs_mtf_atm_eligible_despite_high_ivr_low_oi_volume_and_low_conviction() -> None:
    ev = {
        "ok": True,
        "direction": "bear",
        "reason": "bear_ps_below_vs",
        "conviction": 62.5,  # used for confidence display; should not block signal eligibility.
        "metrics": {"rsi3": 44.2, "adx15": 23.0, "ps": 39.1, "vs": 52.8},
    }
    score_params = {
        "settings_timeframe": "3-min",
        "position_intent": "short_premium",
        "score_max": 5,
        # Intentionally strict values that must no longer reject ATM after spot signal ok.
        "strike_min_oi": 250000,
        "strike_min_volume": 25000,
        "ivr_max_threshold": 20.0,
    }
    with patch.object(ts.asyncio, "to_thread", new=_to_thread_stub):
        with patch.object(ts, "pick_primary_expiry_str", return_value="23APR2026"):
            with patch.object(ts, "evaluate_ps_vs_mtf_signal", return_value=ev):
                recs, _scan, _spot = _run(
                    ts._get_live_candidates_ps_vs_mtf(object(), None, 10, score_params)
                )

    assert recs, "expected one recommendation"
    rec = recs[0]
    assert rec.get("option_type") == "PE"
    assert rec.get("side") == "SELL"
    assert rec.get("signal_eligible") is True
    assert str(rec.get("failed_conditions") or "").upper() == "PASS"
    assert float(rec.get("confidence_score") or 0.0) == 62.5
