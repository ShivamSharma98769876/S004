"""Unit tests for admin today's analysis helpers (no DB)."""

from app.services.admin_todays_analysis import build_analysis_csv_payload, build_heatmap_from_rows


def test_build_heatmap_from_rows_merges_cells():
    rows = [
        {"strategy_id": "s1", "hour_ist": 10, "wins": 2, "n": 4},
        {"strategy_id": "s1", "hour_ist": 10, "wins": 1, "n": 2},
        {"strategy_id": "s1", "hour_ist": 11, "wins": 3, "n": 3},
    ]
    hm = build_heatmap_from_rows(rows, "strategy_id", "hour_ist")
    assert "s1" in hm["strategies"]
    assert 10 in hm["buckets"] or "10" in hm["buckets"]
    by_sb = {(c["strategy_id"], c["bucket"]): c for c in hm["cells"]}
    c10 = by_sb.get(("s1", "10"))
    assert c10 is not None
    assert c10["wins"] == 3
    assert c10["total"] == 6
    assert c10["win_rate_pct"] == 50.0


def test_build_analysis_csv_contains_sections():
    payload = {
        "overview": {"reportDate": "2026-03-24", "market": {"nifty": {"spot": 1}, "pcr": 1.1}},
        "strategies_outcome": [{"display_name": "X", "recommendations": {"generated": 1}}],
        "decision_log": [{"username": "u1", "occurred_at": "t", "gate_reason": None, "cycle_summary": "OK", "evaluations": []}],
        "open_trades": [{"trade_ref": "tr1", "symbol": "SYM", "reason_code": "R", "score_at_entry": 5, "username": "u1"}],
    }
    csv_text = build_analysis_csv_payload(payload)
    assert "overview" in csv_text
    assert "decision_log" in csv_text
    assert "open_trade" in csv_text
    assert "tr1" in csv_text
