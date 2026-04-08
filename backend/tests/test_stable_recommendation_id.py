from __future__ import annotations

from app.services.trades_service import _stable_recommendation_id


def test_stable_recommendation_id_deterministic() -> None:
    a = _stable_recommendation_id(1, "strat-a", "1.0.0", "NIFTY24APR24000CE", "SELL")
    b = _stable_recommendation_id(1, "strat-a", "1.0.0", "NIFTY24APR24000CE", "SELL")
    assert a == b
    assert a.startswith("rec-")
    assert len(a) == 4 + 16


def test_stable_recommendation_id_same_symbol_ignores_rank_semantics() -> None:
    """Rank must not change the id (re-sort would otherwise orphan dashboard/clicks)."""
    a = _stable_recommendation_id(1, "s", "1.0.0", "SYM", "SELL")
    assert _stable_recommendation_id(1, "s", "1.0.0", "SYM", "SELL") == a


def test_stable_recommendation_id_differs_by_user_or_side() -> None:
    base = _stable_recommendation_id(1, "s", "1.0.0", "SYM", "SELL")
    assert _stable_recommendation_id(2, "s", "1.0.0", "SYM", "SELL") != base
    assert _stable_recommendation_id(1, "s", "1.0.0", "SYM", "BUY") != base
