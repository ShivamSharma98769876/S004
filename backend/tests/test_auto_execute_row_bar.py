"""row_meets_auto_execute_score_bar aligns GET /recommendations?eligible_only with auto-execute."""

from __future__ import annotations

from app.services.trades_service import row_meets_auto_execute_score_bar


def test_infer_signal_eligible_when_missing():
    r = {"score": 4.0, "confidence_score": 85.0}
    assert row_meets_auto_execute_score_bar(
        r, min_score=4.0, score_threshold=3.0, min_confidence=80.0
    )


def test_strict_false_signal_eligible_blocks():
    r = {"score": 4.0, "confidence_score": 85.0, "signal_eligible": False}
    assert not row_meets_auto_execute_score_bar(
        r, min_score=4.0, score_threshold=3.0, min_confidence=80.0
    )


def test_below_auto_threshold():
    r = {"score": 3.0, "confidence_score": 85.0, "signal_eligible": True}
    assert not row_meets_auto_execute_score_bar(
        r, min_score=4.0, score_threshold=3.0, min_confidence=80.0
    )


def test_confidence_gate():
    r = {"score": 4.0, "confidence_score": 79.0, "signal_eligible": True}
    assert not row_meets_auto_execute_score_bar(
        r, min_score=4.0, score_threshold=3.0, min_confidence=80.0
    )
