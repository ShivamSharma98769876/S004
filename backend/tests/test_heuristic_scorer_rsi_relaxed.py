"""RSI heuristic with relaxed upper band (rsi_max >= 99.5) uses floor-only scoring."""

from app.services.heuristic_scorer import _score_rsi


def test_score_rsi_relaxed_max_high_rsi_passes():
    s, reason = _score_rsi(85.0, rsi_min=50, rsi_max=100)
    assert s == 5.0
    assert reason and "85" in reason


def test_score_rsi_relaxed_max_below_min_penalized():
    s, _ = _score_rsi(40.0, rsi_min=50, rsi_max=100)
    assert s < 5.0


def test_score_rsi_legacy_band_unchanged():
    s, _ = _score_rsi(70.0, rsi_min=45, rsi_max=75)
    assert s == 5.0
    s2, _ = _score_rsi(80.0, rsi_min=45, rsi_max=75)
    assert s2 < 5.0
