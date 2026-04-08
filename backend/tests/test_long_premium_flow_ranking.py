"""Long-premium flow ranking (TrendSnap-style strike selection)."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services import trades_service as ts


def test_parse_flow_ranking_cfg_disabled() -> None:
    assert ts._parse_flow_ranking_cfg(None) is None
    assert ts._parse_flow_ranking_cfg({}) is None
    assert ts._parse_flow_ranking_cfg({"enabled": False}) is None


def test_parse_flow_ranking_cfg_enabled() -> None:
    cfg = ts._parse_flow_ranking_cfg({"enabled": True})
    assert cfg is not None
    assert cfg["use_chain_flow_tilt"] is True
    assert cfg["tilt_weight"] == 0.22


def test_percentile_rank_map_orders_by_metric() -> None:
    rows = [
        {"oi": 100, "x": 1},
        {"oi": 500, "x": 2},
        {"oi": 300, "x": 3},
    ]
    m = ts._percentile_rank_map(rows, "oi")
    assert m[id(rows[1])] == 1.0
    assert m[id(rows[0])] == 0.0
    assert m[id(rows[2])] == pytest.approx(0.5)


def test_long_premium_rec_sort_key_uses_flow_score() -> None:
    a = {"score": 3, "flow_rank_score": 1.0, "volume_spike_ratio": 1.0, "oi_chg_pct": 0, "delta_distance": 0.1, "distance_to_atm": 1}
    b = {"score": 3, "flow_rank_score": 2.0, "volume_spike_ratio": 1.0, "oi_chg_pct": 0, "delta_distance": 0.1, "distance_to_atm": 1}
    ranked = sorted([a, b], key=ts._long_premium_rec_sort_key)
    assert ranked[0] is b


@patch("app.services.trades_service.compute_sentiment_snapshot")
def test_apply_long_premium_flow_ranking_sets_scores(mock_snap: MagicMock) -> None:
    mock_snap.return_value = {
        "optionsIntel": {"modelOptionTilt": "CE", "flowBlendScore": 0.08},
    }
    chain: list = []
    payload = {"pcr": 1.0, "pcrVol": 1.0, "spotChgPct": 0.3}
    recs = [
        {
            "option_type": "CE",
            "oi": 10000,
            "volume": 500,
            "oi_chg_pct": 5.0,
            "buildup": "Long Buildup",
        },
        {
            "option_type": "CE",
            "oi": 8000,
            "volume": 400,
            "oi_chg_pct": 0.0,
            "buildup": "—",
        },
    ]
    cfg = ts._parse_flow_ranking_cfg({"enabled": True}) or {}
    meta = ts._apply_long_premium_flow_ranking(recs, chain, payload, cfg)
    assert meta.get("flow_ranking", {}).get("landing_flow_tilt") == "CE"
    assert recs[0]["flow_rank_score"] > recs[1]["flow_rank_score"]


@patch("app.services.trades_service.compute_sentiment_snapshot")
def test_short_covering_gets_flow_bonus(mock_snap: MagicMock) -> None:
    mock_snap.return_value = {"optionsIntel": {"modelOptionTilt": "NEUTRAL", "flowBlendScore": 0.0}}
    rec_lb = {
        "option_type": "CE",
        "oi": 5000,
        "volume": 300,
        "oi_chg_pct": 0.0,
        "buildup": "Long Buildup",
    }
    rec_sc = {
        "option_type": "CE",
        "oi": 5000,
        "volume": 300,
        "oi_chg_pct": 0.0,
        "buildup": "Short Covering",
    }
    rec_flat = {
        "option_type": "CE",
        "oi": 5000,
        "volume": 300,
        "oi_chg_pct": 0.0,
        "buildup": "—",
    }
    cfg = ts._parse_flow_ranking_cfg({"enabled": True, "useChainFlowTilt": False}) or {}
    ts._apply_long_premium_flow_ranking([rec_lb], [], {"pcr": 1.0, "pcrVol": 1.0, "spotChgPct": 0.0}, cfg)
    ts._apply_long_premium_flow_ranking([rec_sc], [], {"pcr": 1.0, "pcrVol": 1.0, "spotChgPct": 0.0}, cfg)
    ts._apply_long_premium_flow_ranking([rec_flat], [], {"pcr": 1.0, "pcrVol": 1.0, "spotChgPct": 0.0}, cfg)
    assert rec_lb["flow_rank_score"] > rec_flat["flow_rank_score"]
    assert rec_sc["flow_rank_score"] > rec_flat["flow_rank_score"]
    assert rec_lb["flow_rank_score"] > rec_sc["flow_rank_score"]  # default longBuildup > shortCovering


def test_pin_wall_strikes_respects_dominance_ratio() -> None:
    chain = [
        {"strike": 22000, "call": {"oi": "90000"}, "put": {"oi": "1000"}},
        {"strike": 21950, "call": {"oi": "80000"}, "put": {"oi": "900"}},
    ]
    w_lo, _ = ts._pin_wall_strikes_from_chain(chain, dominance_ratio=1.01)
    assert w_lo == 22000
    w_hi, _ = ts._pin_wall_strikes_from_chain(chain, dominance_ratio=1.2)
    assert w_hi is None


@patch("app.services.trades_service.datetime")
def test_pin_penalty_only_when_calendar_dte_zero(mock_dt: MagicMock) -> None:
    ist = ZoneInfo("Asia/Kolkata")
    mock_dt.now.return_value = datetime(2026, 3, 27, 10, 0, 0, tzinfo=ist)
    chain = [
        {"strike": 22000, "call": {"oi": "100000"}, "put": {"oi": "5000"}},
        {"strike": 21950, "call": {"oi": "40000"}, "put": {"oi": "3000"}},
    ]
    cfg = ts._parse_flow_ranking_cfg(
        {"enabled": True, "useChainFlowTilt": False, "pinPenaltyOnExpiryDay": True}
    )
    assert cfg is not None
    payload = {"spot": 22020.0, "pcr": 1.0, "pcrVol": 1.0, "spotChgPct": 0.0}
    rec_dte1 = {
        "option_type": "CE",
        "strike": 22000,
        "oi": 100_000,
        "volume": 500,
        "oi_chg_pct": 0.0,
        "buildup": "—",
    }
    rec_dte0 = {
        "option_type": "CE",
        "strike": 22000,
        "oi": 100_000,
        "volume": 500,
        "oi_chg_pct": 0.0,
        "buildup": "—",
    }
    with patch(
        "app.services.trades_service.compute_sentiment_snapshot",
        return_value={"optionsIntel": {"modelOptionTilt": "NEUTRAL", "flowBlendScore": 0.0}},
    ):
        meta1 = ts._apply_long_premium_flow_ranking(
            [rec_dte1], chain, payload, cfg, expiry_date=date(2026, 3, 28)
        )
        meta0 = ts._apply_long_premium_flow_ranking(
            [rec_dte0], chain, payload, cfg, expiry_date=date(2026, 3, 27)
        )
    pin1 = meta1["flow_ranking"]["pin_expiry_soft_penalty"]
    assert pin1.get("dte_ist") == 1
    assert pin1["active"] is False
    assert rec_dte1.get("flow_pin_penalized") is not True

    pin0 = meta0["flow_ranking"]["pin_expiry_soft_penalty"]
    assert pin0.get("dte_ist") == 0
    assert pin0["active"] is True
    assert rec_dte0.get("flow_pin_penalized") is True
