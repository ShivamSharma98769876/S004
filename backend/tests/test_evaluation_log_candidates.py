"""Evaluation log writes human-readable .log files with slim candidate lines."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import trades_service as ts


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
    assert "NIFTY26MAR22000CE" in text
    assert "Candidates truncated: False" in text
    assert "Time (IST):" in text
    assert "Recommendation evaluation snapshot" in text
    assert "Auto-trade score:   4.0" in text
    assert "E9=101.00" in text and "RSI=55.00" in text
