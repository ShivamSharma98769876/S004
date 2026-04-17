"""SuperTrendTrail helpers and exit flip rules."""

import app.strategies.supertrend_trail as stt
from app.strategies.supertrend_trail import (
    compute_hybrid_sl_short_sell,
    evaluate_supertrend_trail_signal,
    map_settings_timeframe_to_kite_interval,
    option_sl_trace_phase,
    resolve_supertrend_trail_config,
    should_exit_on_spot_supertrend_flip,
)


def test_map_timeframe():
    assert map_settings_timeframe_to_kite_interval("5-min") == "5minute"
    assert map_settings_timeframe_to_kite_interval("3-min") == "3minute"
    assert map_settings_timeframe_to_kite_interval("15minute") == "15minute"


def test_resolve_config_defaults():
    c = resolve_supertrend_trail_config({})
    assert c["emaFast"] == 10
    assert c["emaSlow"] == 20
    assert c["atrPeriod"] == 10
    assert c["atrMultiplier"] == 3.0
    assert c["minDteCalendarDays"] == 2


def test_option_sl_trace_phase():
    assert option_sl_trace_phase(100.0, 100.0, eps_pct=0.5) == "st"
    assert option_sl_trace_phase(100.0, 99.5, eps_pct=0.5) == "st"
    assert option_sl_trace_phase(100.0, 99.0, eps_pct=0.5) == "vwap"


def test_hybrid_sl_vwap_phase_tightens():
    nsl, mode = compute_hybrid_sl_short_sell(
        entry_premium=100.0,
        ltp=50.0,
        session_vwap=80.0,
        spot_snap=None,
        current_sl=120.0,
        vwap_step_threshold_pct=0.05,
        entry_vs_vwap_eps_pct=0.5,
    )
    assert mode == "VWAP"
    assert nsl is not None
    assert nsl < 120.0
    assert nsl > 50.0


def test_hybrid_sl_st_phase_uses_snap():
    snap = {
        "supertrend_upper": 22500.0,
        "supertrend_lower": 22300.0,
        "close": 22400.0,
    }
    nsl, mode = compute_hybrid_sl_short_sell(
        entry_premium=120.0,
        ltp=100.0,
        session_vwap=125.0,
        spot_snap=snap,
        current_sl=140.0,
        vwap_step_threshold_pct=0.05,
        entry_vs_vwap_eps_pct=0.5,
    )
    assert mode == "ST"
    assert nsl is not None
    assert nsl >= 100.0


def test_exit_flip():
    assert should_exit_on_spot_supertrend_flip(option_type="PE", st_direction=-1) is True
    assert should_exit_on_spot_supertrend_flip(option_type="PE", st_direction=1) is False
    assert should_exit_on_spot_supertrend_flip(option_type="CE", st_direction=1) is True
    assert should_exit_on_spot_supertrend_flip(option_type="CE", st_direction=-1) is False


def _candles_from_closes(closes: list[float]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for c in closes:
        out.append({"open": c, "high": c + 1.0, "low": c - 1.0, "close": c, "volume": 1000.0})
    return out


def _patch_signal_basics(monkeypatch) -> None:
    def _fake_ema_series(closes: list[float], period: int) -> list[float]:
        base = 105.0 if int(period) == 10 else 100.0
        return [base] * len(closes)

    def _fake_supertrend_direction(highs, lows, closes, candles, atr_period, multiplier):
        n = len(closes)
        return ([1] * n, [0.0] * n, [0.0] * n)

    monkeypatch.setattr(stt, "_ema_series", _fake_ema_series)
    monkeypatch.setattr(stt, "_supertrend_direction", _fake_supertrend_direction)


def _patch_signal_basics_bear(monkeypatch) -> None:
    def _fake_ema_series(closes: list[float], period: int) -> list[float]:
        base = 95.0 if int(period) == 10 else 100.0
        return [base] * len(closes)

    def _fake_supertrend_direction(highs, lows, closes, candles, atr_period, multiplier):
        n = len(closes)
        return ([-1] * n, [0.0] * n, [0.0] * n)

    monkeypatch.setattr(stt, "_ema_series", _fake_ema_series)
    monkeypatch.setattr(stt, "_supertrend_direction", _fake_supertrend_direction)


def test_signal_no_ema20_breach_has_metrics(monkeypatch):
    _patch_signal_basics(monkeypatch)
    closes = [110.0] * 39 + [110.0]
    ev = evaluate_supertrend_trail_signal(_candles_from_closes(closes), resolve_supertrend_trail_config({}))
    assert ev["ok"] is False
    assert ev["reason"] == "close_not_in_ema_zone"
    assert isinstance(ev.get("metrics"), dict)
    assert ev["metrics"]["close"] == 110.0
    assert ev["metrics"]["close_prev"] == 110.0
    assert ev["metrics"]["ema10"] == 105.0
    assert ev["metrics"]["ema20"] == 100.0


def test_signal_bullish_triggers_on_close_below_slow_ema(monkeypatch):
    _patch_signal_basics(monkeypatch)
    closes = [110.0] * 39 + [99.0]
    ev = evaluate_supertrend_trail_signal(_candles_from_closes(closes), resolve_supertrend_trail_config({}))
    assert ev["ok"] is True
    assert ev["reason"] == "bull_pullback_close_below_slow_ema_sell_pe"


def test_signal_bearish_triggers_on_close_above_slow_ema(monkeypatch):
    _patch_signal_basics_bear(monkeypatch)
    closes = [90.0] * 39 + [101.0]
    ev = evaluate_supertrend_trail_signal(_candles_from_closes(closes), resolve_supertrend_trail_config({}))
    assert ev["ok"] is True
    assert ev["reason"] == "bear_pullback_close_above_slow_ema_sell_ce"


def test_signal_bullish_does_not_require_prev_close_above_ema10(monkeypatch):
    _patch_signal_basics(monkeypatch)
    # With patched EMA10=105, EMA20=100: both closes must be strictly below slow EMA for a 2-bar run.
    closes = [110.0] * 38 + [99.0, 98.0]
    cfg = resolve_supertrend_trail_config({"maxConsecutiveClosesInZone": 2})
    ev = evaluate_supertrend_trail_signal(_candles_from_closes(closes), cfg)
    assert ev["ok"] is True
    assert ev["reason"] == "bull_pullback_close_below_slow_ema_sell_pe"


def test_signal_allows_second_consecutive_close_below_slow(monkeypatch):
    _patch_signal_basics(monkeypatch)
    closes = [110.0] * 38 + [98.0, 97.0]
    ev = evaluate_supertrend_trail_signal(_candles_from_closes(closes), resolve_supertrend_trail_config({}))
    assert ev["ok"] is True
    assert ev["reason"] == "bull_pullback_close_below_slow_ema_sell_pe"
    assert ev["metrics"]["inside_run"] == 2
