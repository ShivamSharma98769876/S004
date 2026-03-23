"""Unit tests for strategy day-fit scoring (no DB)."""

from __future__ import annotations

from app.services.strategy_day_fit import (
    build_fit_payload,
    position_intent,
    score_long_premium_row,
    score_short_premium_row,
    strategy_kind,
)


def test_position_intent_defaults_long() -> None:
    assert position_intent({}) == "long_premium"
    assert position_intent({"positionIntent": "short_premium"}) == "short_premium"


def test_strategy_kind() -> None:
    assert strategy_kind({"strategyType": "trendpulse-z"}) == "trendpulse_z"
    assert strategy_kind({"strategyType": "heuristic-voting"}) == "heuristic_voting"
    assert strategy_kind({}) == "rule_based"


def test_scores_bounded() -> None:
    meta = {
        "strategy_id": "x",
        "version": "1",
        "display_name": "X",
        "description": "",
        "risk_profile": "MEDIUM",
        "details": {"strategyType": "trendpulse-z", "positionIntent": "long_premium"},
    }
    sentiment = {
        "directionLabel": "BULLISH",
        "directionScore": 80,
        "confidence": 90,
        "regime": "TRENDING",
    }
    tp = {"tradeSignal": {"entryEligible": True}, "htfBias": "bullish"}
    sc, reasons = score_long_premium_row(meta, sentiment, tp)
    assert 0 <= sc <= 100
    assert len(reasons) >= 1

    meta_s = {
        **meta,
        "details": {"positionIntent": "short_premium"},
    }
    sc2, r2 = score_short_premium_row(meta_s, sentiment, tp)
    assert 0 <= sc2 <= 100
    assert len(r2) >= 1


def test_build_fit_payload_ranks_short_and_long() -> None:
    from datetime import date

    catalog = [
        {
            "strategy_id": "long-a",
            "version": "1.0.0",
            "display_name": "Long A",
            "description": "",
            "risk_profile": "MEDIUM",
            "details": {"strategyType": "rule-based"},
        },
        {
            "strategy_id": "short-a",
            "version": "1.0.0",
            "display_name": "Short A",
            "description": "",
            "risk_profile": "HIGH",
            "details": {"positionIntent": "short_premium"},
        },
    ]
    sentiment = {
        "directionLabel": "NEUTRAL",
        "directionScore": 0,
        "confidence": 50,
        "regime": "RANGE_CHOP",
    }
    payload = build_fit_payload(catalog, sentiment, {}, {}, fit_date=date(2025, 1, 1), from_history=False)
    assert payload["buyerPick"]["strategyId"] == "long-a"
    assert payload["sellerPick"]["strategyId"] == "short-a"
    assert "picksJson" in payload
