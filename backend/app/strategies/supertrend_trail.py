"""SuperTrendTrail: NIFTY spot SuperTrend + EMA pullback vs slow EMA; short ATM premium (weekly, min DTE)."""

from __future__ import annotations

from typing import Any

from app.services.option_chain_zerodha import (
    _parse_candle_time_ist,
    _true_range_series,
    _wilder_smooth_list,
    running_typical_price_average_series,
)


def map_settings_timeframe_to_kite_interval(raw: str | None) -> str:
    """Map Settings UI values (e.g. ``3-min``) to Kite ``historical_data`` interval strings."""
    s = str(raw or "").strip().lower().replace(" ", "")
    if not s:
        return "5minute"
    if "minute" in s:
        return s if s.endswith("minute") else s.replace("m", "") + "minute"
    # e.g. 3-min, 5-min, 15-min, 1-min
    if s.endswith("-min"):
        num = s.replace("-min", "").replace("min", "")
        return f"{num}minute" if num.isdigit() else "5minute"
    if s.endswith("m") and s[:-1].isdigit():
        return f"{s[:-1]}minute"
    if s.isdigit():
        return f"{s}minute"
    return "5minute"


def resolve_supertrend_trail_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = raw if isinstance(raw, dict) else {}
    return {
        "emaFast": int(r.get("emaFast", 10) or 10),
        "emaSlow": int(r.get("emaSlow", 20) or 20),
        "atrPeriod": int(r.get("atrPeriod", 10) or 10),
        "atrMultiplier": float(r.get("atrMultiplier", 3.0) or 3.0),
        "candleDaysBack": int(r.get("candleDaysBack", 5) or 5),
        "minDteCalendarDays": int(r.get("minDteCalendarDays", 2) or 2),
        "niftyWeeklyExpiryWeekday": r.get("niftyWeeklyExpiryWeekday", "TUE"),
        "maxConsecutiveClosesInZone": max(1, int(r.get("maxConsecutiveClosesInZone", 1) or 1)),
        "vwapStepThresholdPct": float(r.get("vwapStepThresholdPct", 0.05) or 0.05),
        # Slippage when comparing session VWAP vs entry premium ("same or higher").
        "entryVsVwapEpsPct": float(r.get("entryVsVwapEpsPct", 0.02) or 0.02),
    }


def _ema_series(closes: list[float], period: int) -> list[float]:
    p = max(1, int(period))
    if not closes:
        return []
    k = 2.0 / (p + 1)
    out: list[float] = []
    ema_v = closes[0]
    out.append(ema_v)
    for c in closes[1:]:
        ema_v = c * k + ema_v * (1.0 - k)
        out.append(ema_v)
    return out


def _close_below_slow_ema(close: float, ema_s: float) -> bool:
    return close < ema_s


def _close_above_slow_ema(close: float, ema_s: float) -> bool:
    return close > ema_s


def _count_consecutive_pullback_vs_slow(
    closes: list[float],
    ema_f: list[float],
    ema_s: list[float],
    end_idx: int,
    *,
    bull: bool,
) -> int:
    """Count contiguous bars matching regime: bull = close below slow & fast>slow; bear = close above slow & fast<slow."""
    run = 0
    i = end_idx
    while i >= 0:
        c, ef, es = closes[i], ema_f[i], ema_s[i]
        if bull:
            if not _close_below_slow_ema(c, es) or not (ef > es):
                break
        else:
            if not _close_above_slow_ema(c, es) or not (ef < es):
                break
        run += 1
        i -= 1
    return run


def _supertrend_direction(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    candles: list[dict[str, Any]],
    atr_period: int,
    multiplier: float,
) -> tuple[list[int], list[float], list[float]]:
    """Returns (direction per bar, final_upper, final_lower). Direction +1 = buy/bull, -1 = sell/bear."""
    n = len(closes)
    if n < max(3, atr_period + 2):
        return [0] * n, [0.0] * n, [0.0] * n
    tr = _true_range_series(candles)
    atr_s = _wilder_smooth_list(tr, atr_period)
    hl2 = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
    upper = [hl2[i] + multiplier * float(atr_s[i]) for i in range(n)]
    lower = [hl2[i] - multiplier * float(atr_s[i]) for i in range(n)]
    fu = [upper[0]]
    fl = [lower[0]]
    for i in range(1, n):
        fu.append(upper[i] if (upper[i] < fu[i - 1] or closes[i - 1] > fu[i - 1]) else fu[i - 1])
        fl.append(lower[i] if (lower[i] > fl[i - 1] or closes[i - 1] < fl[i - 1]) else fl[i - 1])
    direction = [0] * n
    direction[0] = 1 if closes[0] > fl[0] else -1
    for i in range(1, n):
        if closes[i] > fu[i - 1]:
            direction[i] = 1
        elif closes[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    return direction, fu, fl


def evaluate_supertrend_trail_signal(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    Evaluate latest **closed** bar for pullback entry.

    Bullish regime (short PE): SuperTrend bullish, fast EMA above slow EMA, latest close **below slow EMA**.
    Bearish regime (short CE): SuperTrend bearish, fast EMA below slow EMA, latest close **above slow EMA**.
    """
    ema_f_p = int(cfg.get("emaFast", 10))
    ema_s_p = int(cfg.get("emaSlow", 20))
    atr_p = int(cfg.get("atrPeriod", 10))
    mult = float(cfg.get("atrMultiplier", 3.0))
    need = max(ema_s_p + 5, atr_p + 15, 35)
    if not candles or len(candles) < need:
        return {"ok": False, "reason": f"need>={need}_candles", "direction": None}

    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]

    ema_f = _ema_series(closes, ema_f_p)
    ema_s = _ema_series(closes, ema_s_p)
    direction, fu, fl = _supertrend_direction(highs, lows, closes, candles, atr_p, mult)

    n = len(closes)
    i = n - 1

    prev_i = i - 1
    ef_i, es_i = ema_f[i], ema_s[i]
    ef_prev, es_prev = ema_f[prev_i], ema_s[prev_i]
    st_i = direction[i]
    st_prev = direction[prev_i]

    low_i = lows[i]
    high_i = highs[i]
    close_i = closes[i]
    close_prev = closes[prev_i]

    # Uptrend: sell PUT — trend bullish, fast > slow, pullback close below slow EMA.
    if st_prev == 1 and st_i == 1:
        if not (ef_i > es_i):
            return {
                "ok": False,
                "reason": "trend_or_ema_mismatch",
                "direction": None,
                "metrics": {
                    "st": st_i,
                    "st_prev": st_prev,
                    "ema10": ef_i,
                    "ema20": es_i,
                    "ema10_prev": ef_prev,
                    "ema20_prev": es_prev,
                    "close": close_i,
                    "close_prev": close_prev,
                    "low": low_i,
                    "high": high_i,
                },
            }
        if _close_below_slow_ema(close_i, es_i):
            inside_run = _count_consecutive_pullback_vs_slow(closes, ema_f, ema_s, i, bull=True)
            return {
                "ok": True,
                "reason": "bull_pullback_close_below_slow_ema_sell_pe",
                "direction": "bull",
                "metrics": {
                    "st": st_i,
                    "st_prev": st_prev,
                    "ema10": ef_i,
                    "ema20": es_i,
                    "close": close_i,
                    "close_prev": close_prev,
                    "low": low_i,
                    "high": high_i,
                    "supertrend_upper": fu[i],
                    "supertrend_lower": fl[i],
                    "inside_run": inside_run,
                },
            }
        return {
            "ok": False,
            "reason": "close_not_in_ema_zone",
            "direction": None,
            "metrics": {
                "st": st_i,
                "st_prev": st_prev,
                "ema10": ef_i,
                "ema20": es_i,
                "ema10_prev": ef_prev,
                "ema20_prev": es_prev,
                "close": close_i,
                "close_prev": close_prev,
                "low": low_i,
                "high": high_i,
            },
        }

    # Downtrend: sell CALL — trend bearish, fast < slow, pullback close above slow EMA.
    if st_prev == -1 and st_i == -1:
        if not (ef_i < es_i):
            return {
                "ok": False,
                "reason": "trend_or_ema_mismatch",
                "direction": None,
                "metrics": {
                    "st": st_i,
                    "st_prev": st_prev,
                    "ema10": ef_i,
                    "ema20": es_i,
                    "ema10_prev": ef_prev,
                    "ema20_prev": es_prev,
                    "close": close_i,
                    "close_prev": close_prev,
                    "low": low_i,
                    "high": high_i,
                },
            }
        if _close_above_slow_ema(close_i, es_i):
            inside_run = _count_consecutive_pullback_vs_slow(closes, ema_f, ema_s, i, bull=False)
            return {
                "ok": True,
                "reason": "bear_pullback_close_above_slow_ema_sell_ce",
                "direction": "bear",
                "metrics": {
                    "st": st_i,
                    "st_prev": st_prev,
                    "ema10": ef_i,
                    "ema20": es_i,
                    "close": close_i,
                    "close_prev": close_prev,
                    "low": low_i,
                    "high": high_i,
                    "supertrend_upper": fu[i],
                    "supertrend_lower": fl[i],
                    "inside_run": inside_run,
                },
            }
        return {
            "ok": False,
            "reason": "close_not_in_ema_zone",
            "direction": None,
            "metrics": {
                "st": st_i,
                "st_prev": st_prev,
                "ema10": ef_i,
                "ema20": es_i,
                "ema10_prev": ef_prev,
                "ema20_prev": es_prev,
                "close": close_i,
                "close_prev": close_prev,
                "low": low_i,
                "high": high_i,
            },
        }

    return {
        "ok": False,
        "reason": "trend_or_ema_mismatch",
        "direction": None,
        "metrics": {
            "st": st_i,
            "st_prev": st_prev,
            "ema10": ef_i,
            "ema20": es_i,
            "ema10_prev": ef_prev,
            "ema20_prev": es_prev,
            "close": close_i,
            "close_prev": close_prev,
            "low": low_i,
            "high": high_i,
        },
    }


def should_exit_on_spot_supertrend_flip(*, option_type: str, st_direction: int) -> bool:
    """Short PE (bullish) exits when spot SuperTrend flips bearish; short CE exits when flips bullish."""
    ot = str(option_type or "").upper().strip()
    if ot == "PE":
        return st_direction == -1
    if ot == "CE":
        return st_direction == 1
    return False


def snapshot_supertrend_state(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any] | None:
    """Latest-bar SuperTrend direction on the same series as entries (for exit / monitor)."""
    ema_f_p = int(cfg.get("emaFast", 10))
    ema_s_p = int(cfg.get("emaSlow", 20))
    atr_p = int(cfg.get("atrPeriod", 10))
    mult = float(cfg.get("atrMultiplier", 3.0))
    need = max(ema_s_p + 5, atr_p + 15, 35)
    if not candles or len(candles) < need:
        return None
    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]
    direction, fu, fl = _supertrend_direction(highs, lows, closes, candles, atr_p, mult)
    i = len(closes) - 1
    ema_f = _ema_series(closes, ema_f_p)
    ema_s = _ema_series(closes, ema_s_p)
    return {
        "st_direction": direction[i],
        "ema10": ema_f[i],
        "ema20": ema_s[i],
        "close": closes[i],
        "supertrend_upper": fu[i],
        "supertrend_lower": fl[i],
    }


def option_sl_trace_phase(
    entry_premium: float,
    session_vwap: float | None,
    *,
    eps_pct: float = 0.02,
) -> str:
    """Return ``st`` while session VWAP is at/above entry premium; ``vwap`` once VWAP drops below."""
    if session_vwap is None or entry_premium <= 0:
        return "st"
    floor = entry_premium * (1.0 - max(0.0, eps_pct) / 100.0)
    if session_vwap >= floor - 1e-9:
        return "st"
    return "vwap"


def compute_hybrid_sl_short_sell(
    *,
    entry_premium: float,
    ltp: float,
    session_vwap: float | None,
    spot_snap: dict[str, Any] | None,
    current_sl: float,
    vwap_step_threshold_pct: float,
    entry_vs_vwap_eps_pct: float = 0.02,
) -> tuple[float | None, str]:
    """
    Short option SELL: numeric stop is **above** LTP.

    - While session VWAP >= entry premium (within eps): peg SL to **spot SuperTrend band** (mapped to premium).
    - Once VWAP < entry: **trail** SL down with **session VWAP** (only when step exceeds threshold).
    """
    if entry_premium <= 0 or ltp <= 0 or current_sl <= 0:
        return (None, "—")

    phase = option_sl_trace_phase(entry_premium, session_vwap, eps_pct=entry_vs_vwap_eps_pct)

    if phase == "st":
        if not spot_snap:
            return (None, "ST")
        fu = float(spot_snap.get("supertrend_upper") or 0)
        fl = float(spot_snap.get("supertrend_lower") or 0)
        sc = float(spot_snap.get("close") or 0)
        if sc <= 0 or fu <= 0 or fl <= 0:
            return (None, "ST")
        band = max(fu - fl, 1.0)
        # Map spot band width to option premium space (heuristic; ST phase follows structure, not VWAP).
        addon = (band / sc) * entry_premium * 0.4
        proposed = max(ltp * 1.02, ltp + addon, entry_premium * 1.02)
        proposed = min(proposed, entry_premium * 2.5)
        proposed = max(proposed, ltp * 1.005)
        if abs(proposed - current_sl) < 0.005:
            return (None, "ST")
        return (round(proposed, 2), "ST")

    # VWAP trail phase: tighten stop downward as session VWAP drops below entry (short SELL).
    if session_vwap is None or session_vwap <= 0:
        return (None, "VWAP")
    thr = max(0.0, vwap_step_threshold_pct) / 100.0
    cushion = max(0.01, ltp * 0.01)
    candidate_sl = max(ltp + cushion, session_vwap * (1.0 + thr))
    candidate_sl = min(candidate_sl, current_sl)
    candidate_sl = max(candidate_sl, ltp * 1.005)
    if candidate_sl >= current_sl - 0.005:
        return (None, "VWAP")
    min_step = max(0.02, current_sl * thr) if thr > 0 else 0.02
    if (current_sl - candidate_sl) < min_step:
        return (None, "VWAP")
    return (round(candidate_sl, 2), "VWAP")


def session_vwap_from_ohlcv(candles: list[dict[str, Any]]) -> float | None:
    """Typical-price volume-weighted average for session bars."""
    num = 0.0
    den = 0.0
    for c in candles:
        h = float(c.get("high") or 0)
        l_ = float(c.get("low") or 0)
        cl = float(c.get("close") or 0)
        v = float(c.get("volume") or 0)
        tp = (h + l_ + cl) / 3.0 if (h or l_ or cl) else cl
        if v > 0:
            num += tp * v
            den += v
    if den <= 0:
        return None
    return num / den


def compute_supertrend_trail_observability_series(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """SuperTrend bands, EMAs, and running typical-price series for charts."""
    if not candles or len(candles) < 20:
        return {"ok": False, "reason": "insufficient_candles"}
    ema_f_p = int(cfg.get("emaFast", 10))
    ema_s_p = int(cfg.get("emaSlow", 20))
    atr_p = int(cfg.get("atrPeriod", 10))
    mult = float(cfg.get("atrMultiplier", 3.0))
    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]
    direction, fu, fl = _supertrend_direction(highs, lows, closes, candles, atr_p, mult)
    ema_f = _ema_series(closes, ema_f_p)
    ema_s = _ema_series(closes, ema_s_p)
    vwap_run = running_typical_price_average_series(candles)
    times: list[int] = []
    for c in candles:
        dti = _parse_candle_time_ist(c)
        times.append(int(dti.timestamp()) if dti else 0)
    return {
        "ok": True,
        "times": times,
        "open": [float(c.get("open") or 0) for c in candles],
        "high": highs,
        "low": lows,
        "close": closes,
        "emaFast": ema_f,
        "emaSlow": ema_s,
        "supertrendUpper": fu,
        "supertrendLower": fl,
        "stDirection": [int(x) for x in direction],
        "vwap": vwap_run,
    }
