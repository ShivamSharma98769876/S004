"""Strategy EOD report suggestion helpers."""

from app.services.strategy_eod_report_service import _build_suggestions


def test_build_suggestions_empty_below_five_trades():
    assert _build_suggestions(n=4, exit_counts={"SL_HIT": 4}, avg_entry_vix=None) == []


def test_build_suggestions_info_when_no_rule_fires():
    """Many trades but exit mix does not hit SL/target/manual share thresholds → info row."""
    out = _build_suggestions(
        n=10,
        exit_counts={"UNKNOWN": 6, "BREAKEVEN": 4},
        avg_entry_vix=None,
    )
    assert len(out) == 1
    assert out[0]["kind"] == "info"
    assert out[0]["hint_key"] == "exit_mix_thresholds"
    assert "10 closed trades" in out[0]["message"]
    assert "UNKNOWN" in out[0]["message"]


def test_build_suggestions_sl_rule_with_mixed_codes():
    out = _build_suggestions(
        n=10,
        exit_counts={"SL_HIT": 7, "TARGET_HIT": 3},
        avg_entry_vix=None,
    )
    assert any(s.get("hint_key") == "stop_loss_price" for s in out)


def test_manual_bucket_includes_user_close_aliases():
    out = _build_suggestions(
        n=10,
        exit_counts={"MANUAL_EXECUTE": 4, "TARGET_HIT": 6},
        avg_entry_vix=None,
    )
    assert any(s.get("hint_key") == "manual_close" for s in out)
