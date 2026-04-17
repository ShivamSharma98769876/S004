"""PS/VS MTF strategy: config and evaluation smoke tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.strategies.ps_vs_mtf import (
    evaluate_ps_vs_mtf_signal,
    resample_3m_to_15m,
    resolve_ps_vs_mtf_config,
)

_IST = ZoneInfo("Asia/Kolkata")


def test_resolve_ps_vs_mtf_config_defaults():
    c = resolve_ps_vs_mtf_config({})
    assert c["rsiPeriod"] == 9
    assert c["vsWmaPeriod"] == 21
    assert c["minConvictionPct"] == 80.0
    assert c["wVolume"] + c["wPsVs"] + c["wRsi"] + c["wAlign"] + c["wAdx"] == 100


def test_evaluate_insufficient_candles():
    out = evaluate_ps_vs_mtf_signal(
        [],
        resolve_ps_vs_mtf_config({}),
        now_ist=datetime(2026, 4, 12, 10, 15, tzinfo=_IST),
    )
    assert out["ok"] is False
    assert out["reason"] == "insufficient_3m"


def test_resample_empty():
    assert resample_3m_to_15m([]) == []
