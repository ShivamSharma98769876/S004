"""Unit tests for TrendPulse Z signal helpers."""

import math
from datetime import datetime, timedelta, timezone

from app.services.trendpulse_z import (
    build_trendpulse_chart_series,
    detect_cross,
    evaluate_trendpulse_signal,
    htf_bias_from_closes,
    _raw_ps,
    _raw_vs,
    _rolling_z,
)


def test_detect_cross_bullish():
    ps_z = [0.0, 0.0, -0.5, -0.2, 0.3]
    vs_z = [0.0, 0.0, 0.1, 0.0, -0.1]
    assert detect_cross(ps_z, vs_z, 4) == "bullish"


def test_detect_cross_bearish():
    ps_z = [0.0, 0.0, 0.5, 0.2, -0.3]
    vs_z = [0.0, 0.0, -0.1, 0.0, 0.1]
    assert detect_cross(ps_z, vs_z, 4) == "bearish"


def test_htf_bias_bullish_uptrend():
    closes = [float(i) for i in range(100, 200)]
    assert htf_bias_from_closes(closes, 13, 34) == "bullish"


def test_evaluate_insufficient_candles():
    st = [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}] * 10
    htf = [{"open": 1, "high": 1, "low": 1, "close": 1, "volume": 100}] * 10
    r = evaluate_trendpulse_signal(st, htf)
    assert r.ok is False
    assert "Insufficient" in r.reason


def test_rolling_z_nonzero_variance():
    vals = [float(i % 5) for i in range(60)]
    z = _rolling_z(vals, 20)
    assert math.isfinite(z[-1])


def test_chart_series_shows_single_ist_session_day():
    """Regression: x-axis must not mix two calendar days (IST) on the same chart."""
    day1_open = datetime(2025, 1, 2, 3, 45, tzinfo=timezone.utc)  # ~09:15 IST
    st: list[dict] = []
    for i in range(70):
        st.append(
            {
                "time": (day1_open + timedelta(minutes=5 * i)).isoformat(),
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 100.0 + i * 0.02,
                "volume": 1000,
            }
        )
    day2_open = datetime(2025, 1, 3, 3, 45, tzinfo=timezone.utc)
    for i in range(45):
        st.append(
            {
                "time": (day2_open + timedelta(minutes=5 * i)).isoformat(),
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 200.0 + i * 0.02,
                "volume": 1000,
            }
        )
    now = datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc)
    r = build_trendpulse_chart_series(
        st,
        z_window=20,
        slope_lookback=4,
        tail=120,
        now_utc=now,
    )
    assert r.get("displayDate") == "2025-01-03"
    assert len(r["times"]) == 45
    assert r["tail_start_index"] >= r["warmup_bars"]


def test_chart_series_kite_naive_ist_timestamps():
    """Kite returns naive datetimes in exchange local (IST), not UTC."""
    st: list[dict] = []
    for i in range(55):
        t = datetime(2026, 3, 24, 9, 15) + timedelta(minutes=5 * i)
        st.append(
            {
                "time": t.isoformat(),
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 100.0 + i * 0.02,
                "volume": 1000,
            }
        )
    now = datetime(2026, 3, 24, 6, 0, tzinfo=timezone.utc)
    r = build_trendpulse_chart_series(
        st,
        z_window=20,
        slope_lookback=4,
        tail=120,
        now_utc=now,
    )
    assert r["displayDate"] == "2026-03-24"
    assert not r.get("noBarsForDisplayDate")
    assert r["tail_start_index"] == 22
    assert len(r["times"]) == 33
    # Naive Kite times are IST wall; API must emit UTC ...Z for frontend parseBackendUtcNaive.
    assert all(isinstance(t, str) and t.endswith("Z") for t in r["times"])


def test_chart_series_strict_today_no_stale_previous_session():
    """Do not plot Mar 20 when current IST day is Mar 24 and feed has no Mar 24 bars."""
    st: list[dict] = []
    for i in range(80):
        t = datetime(2026, 3, 20, 9, 15) + timedelta(minutes=5 * i)
        st.append(
            {
                "time": t.isoformat(),
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 50.0 + i * 0.02,
                "volume": 1000,
            }
        )
    now = datetime(2026, 3, 24, 8, 0, tzinfo=timezone.utc)
    r = build_trendpulse_chart_series(
        st,
        z_window=20,
        slope_lookback=4,
        tail=120,
        now_utc=now,
    )
    assert r.get("noBarsForDisplayDate") is True
    assert r["displayDate"] == "2026-03-24"
    assert len(r["times"]) == 0
    assert r["chartHint"]


def test_raw_ps_vs_pipeline():
    closes = [100.0 + i * 0.05 for i in range(80)]
    vols = [1000.0 + (i % 7) * 10 for i in range(80)]
    rps = _raw_ps(closes, 4)
    rvs = _raw_vs(vols, 4)
    assert len(rps) == len(closes)
    zps = _rolling_z(rps, 30)
    zvs = _rolling_z(rvs, 30)
    assert len(zps) == len(zvs)
