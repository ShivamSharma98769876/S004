"""Short-premium recommendation sort: prefer score/confidence/liquidity over min-gamma alone."""

from __future__ import annotations

from app.services.trades_service import _short_premium_eligible_sort_key


def _rec(
    *,
    score: int,
    confidence: float,
    oi: int,
    vol_spike: float,
    oi_chg: float,
    delta_dist: float,
    datm: int,
    gamma: float,
    short_premium_rsi_drop: float = 0.0,
) -> dict:
    return {
        "score": score,
        "confidence_score": confidence,
        "oi": oi,
        "volume_spike_ratio": vol_spike,
        "oi_chg_pct": oi_chg,
        "delta_distance": delta_dist,
        "distance_to_atm": datm,
        "gamma": gamma,
        "short_premium_rsi_drop": short_premium_rsi_drop,
    }


def test_short_premium_rank_prefers_higher_score_over_lower_gamma() -> None:
    weak_gamma = _rec(
        score=3,
        confidence=70.0,
        oi=5000,
        vol_spike=1.0,
        oi_chg=0.5,
        delta_dist=0.02,
        datm=1,
        gamma=0.001,
    )
    strong = _rec(
        score=5,
        confidence=85.0,
        oi=8000,
        vol_spike=1.2,
        oi_chg=1.0,
        delta_dist=0.02,
        datm=1,
        gamma=0.05,
    )
    rows = [weak_gamma, strong]
    rows.sort(key=_short_premium_eligible_sort_key)
    assert rows[0]["score"] == 5


def test_short_premium_tie_breaks_on_gamma_last() -> None:
    a = _rec(
        score=4,
        confidence=80.0,
        oi=10000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.01,
        datm=1,
        gamma=0.02,
    )
    b = _rec(
        score=4,
        confidence=80.0,
        oi=10000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.01,
        datm=1,
        gamma=0.01,
    )
    rows = [a, b]
    rows.sort(key=_short_premium_eligible_sort_key)
    assert rows[0]["gamma"] == 0.01


def test_short_premium_rsi_decreasing_rank_orders_by_rsi_drop_second() -> None:
    """After score + confidence, prefer stronger leg RSI decay (rsiPrev − rsi) when flag is on."""
    weak_momo = _rec(
        score=4,
        confidence=75.0,
        oi=5000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.02,
        datm=1,
        gamma=0.02,
        short_premium_rsi_drop=0.5,
    )
    strong_momo = _rec(
        score=4,
        confidence=75.0,
        oi=5000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.02,
        datm=1,
        gamma=0.02,
        short_premium_rsi_drop=4.0,
    )
    rows = [weak_momo, strong_momo]
    rows.sort(
        key=lambda r: _short_premium_eligible_sort_key(r, rsi_decreasing_rank=True),
    )
    assert rows[0]["short_premium_rsi_drop"] == 4.0


def test_short_premium_rsi_drop_ignored_when_flag_off() -> None:
    hi = _rec(
        score=4,
        confidence=75.0,
        oi=5000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.02,
        datm=1,
        gamma=0.02,
        short_premium_rsi_drop=10.0,
    )
    lo = _rec(
        score=4,
        confidence=75.0,
        oi=5000,
        vol_spike=1.0,
        oi_chg=0.0,
        delta_dist=0.02,
        datm=1,
        gamma=0.01,
        short_premium_rsi_drop=0.1,
    )
    rows = [hi, lo]
    rows.sort(key=_short_premium_eligible_sort_key)
    assert rows[0]["gamma"] == 0.01
