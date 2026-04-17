"""Long-premium failed_conditions rebuild + gate alignment with indicator pack rounding."""

from app.services.trades_service import (
    _failed_conditions,
    _long_premium_gates_from_rounded_metrics,
    _refresh_long_leg_failed_conditions_from_snapshot,
)


def test_rounded_metrics_match_strict_pack():
    p, e = _long_premium_gates_from_rounded_metrics(
        {"entry_price": 177.5, "vwap": 145.88, "ema9": 176.76, "ema21": 163.3},
        strict_bullish=True,
    )
    assert p is True and e is True


def test_strict_tie_at_vwap_fails_primary():
    p, e = _long_premium_gates_from_rounded_metrics(
        {"entry_price": 100.0, "vwap": 100.0, "ema9": 101.0, "ema21": 99.0},
        strict_bullish=True,
    )
    assert p is False


def test_failed_conditions_rsi_above_band_message():
    msg = _failed_conditions(
        True,
        True,
        False,
        rsi_min=50,
        rsi_max=75,
        strict_bullish=True,
        rsi_value=80.5,
    )
    assert "above band" in msg
    assert "80.50" in msg or "80.5" in msg


def test_refresh_reconcile_updates_flags_and_message():
    merged = {
        "threshold_failed_style": "long",
        "primary_ok": False,
        "ema_ok": False,
        "rsi_ok": False,
        "volume_ok": False,
        "entry_price": 177.5,
        "vwap": 145.88,
        "ema9": 176.76,
        "ema21": 163.3,
        "rsi": 80.32,
        "volume_spike_ratio": 1.1,
        "threshold_rsi_min": 50,
        "threshold_rsi_max": 100,
        "threshold_volume_min_ratio": 1.02,
        "include_volume_in_leg_score": True,
        "threshold_strict_bullish_comparisons": True,
    }
    _refresh_long_leg_failed_conditions_from_snapshot(
        merged,
        reconcile_leg_metrics=True,
        rescore_without_crossover=True,
        score_max_for_confidence=4,
    )
    assert merged["primary_ok"] is True
    assert merged["ema_ok"] is True
    assert merged["rsi_ok"] is True
    assert merged["volume_ok"] is True
    assert merged["failed_conditions"] == "PASS"
    assert merged["score"] == 4
    # Same formula as _get_live_candidates long leg: (score/score_max)*100 + volume bonus, cap 99.
    assert merged["confidence_score"] == 99.0
