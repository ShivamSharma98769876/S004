"""Evaluation log writes human-readable .log files for recommendation snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import trades_service as ts
from app.services.evaluation_log import format_evaluation_event_text


def test_emit_evaluation_snapshot_writes_readable_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S004_EVALUATION_LOG_DIR", str(tmp_path))
    row = {
        "symbol": "NIFTY26MAR22000PE",
        "instrument": "NIFTY",
        "expiry": "26MAR2026",
        "side": "SELL",
        "option_type": "PE",
        "distance_to_atm": 0,
        "signal_eligible": True,
        "score": 4,
        "confidence_score": 92.0,
        "failed_conditions": "PASS",
        "delta": -0.32,
        "gamma": 0.01,
        "ivr": 40.0,
        "oi": 10000,
        "volume_spike_ratio": 1.2,
        "entry_price": 100.0,
        "ema9": 101.0,
        "ema21": 99.0,
        "vwap": 100.5,
        "rsi": 55.0,
    }
    ts._emit_evaluation_snapshot(
        trigger_user_id=1,
        strategy_id="strat-test",
        strategy_version="1.0.0",
        strategy_type="rule-based",
        subscribed_user_ids=[1],
        score_params={
            "score_threshold": 3,
            "score_max": 4,
            "auto_trade_score_threshold": 4.0,
            "position_intent": "long_premium",
            "include_ema_crossover_in_score": False,
            "strict_bullish_comparisons": True,
        },
        fetch_failed=False,
        error=None,
        generated_rows=[row],
        scanned_candidates=[row, {**row, "symbol": "NIFTY26MAR22000CE", "option_type": "CE"}],
        chain_snapshot={
            "option_expiry": "07APR2026",
            "chain_rows": 11,
            "calendar_dte_ist": 11,
            "short_leg_diagnostics": [
                {
                    "symbol": "NIFTY07APR22000PE",
                    "strike": 22000,
                    "option_type": "PE",
                    "distance_to_atm": 0,
                    "ltp": 100.0,
                    "delta": -0.31,
                    "delta_abs": 0.31,
                    "ivr": 40.0,
                    "oi": 5000,
                    "volume": 100,
                    "volume_spike_ratio": 1.01,
                    "ema9": 101.0,
                    "ema21": 99.0,
                    "vwap": 100.5,
                    "rsi": 55.0,
                    "regime_sell_pe": False,
                    "regime_sell_ce": False,
                    "leg_score": 2,
                    "leg_signal_eligible": False,
                    "ema_crossover_ok": False,
                    "blockers": "regimeSellPe=false",
                    "would_pass_non_liquidity_gates": False,
                },
            ],
        },
    )
    day_dirs = list(tmp_path.iterdir())
    assert len(day_dirs) == 1
    files = list(day_dirs[0].glob("*.log"))
    assert len(files) >= 1
    text = files[0].read_text(encoding="utf-8")
    assert "strat-test" in text and "1.0.0" in text
    assert "Scanned candidates: 2" in text
    assert "Persisted rows:     1" in text
    assert "NIFTY26MAR22000PE" in text
    assert "Time (IST):" in text
    assert "Recommendation evaluation snapshot" in text
    assert "Auto-trade score:   4.0" in text
    assert "steps_from_ATM=0" in text
    assert "E9=101.00" in text and "RSI=55.00" in text
    assert "per-leg diagnostics" in text
    assert "NIFTY07APR22000PE" in text
    assert "failed: regimeSellPe=false" in text
    assert "side=SELL" in text


def test_short_premium_compact_log_shows_gate_and_in_band_strikes_only() -> None:
    event: dict = {
        "ts_ist": "2026-03-25T10:00:00+05:30",
        "strategy_id": "strat-nifty-ivr-trend-short",
        "strategy_version": "1.1.0",
        "strategy_type": "rule-based",
        "position_intent": "short_premium",
        "fetch_failed": False,
        "error": None,
        "candidate_count": 0,
        "scanned_candidate_count": 2,
        "eligible_count": 0,
        "chain_snapshot": {
            "option_expiry": "07APR2026",
            "chain_rows": 40,
            "calendar_dte_ist": 11,
            "short_premium_delta_abs": "VIX=26.66 > 17 → CE [0.29,0.35] PE [-0.35,-0.29]",
            "short_delta_ce_lo": 0.29,
            "short_delta_ce_hi": 0.35,
            "short_delta_pe_lo": -0.35,
            "short_delta_pe_hi": -0.29,
            "short_leg_diagnostics": [
                {
                    "symbol": "NIFTY07APR22750PE",
                    "strike": 22750,
                    "option_type": "PE",
                    "distance_to_atm": -4,
                    "ltp": 385.35,
                    "delta": -0.4107,
                    "ivr": 92.0,
                    "leg_signal_eligible": False,
                },
                {
                    "symbol": "NIFTY07APR22100PE",
                    "strike": 22100,
                    "option_type": "PE",
                    "distance_to_atm": -2,
                    "ltp": 120.0,
                    "delta": -0.32,
                    "ivr": 40.0,
                    "leg_signal_eligible": False,
                },
            ],
        },
        "candidates": [],
    }
    text = format_evaluation_event_text(event)
    assert "Short delta gate:   VIX=26.66 > 17 → CE [0.29,0.35] PE [-0.35,-0.29]" in text
    assert "Strikes in band (1):" in text
    assert "NIFTY07APR22100PE" in text
    assert "NIFTY07APR22750PE" not in text
    assert "Short premium — per-leg diagnostics" not in text
    assert "compact" in text


def test_short_premium_verbose_log_when_env_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S004_EVALUATION_LOG_SHORT_FULL", "1")
    event: dict = {
        "ts_ist": "2026-03-25T10:00:00+05:30",
        "trigger_user_id": 1,
        "subscribed_user_ids": [1],
        "strategy_id": "strat-x",
        "strategy_version": "1.0.0",
        "strategy_type": "rule-based",
        "position_intent": "short_premium",
        "fetch_failed": False,
        "error": None,
        "candidate_count": 0,
        "scanned_candidate_count": 0,
        "eligible_count": 0,
        "score_threshold": 3,
        "score_max": 4,
        "auto_trade_score_threshold": 4.0,
        "include_ema_crossover_in_score": False,
        "strict_bullish_comparisons": False,
        "rsi_min": 45,
        "rsi_max": 85,
        "volume_min_ratio": 1.5,
        "adx_min_threshold": None,
        "failed_conditions_sample": [],
        "candidates": [],
        "candidates_truncated": False,
        "chain_snapshot": {
            "option_expiry": "07APR2026",
            "chain_rows": 1,
            "calendar_dte_ist": 5,
            "short_premium_delta_abs": "VIX=20 > 17 → CE [0.29,0.35] PE [-0.35,-0.29]",
            "short_delta_ce_lo": 0.29,
            "short_delta_ce_hi": 0.35,
            "short_delta_pe_lo": -0.35,
            "short_delta_pe_hi": -0.29,
            "short_leg_diagnostics": [
                {
                    "symbol": "NIFTY07APR22000PE",
                    "strike": 22000,
                    "option_type": "PE",
                    "distance_to_atm": 0,
                    "ltp": 100.0,
                    "delta": -0.31,
                    "ivr": 40.0,
                    "leg_score": 2,
                    "leg_signal_eligible": False,
                    "ema9": 101.0,
                    "ema21": 99.0,
                    "vwap": 100.5,
                    "rsi": 55.0,
                    "blockers": "test",
                },
            ],
        },
    }
    text = format_evaluation_event_text(event)
    assert "Short premium — per-leg diagnostics" in text
    assert "failed: test" in text
