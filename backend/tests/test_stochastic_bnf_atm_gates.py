"""StochasticBNF: ATM trade should execute once spot signal is valid."""

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
        c = 51200.0 + i * 0.5
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


def _chain_atm_ce_high_ivr_low_liq(*, spot: float = 51234.0) -> dict:
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
                    "oi": 120 if is_atm else 40000,
                    "volume": 12 if is_atm else 3000,
                    "ltp": 120.5 if is_atm else 80.0,
                    "delta": 0.49,
                    "ivr": 96.4,
                    "tradingsymbol": f"BANKNIFTY26423{strike}CE",
                    "vwap": 118.0,
                    "volumeSpikeRatio": 1.02,
                    "oiChgPct": 0.0,
                },
                "put": {
                    "oi": 50000,
                    "volume": 2500,
                    "ltp": 78.0,
                    "delta": -0.51,
                    "ivr": 11.0,
                    "tradingsymbol": f"BANKNIFTY26423{strike}PE",
                    "vwap": 79.0,
                    "volumeSpikeRatio": 1.0,
                    "oiChgPct": 0.0,
                },
            }
        )
    return {"spot": spot, "chain": chain, "vix": 14.0}


async def _to_thread_stub(func, /, *args, **kwargs):
    name = getattr(func, "__name__", "")
    if name == "fetch_index_candles_sync":
        return _ist_candles(70)
    if name == "fetch_option_chain_sync":
        return _chain_atm_ce_high_ivr_low_liq()
    return func(*args, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def test_stochastic_bnf_atm_eligible_despite_high_ivr_low_oi_volume() -> None:
    ev = {
        "ok": True,
        "direction": "bear",
        "reason": "bear_stoch_cross_down",
        "metrics": {
            "ema5": 51182.4,
            "ema15": 51198.2,
            "ema50": 51224.3,
            "adx": 28.7,
            "stochK": 74.0,
            "stochD": 78.3,
            "vwap": 51205.1,
        },
    }
    score_params = {
        "settings_timeframe": "3-min",
        "score_max": 5,
        # These would have blocked before; now they must be ignored for ATM after spot signal is ok.
        "strike_min_oi": 200000,
        "strike_min_volume": 20000,
        "ivr_max_threshold": 20.0,
        "stochastic_bnf_config": {"minDteCalendarDays": 2},
    }
    with patch.object(ts.asyncio, "to_thread", new=_to_thread_stub):
        with patch.object(
            ts,
            "pick_banknifty_tuesday_2_trading_dte_expiry",
            return_value="23APR2026",
        ):
            with patch.object(ts, "evaluate_stochastic_bnf_signal", return_value=ev):
                gen, scan, _spot = _run(
                    ts._get_live_candidates_stochastic_bnf(object(), None, 10, score_params)
                )

    atm_rows = [r for r in scan if r.get("distance_to_atm") == 0 and r.get("option_type") == "CE"]
    assert atm_rows, "expected ATM CE scan row"
    assert atm_rows[0].get("signal_eligible") is True, atm_rows[0].get("failed_conditions")
    assert str(atm_rows[0].get("failed_conditions") or "").upper() == "PASS"
    assert gen, "expected one persistable recommendation"
    assert gen[0].get("option_type") == "CE"
    assert gen[0].get("signal_eligible") is True
