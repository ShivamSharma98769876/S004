"""Sideways regime uses shared ADX/TR/VWAP helpers + sentiment-shaped inputs."""

from __future__ import annotations

from app.services.sentiment_engine import compute_sideways_regime_snapshot


def _flat_candles(n: int, base: float = 24000.0) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        c = base + (i % 3) * 2.0 - 2.0
        out.append(
            {
                "open": c - 1,
                "high": c + 3,
                "low": c - 3,
                "close": c,
                "volume": 0.0,
                "time": f"2026-04-01T09:{15 + i * 5:02d}:00",
            }
        )
    return out


def test_sideways_regime_disabled_without_enough_bars():
    out = compute_sideways_regime_snapshot(
        candles=_flat_candles(10),
        spot=24000.0,
        sentiment={"inputs": {"ceOi": 1, "peOi": 1}, "optionsIntel": {"oiDominant": "EVEN"}},
        vix=12.0,
        vix_prev=12.1,
        ce_oi_prev=0.0,
        pe_oi_prev=0.0,
    )
    assert out["enabled"] is False
    assert out.get("timeframe") == "30m"


def test_sideways_regime_returns_checks_when_warmed():
    candles = _flat_candles(40)
    sent = {
        "inputs": {"ceOi": 1_000_000, "peOi": 1_000_000},
        "optionsIntel": {"oiDominant": "EVEN"},
    }
    out = compute_sideways_regime_snapshot(
        candles=candles,
        spot=float(candles[-1]["close"]),
        sentiment=sent,
        vix=12.0,
        vix_prev=12.05,
        ce_oi_prev=900_000.0,
        pe_oi_prev=900_000.0,
    )
    assert out["enabled"] is True
    assert out["maxScore"] == 6
    assert len(out["checks"]) == 6
    assert "regimeLabel" in out
    assert out.get("timeframe") == "30m"
