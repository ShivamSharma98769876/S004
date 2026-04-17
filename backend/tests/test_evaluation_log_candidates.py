"""Evaluation log writes human-readable .log files for recommendation snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import trades_service as ts
from app.services.evaluation_log import execution_intent_side_note, format_evaluation_event_text


def test_execution_intent_side_note_only_when_intents_diverge() -> None:
    assert execution_intent_side_note({"position_intent": "long_premium"}) is None
    assert execution_intent_side_note({"position_intent": "long_premium", "execution_action_intent": "long_premium"}) is None
    n = execution_intent_side_note(
        {"position_intent": "long_premium", "execution_action_intent": "short_premium"}
    )
    assert n is not None
    assert "short_premium" in n and "long_premium" in n
    assert "SELL" in n and "long-premium" in n
    n2 = execution_intent_side_note(
        {"position_intent": "short_premium", "execution_action_intent": "long_premium"}
    )
    assert n2 is not None and "BUY" in n2 and "short-premium" in n2


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


def test_emit_evaluation_snapshot_includes_execution_side_note_when_intents_diverge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("S004_EVALUATION_LOG_DIR", str(tmp_path))
    row = {
        "symbol": "NIFTY26MAR22000CE",
        "instrument": "NIFTY",
        "expiry": "26MAR2026",
        "side": "SELL",
        "option_type": "CE",
        "distance_to_atm": 0,
        "signal_eligible": False,
        "score": 0,
        "confidence_score": 0.0,
        "failed_conditions": "PASS",
        "delta": 0.32,
        "gamma": 0.0,
        "ivr": 40.0,
        "oi": 10000,
        "volume_spike_ratio": 1.0,
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
            "position_intent": "long_premium",
            "execution_action_intent": "short_premium",
            "include_ema_crossover_in_score": False,
        },
        fetch_failed=False,
        error=None,
        generated_rows=[row],
        scanned_candidates=[row],
        chain_snapshot={"option_expiry": "07APR2026", "chain_rows": 11, "calendar_dte_ist": 11},
    )
    text = next(tmp_path.iterdir()).glob("*.log").__next__().read_text(encoding="utf-8")
    assert "Note: execution intent (short_premium) differs from position intent (long_premium)" in text
    assert "recommendation side=SELL" in text


def test_short_premium_compact_log_includes_execution_side_note_when_present() -> None:
    event: dict = {
        "ts_ist": "2026-03-25T10:00:00+05:30",
        "strategy_id": "strat-nifty-ivr-trend-short",
        "strategy_version": "1.1.0",
        "strategy_type": "rule-based",
        "position_intent": "short_premium",
        "execution_side_note": (
            "Note: execution intent (long_premium) differs from position intent (short_premium): "
            "recommendation side=BUY; chain/scoring uses short-premium rules."
        ),
        "fetch_failed": False,
        "error": None,
        "candidate_count": 0,
        "scanned_candidate_count": 0,
        "eligible_count": 0,
        "chain_snapshot": {
            "option_expiry": "07APR2026",
            "chain_rows": 40,
            "calendar_dte_ist": 11,
            "short_premium_delta_abs": "VIX=20 → CE [0.29,0.35] PE [-0.35,-0.29]",
            "short_delta_ce_lo": 0.29,
            "short_delta_ce_hi": 0.35,
            "short_delta_pe_lo": -0.35,
            "short_delta_pe_hi": -0.29,
        },
        "candidates": [],
    }
    text = format_evaluation_event_text(event)
    assert "Note: execution intent (long_premium)" in text
    assert "recommendation side=BUY" in text


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


def test_long_premium_log_includes_candidate_detail_when_env_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("S004_EVALUATION_LOG_LONG_CANDIDATES", "1")
    event: dict = {
        "ts_ist": "2026-03-25T10:00:00+05:30",
        "strategy_id": "strat-trendsnap-momentum",
        "strategy_version": "1.0.0",
        "strategy_type": "rule-based",
        "position_intent": "long_premium",
        "fetch_failed": False,
        "error": None,
        "candidate_count": 1,
        "scanned_candidate_count": 2,
        "eligible_count": 0,
        "score_threshold": 3,
        "score_max": 4,
        "auto_trade_score_threshold": 4.0,
        "include_ema_crossover_in_score": False,
        "strict_bullish_comparisons": True,
        "rsi_min": 50,
        "rsi_max": 75,
        "volume_min_ratio": 1.02,
        "adx_min_threshold": None,
        "failed_conditions_sample": ["score<3"],
        "candidates": [
            {
                "symbol": "NIFTY07APR22000CE",
                "option_type": "CE",
                "strike": 22000,
                "side": "BUY",
                "distance_to_atm": 1,
                "score": 2,
                "confidence_score": 55.0,
                "signal_eligible": False,
                "delta": 0.45,
                "ivr": 22.0,
                "oi": 5000,
                "volume_spike_ratio": 1.1,
                "entry_price": 150.0,
                "ema9": 22010.0,
                "ema21": 21990.0,
                "vwap": 22005.0,
                "rsi": 60.0,
                "failed_conditions": "score 2 < 3",
            },
        ],
        "candidates_truncated": False,
        "chain_snapshot": {
            "option_expiry": "07APR2026",
            "chain_rows": 20,
            "calendar_dte_ist": 8,
        },
    }
    text = format_evaluation_event_text(event)
    assert "Scanned candidates (detail, long premium):" in text
    assert "NIFTY07APR22000CE" in text
    assert "strike=22000" in text
    assert "E9=22010.00" in text
    assert "failed: score 2 < 3" in text


def test_stochastic_bnf_empty_candidates_uses_short_title_and_spot_led_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S004_EVALUATION_LOG_LONG_CANDIDATES", "1")
    event: dict = {
        "ts_ist": "2026-04-13T10:27:58+05:30",
        "strategy_id": "strat-stochastic-bnf",
        "strategy_version": "1.0.0",
        "strategy_type": "stochastic-bnf",
        "position_intent": "short_premium",
        "fetch_failed": False,
        "error": None,
        "candidate_count": 0,
        "scanned_candidate_count": 0,
        "eligible_count": 0,
        "score_threshold": 3,
        "score_max": 5,
        "auto_trade_score_threshold": 3.5,
        "include_ema_crossover_in_score": False,
        "strict_bullish_comparisons": True,
        "rsi_min": 50,
        "rsi_max": 75,
        "volume_min_ratio": 1.02,
        "adx_min_threshold": None,
        "failed_conditions_sample": [],
        "candidates": [],
        "candidates_truncated": False,
        "chain_snapshot": {},
    }
    text = format_evaluation_event_text(event)
    assert "Scanned candidates (detail, short premium):" in text
    assert "spot-led path" in text
    assert "Chain snapshot:     — (not attached for this strategy type" in text
    assert "chain fetch failed" not in text.lower()


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
