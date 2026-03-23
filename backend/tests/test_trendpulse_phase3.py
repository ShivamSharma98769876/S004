"""Tests for TrendPulse Z Phase 3 — profiles, session, breadth gates."""

from datetime import datetime, timezone

from app.services.trendpulse_phase3 import (
    apply_trendpulse_hard_gates,
    resolve_trendpulse_z_config,
    session_block_reason,
)
from app.services.trendpulse_z import TrendPulseEval


def test_resolve_conservative_preset():
    c = resolve_trendpulse_z_config({"profile": "conservative"})
    assert c["zWindow"] == 80
    assert c["slopeLookback"] == 6
    assert c["adxMin"] == 22.0
    assert c["ivRankMaxPercentile"] == 60.0
    assert c["profile"] == "conservative"


def test_explicit_fields_override_profile():
    c = resolve_trendpulse_z_config({"profile": "conservative", "zWindow": 55, "adxMin": 19.0})
    assert c["zWindow"] == 55
    assert c["adxMin"] == 19.0
    assert c["profile"] == "conservative"


def test_risk_profile_alias():
    c = resolve_trendpulse_z_config({"riskProfile": "aggressive"})
    assert c["zWindow"] == 40
    assert c["profile"] == "aggressive"


def test_session_weekend_blocked():
    # Saturday 2025-03-22
    dt = datetime(2025, 3, 22, 4, 0, tzinfo=timezone.utc)
    msg = session_block_reason(dt, {"enabled": True, "blockFirstMinutes": 0, "blockLastMinutes": 0})
    assert msg is not None
    assert "weekend" in msg.lower()


def test_session_first_minutes_blocked_ist():
    # Monday 2025-03-24 09:20 IST = 03:50 UTC
    dt = datetime(2025, 3, 24, 3, 50, tzinfo=timezone.utc)
    msg = session_block_reason(
        dt,
        {
            "enabled": True,
            "blockFirstMinutes": 15,
            "blockLastMinutes": 0,
            "timezone": "Asia/Kolkata",
        },
    )
    assert msg is not None
    assert "first" in msg.lower()


def test_apply_breadth_blocks_weak_spot_bullish():
    ev = TrendPulseEval(True, "bullish", "bullish", 0.5, 0.2, 22.0, "OK")
    tpc = resolve_trendpulse_z_config(
        {
            "breadth": {
                "enabled": True,
                "requireSpotAligned": True,
                "minAbsSpotChgPct": 0.1,
                "requirePcrAligned": False,
            }
        }
    )
    out = apply_trendpulse_hard_gates(
        ev,
        tpc,
        spot_chg_pct=0.02,
        pcr=1.0,
        now_utc=datetime(2025, 3, 24, 6, 0, tzinfo=timezone.utc),
    )
    assert out.ok is False
    assert "Breadth" in out.reason


def test_apply_session_blocks_before_breadth():
    ev = TrendPulseEval(True, "bullish", "bullish", 0.5, 0.2, 22.0, "OK")
    tpc = resolve_trendpulse_z_config(
        {
            "session": {"enabled": True, "blockFirstMinutes": 15, "blockLastMinutes": 0},
            "breadth": {
                "enabled": True,
                "requireSpotAligned": True,
                "minAbsSpotChgPct": 0.5,
            },
        }
    )
    # Same moment as first-minutes block
    now = datetime(2025, 3, 24, 3, 50, tzinfo=timezone.utc)
    out = apply_trendpulse_hard_gates(ev, tpc, spot_chg_pct=2.0, pcr=0.9, now_utc=now)
    assert out.ok is False
    assert "Session filter" in out.reason
