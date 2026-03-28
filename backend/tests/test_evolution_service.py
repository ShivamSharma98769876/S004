"""Unit tests for evolution helpers (no DB)."""

from app.services.evolution_service import (
    evaluation_analytics_from_daily,
    regime_and_fit_from_daily,
    shallow_merge_details,
    strategy_family_from_details,
    suggest_next_catalog_version,
)


def test_shallow_merge_nested() -> None:
    base = {"a": 1, "nested": {"x": 1, "y": 2}}
    patch = {"nested": {"y": 9, "z": 3}, "b": 2}
    out = shallow_merge_details(base, patch)
    assert out["a"] == 1
    assert out["b"] == 2
    assert out["nested"]["x"] == 1
    assert out["nested"]["y"] == 9
    assert out["nested"]["z"] == 3


def test_suggest_next_semver() -> None:
    assert suggest_next_catalog_version("1.0.0") == "1.0.1"
    assert suggest_next_catalog_version("2.3.9") == "2.3.10"


def test_suggest_next_fallback() -> None:
    assert suggest_next_catalog_version("v3") == "v4"
    assert suggest_next_catalog_version("custom") == "custom-evo"


def test_evaluation_analytics_equity_and_drawdown() -> None:
    daily = [
        {"trade_date_ist": f"2025-01-{i:02d}", "realized_pnl": 100.0 if i % 2 == 0 else -40.0, "closed_trades": 1}
        for i in range(1, 11)
    ]
    a = evaluation_analytics_from_daily(daily)
    assert a["equity_values"][-1] == sum(float(d["realized_pnl"]) for d in daily)
    assert a["max_drawdown_abs"] <= 0
    assert len(a["equity_dates"]) <= 90


def test_regime_insufficient_data() -> None:
    r = regime_and_fit_from_daily(
        [{"trade_date_ist": "2025-01-01", "realized_pnl": 1, "closed_trades": 1}],
        "trend",
    )
    assert r["regime_label"] == "INSUFFICIENT_DATA"


def test_strategy_family_from_details() -> None:
    assert strategy_family_from_details({"strategy_type": "trendpulse-z"}) == "trend"
    assert strategy_family_from_details({}) == "unknown"
