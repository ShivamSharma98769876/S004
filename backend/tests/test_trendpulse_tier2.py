"""Tests for TrendPulse Z Tier-2 helpers."""

from datetime import datetime, timezone

import pytest

from app.services.trendpulse_tier2 import (
    delta_abs_in_band,
    option_extrinsic_share,
    trendpulse_opening_window_blocked,
)


def test_extrinsic_share_ce_itm():
    # spot 25000, strike 24800 CE -> intrinsic 200; premium 250 -> share = 50/250 = 0.2
    assert option_extrinsic_share(250.0, 25000.0, 24800, "CE") == pytest.approx(0.2)


def test_extrinsic_share_pe():
    # PE strike 25200 spot 25000 -> intrinsic 200; premium 250 -> TV share 50/250
    assert option_extrinsic_share(250.0, 25000.0, 25200, "PE") == pytest.approx(0.2)


def test_extrinsic_share_zero_premium():
    assert option_extrinsic_share(0.0, 25000.0, 25000, "CE") is None


def test_delta_band():
    assert delta_abs_in_band(0.45, 0.40, 0.50) is True
    assert delta_abs_in_band(-0.42, 0.40, 0.50) is True
    assert delta_abs_in_band(0.35, 0.40, 0.50) is False


def test_opening_block_weekday_morning_ist():
    # 2026-03-24 Tuesday 09:17 IST = 03:47 UTC
    dt = datetime(2026, 3, 24, 3, 47, 0, tzinfo=timezone.utc)
    assert trendpulse_opening_window_blocked(dt) is True


def test_opening_block_after_window():
    dt = datetime(2026, 3, 24, 4, 0, 0, tzinfo=timezone.utc)  # 09:30 IST
    assert trendpulse_opening_window_blocked(dt) is False
