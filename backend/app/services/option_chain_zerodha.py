from __future__ import annotations

from collections import deque
import math
import os
from datetime import date, datetime, timedelta
import statistics
from typing import Any
from zoneinfo import ZoneInfo

from kiteconnect import KiteConnect

from app.services.option_greeks import compute_greeks

SPOT_SYMBOLS = {
    "NIFTY": "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX": "BSE:SENSEX",
    "FINNIFTY": "NSE:NIFTY FINANCIAL SERVICES",
}

# Kite historical_data from/to are interpreted in exchange local time (IST for NSE), not UTC.
_NSE_IST = ZoneInfo("Asia/Kolkata")

_BASE_SPOTS = {
    "NIFTY": 22450.0,
    "BANKNIFTY": 49200.0,
    "SENSEX": 74100.0,
    "FINNIFTY": 21250.0,
}

_BASE_CHG = {
    "NIFTY": 0.42,
    "BANKNIFTY": -0.17,
    "SENSEX": 0.28,
    "FINNIFTY": 0.36,
}

_INSTRUMENTS_CACHE_TTL_SEC = 600
_INSTRUMENTS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_RECENT_FETCHES: dict[str, deque[dict[int, dict[str, float]]]] = {}
_SPOT_TOKEN_CACHE: dict[str, tuple[float, int]] = {}


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema_val = values[0]
    for value in values[1:]:
        ema_val = (value * k) + (ema_val * (1 - k))
    return ema_val


def _rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = statistics.mean(gains[-period:])
    avg_loss = statistics.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _adx_from_candles(candles: list[dict[str, Any]], period: int = 14) -> float:
    """ADX from OHLC candles. Uses close-only TR when high/low unavailable. Returns 0 if insufficient data."""
    if len(candles) < period + 2:
        return 0.0

    def _wilder_smooth(vals: list[float], period: int) -> list[float]:
        out: list[float] = []
        for i, v in enumerate(vals):
            if i < period - 1:
                out.append(vals[i])
            elif i == period - 1:
                out.append(statistics.mean(vals[:period]))
            else:
                prev = out[-1]
                out.append((prev * (period - 1) + v) / period)
        return out

    tr_vals: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for i, c in enumerate(candles):
        h = float(c.get("high", 0))
        l_ = float(c.get("low", 0))
        cl = float(c.get("close", 0))
        prev_cl = float(candles[i - 1].get("close", cl)) if i > 0 else cl
        if i == 0:
            tr_vals.append(max(1e-6, (h - l_) if (h > 0 and l_ >= 0 and h >= l_) else 0))
            plus_dm.append(0.0)
            minus_dm.append(0.0)
        else:
            prev_h = float(candles[i - 1].get("high", h))
            prev_l = float(candles[i - 1].get("low", l_))
            tr = max(
                h - l_ if (h and l_) else 0,
                abs(h - prev_cl) if h else 0,
                abs(l_ - prev_cl) if l_ else 0,
                abs(cl - prev_cl),
            )
            tr_vals.append(tr if tr > 0 else abs(cl - prev_cl))
            up = h - prev_h if (h and prev_h) else max(0.0, cl - prev_cl)
            down = prev_l - l_ if (l_ and prev_l) else max(0.0, prev_cl - cl)
            plus_dm.append(up if up > down and up > 0 else 0.0)
            minus_dm.append(down if down > up and down > 0 else 0.0)

    tr_smooth = _wilder_smooth(tr_vals, period)
    plus_smooth = _wilder_smooth(plus_dm, period)
    minus_smooth = _wilder_smooth(minus_dm, period)

    di_plus = [
        100.0 * plus_smooth[i] / tr_smooth[i] if tr_smooth[i] > 0 else 0.0
        for i in range(len(tr_smooth))
    ]
    di_minus = [
        100.0 * minus_smooth[i] / tr_smooth[i] if tr_smooth[i] > 0 else 0.0
        for i in range(len(tr_smooth))
    ]
    dx_vals = [
        100.0 * abs(di_plus[i] - di_minus[i]) / (di_plus[i] + di_minus[i])
        if (di_plus[i] + di_minus[i]) > 0
        else 0.0
        for i in range(len(di_plus))
    ]
    adx_series = _wilder_smooth(dx_vals, period)
    return round(adx_series[-1], 2) if adx_series else 0.0


def _vwap_from_candles(candles: list[dict[str, Any]]) -> float:
    pv = 0.0
    vol = 0.0
    for c in candles:
        h = float(c.get("high", 0))
        l = float(c.get("low", 0))
        cl = float(c.get("close", 0))
        v = float(c.get("volume", 0))
        if v <= 0:
            continue
        pv += ((h + l + cl) / 3) * v
        vol += v
    return (pv / vol) if vol else 0.0


def _indicator_pack(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute indicators from candles. TODO: Accept strategy JSON for indicator params (EMA periods, RSI min/max, volume minRatio)."""
    if not candles:
        return {
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 50.0,
            "vwap": 0.0,
            "avgVolume": 0.0,
            "volumeSpikeRatio": 0.0,
            "score": 0,
            "primaryOk": False,
            "emaOk": False,
            "emaCrossoverOk": False,
            "rsiOk": False,
            "volumeOk": False,
            "signalEligible": False,
        }
    closes = [float(x.get("close", 0)) for x in candles]
    vols = [float(x.get("volume", 0)) for x in candles]
    close_now = closes[-1]
    vol_now = vols[-1]
    ema9 = _ema(closes[-30:], 9)
    ema21 = _ema(closes[-30:], 21)
    ema_crossover = False
    if len(closes) >= 22:
        ema9_prev = _ema(closes[-31:-1], 9)
        ema21_prev = _ema(closes[-31:-1], 21)
        ema_crossover = (ema9_prev <= ema21_prev) and (ema9 > ema21)
    rsi = _rsi(closes[-30:], 14)
    vwap = _vwap_from_candles(candles)
    avg_vol = statistics.mean(vols[-11:-1]) if len(vols) >= 11 else statistics.mean(vols[:-1] or [vol_now])
    vol_ratio = (vol_now / avg_vol) if avg_vol else 0.0
    primary_ok = close_now > vwap
    ema_ok = ema9 > ema21
    rsi_ok = 50 <= rsi <= 75
    volume_ok = vol_ratio > 1.5
    score = (1 if primary_ok else 0) + (1 if ema_ok else 0) + (1 if ema_crossover else 0) + (1 if rsi_ok else 0) + (1 if volume_ok else 0)
    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "vwap": round(vwap, 2),
        "avgVolume": float(avg_vol),
        "volumeSpikeRatio": round(vol_ratio, 2),
        "score": score,
        "primaryOk": primary_ok,
        "emaOk": ema_ok,
        "emaCrossoverOk": ema_crossover,
        "rsiOk": rsi_ok,
        "volumeOk": volume_ok,
        "signalEligible": primary_ok and score >= 3,
    }


def _bars_since_bullish_cross(ltps: list[float], fast_period: int = 9, slow_period: int = 21) -> int | None:
    """Returns bars since most recent bullish EMA crossover (ema_fast crossed above ema_slow), or None if no cross in history."""
    min_len = max(fast_period, slow_period) + 2
    if len(ltps) < min_len:
        return None
    for i in range(len(ltps) - 1, 0, -1):
        window_curr = ltps[: i + 1]
        window_prev = ltps[:i]
        if len(window_prev) < min_len - 1:
            continue
        ema_fast_prev = _ema(window_prev, fast_period)
        ema_slow_prev = _ema(window_prev, slow_period)
        ema_fast_curr = _ema(window_curr, fast_period)
        ema_slow_curr = _ema(window_curr, slow_period)
        if ema_fast_prev <= ema_slow_prev and ema_fast_curr > ema_slow_curr:
            return (len(ltps) - 1) - i
    return None


def _bars_since_bearish_cross(ltps: list[float], fast_period: int = 9, slow_period: int = 21) -> int | None:
    """Bars since fast EMA crossed below slow (bearish cross), or None if none in history."""
    min_len = max(fast_period, slow_period) + 2
    if len(ltps) < min_len:
        return None
    for i in range(len(ltps) - 1, 0, -1):
        window_curr = ltps[: i + 1]
        window_prev = ltps[:i]
        if len(window_prev) < min_len - 1:
            continue
        ema_fast_prev = _ema(window_prev, fast_period)
        ema_slow_prev = _ema(window_prev, slow_period)
        ema_fast_curr = _ema(window_curr, fast_period)
        ema_slow_curr = _ema(window_curr, slow_period)
        if ema_fast_prev >= ema_slow_prev and ema_fast_curr < ema_slow_curr:
            return (len(ltps) - 1) - i
    return None


def _indicator_pack_from_series_bearish(
    ltps: list[float],
    vols: list[float],
    score_threshold: int = 3,
    max_candles_since_cross: int | None = None,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_min_ratio: float = 1.5,
    *,
    include_volume_in_score: bool = True,
    include_ema_crossover_in_score: bool = True,
    leg_score_mode: str = "legacy",
    rsi_below_for_weak: float = 50.0,
    rsi_direct_band: bool = False,
) -> dict[str, Any]:
    """Bearish mirror of _indicator_pack_from_series: price below VWAP, EMA9 < EMA21, RSI on option LTP.

    ``leg_score_mode``:
    - ``legacy``: RSI in mirror band vs ``rsi_min``/``rsi_max``, crossover + optional volume in score.
    - ``three_factor``: +1 LTP<VWAP, +1 EMA9<EMA21, +1 RSI<``rsi_below_for_weak``; no crossover/volume in score.
      Skew/PCR bonuses are applied later in ``_apply_short_premium_skew_pcr_leg_scores``.

    ``rsi_direct_band``: when True (short premium), ``rsi_ok`` = ``rsi_min`` <= RSI <= ``rsi_max`` on the leg
    (e.g. overbought 65–100); applies to both ``legacy`` and ``three_factor`` RSI checks.
    """
    if not ltps:
        return {
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 50.0,
            "vwap": 0.0,
            "avgVolume": 0.0,
            "volumeSpikeRatio": 0.0,
            "score": 0,
            "technicalScore": 0,
            "primaryOk": False,
            "emaOk": False,
            "emaCrossoverOk": False,
            "rsiOk": False,
            "volumeOk": False,
            "signalEligible": False,
        }
    mode = (leg_score_mode or "legacy").strip().lower()
    three_factor = mode == "three_factor"
    rsi_bear_lo = max(0.0, 100.0 - float(rsi_max))
    rsi_bear_hi = min(100.0, 100.0 - float(rsi_min))
    if rsi_bear_lo > rsi_bear_hi:
        rsi_bear_lo, rsi_bear_hi = rsi_bear_hi, rsi_bear_lo
    close_now = ltps[-1]
    vol_now = vols[-1] if vols else 0.0
    ema9 = _ema(ltps[-30:], 9)
    ema21 = _ema(ltps[-30:], 21)
    ema_crossover = False
    if len(ltps) >= 22:
        if max_candles_since_cross is not None:
            bars_since = _bars_since_bearish_cross(ltps, 9, 21)
            ema_crossover = bars_since is not None and bars_since <= max_candles_since_cross
        else:
            ema9_prev = _ema(ltps[-31:-1], 9)
            ema21_prev = _ema(ltps[-31:-1], 21)
            ema_crossover = (ema9_prev >= ema21_prev) and (ema9 < ema21)
    if len(ltps) >= 3:
        rsi = _rsi(ltps[-30:], min(14, len(ltps) - 1))
    else:
        rsi = 50.0
    v_sum = sum(max(0.0, v) for v in vols)
    if v_sum > 0:
        vwap = sum(p * max(0.0, v) for p, v in zip(ltps, vols)) / v_sum
    else:
        vwap = statistics.mean(ltps)
    avg_vol = statistics.mean(vols[:-1]) if len(vols) > 1 else max(1.0, vol_now)
    vol_ratio = (vol_now / avg_vol) if avg_vol > 0 else 0.0
    primary_ok = close_now < vwap
    ema_ok = ema9 < ema21
    raw_vol_ok = vol_ratio > volume_min_ratio
    if three_factor:
        include_ema_crossover_in_score = False
        include_volume_in_score = False
        if rsi_direct_band:
            rlo, rhi = float(rsi_min), float(rsi_max)
            if rlo > rhi:
                rlo, rhi = rhi, rlo
            rsi_ok = rlo - 1e-9 <= rsi <= rhi + 1e-9
        else:
            thr = float(rsi_below_for_weak)
            rsi_ok = rsi < thr
        cross_pts = 0
        vol_pts = 0
        technical = (1 if primary_ok else 0) + (1 if ema_ok else 0) + (1 if rsi_ok else 0)
        score = technical
        volume_ok = True
    else:
        if rsi_direct_band:
            rlo, rhi = float(rsi_min), float(rsi_max)
            if rlo > rhi:
                rlo, rhi = rhi, rlo
            rsi_ok = rlo - 1e-9 <= rsi <= rhi + 1e-9
        else:
            rsi_ok = rsi_bear_lo <= rsi <= rsi_bear_hi
        volume_ok = raw_vol_ok if include_volume_in_score else True
        vol_pts = (1 if raw_vol_ok else 0) if include_volume_in_score else 0
        cross_pts = (1 if ema_crossover else 0) if include_ema_crossover_in_score else 0
        technical = (1 if primary_ok else 0) + (1 if ema_ok else 0) + cross_pts + (1 if rsi_ok else 0) + vol_pts
        score = technical
    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "vwap": round(vwap, 2),
        "avgVolume": float(round(avg_vol, 2)),
        "volumeSpikeRatio": round(vol_ratio, 2),
        "score": score,
        "technicalScore": score,
        "primaryOk": primary_ok,
        "emaOk": ema_ok,
        "emaCrossoverOk": ema_crossover,
        "rsiOk": rsi_ok,
        "volumeOk": volume_ok,
        "signalEligible": primary_ok and score >= score_threshold,
    }


def _max_candles_since_cross_int(raw: Any, default: int = 5) -> int:
    if raw is None:
        return max(1, default)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return max(1, default)


# Must match _bars_since_bullish_cross / _bars_since_bearish_cross (slow_period + 2).
_REGIME_LTP_MIN_LEN = max(9, 21) + 2


def _strike_leg_regime_sell_pe(
    ltps: list[float],
    vols: list[float],
    max_cross_i: int,
) -> tuple[bool, int | None]:
    """Sell PE: same regime geometry as sell CE — fresh EMA9 cross below EMA21; last LTP < leg VWAP."""
    if len(ltps) < _REGIME_LTP_MIN_LEN:
        return False, None
    n = min(len(ltps), len(vols))
    t = ltps[-n:]
    v = vols[-n:]
    v_sum = sum(max(0.0, x) for x in v)
    vwap = sum(p * max(0.0, vol) for p, vol in zip(t, v)) / v_sum if v_sum > 0 else statistics.mean(t)
    if not (t[-1] < vwap):
        return False, None
    bb = _bars_since_bearish_cross(t, 9, 21)
    if bb is None or bb > max_cross_i:
        return False, bb
    return True, bb


def _strike_leg_regime_sell_ce(
    ltps: list[float],
    vols: list[float],
    max_cross_i: int,
) -> tuple[bool, int | None]:
    """Sell CE: fresh EMA9 cross below EMA21 on this leg LTP series; last LTP < leg VWAP."""
    if len(ltps) < _REGIME_LTP_MIN_LEN:
        return False, None
    n = min(len(ltps), len(vols))
    t = ltps[-n:]
    v = vols[-n:]
    v_sum = sum(max(0.0, x) for x in v)
    vwap = sum(p * max(0.0, vol) for p, vol in zip(t, v)) / v_sum if v_sum > 0 else statistics.mean(t)
    if not (t[-1] < vwap):
        return False, None
    bb = _bars_since_bearish_cross(t, 9, 21)
    if bb is None or bb > max_cross_i:
        return False, bb
    return True, bb


def _resolve_regime_sell_pe_ce_at_strike(
    put_ltps: list[float],
    put_vols: list[float],
    call_ltps: list[float],
    call_vols: list[float],
    max_cross_i: int,
) -> tuple[bool, bool]:
    """(regimeSellPe, regimeSellCe). If both qualify, keep the side whose cross is more recent (smaller bars-since)."""
    pe_ok, pb = _strike_leg_regime_sell_pe(put_ltps, put_vols, max_cross_i)
    ce_ok, cb = _strike_leg_regime_sell_ce(call_ltps, call_vols, max_cross_i)
    if pe_ok and ce_ok:
        if pb is not None and cb is not None:
            if pb < cb:
                return True, False
            if cb < pb:
                return False, True
            return False, False
        return False, False
    return pe_ok, ce_ok


def _spot_trend_payload_from_candles(
    candles: list[dict[str, Any]],
    indicator_params: dict[str, Any],
    score_threshold: int,
) -> dict[str, Any]:
    """NIFTY spot trend scores for short-premium: bullish vs bearish regime (mutually exclusive when possible)."""
    closes: list[float] = []
    vols: list[float] = []
    for c in candles:
        cl = float(c.get("close", 0) or 0)
        if cl <= 0:
            continue
        closes.append(cl)
        vols.append(float(c.get("volume") or 0))
    if len(closes) < 5:
        return {"spotBullishScore": 0, "spotBearishScore": 0, "spotRegime": None}
    ip = indicator_params or {}
    mode = str(ip.get("spotRegimeMode") or ip.get("spot_regime_mode") or "").strip().lower()
    if mode == "ema_cross_vwap":
        # Regime is computed per strike on option LTP series in _build_live_chain, not on spot.
        return {"spotBullishScore": 0, "spotBearishScore": 0, "spotRegime": None}
    max_cross = ip.get("max_candles_since_cross")
    rsi_min = float(ip.get("rsi_min", 50))
    rsi_max = float(ip.get("rsi_max", 75))
    vol_min = float(ip.get("volume_min_ratio", 1.5))
    inc_cross = bool(ip.get("include_ema_crossover_in_score", True))
    strict_bull = bool(ip.get("strict_bullish_comparisons", False))
    bull = _indicator_pack_from_series(
        closes,
        vols,
        score_threshold,
        max_cross,
        rsi_min,
        rsi_max,
        vol_min,
        include_ema_crossover_in_score=inc_cross,
        strict_bullish_comparisons=strict_bull,
    )
    leg_mode_spot = str(ip.get("shortPremiumLegScoreMode") or "").strip().lower()
    rsi_below_spot = float(ip.get("shortPremiumRsiBelow", 50) or 50)
    inc_cross_bear_spot = bool(ip.get("include_ema_crossover_in_score", True))
    inc_vol_bear_spot = bool(ip.get("include_volume_in_leg_score", True))
    if leg_mode_spot == "three_factor":
        inc_cross_bear_spot = False
        inc_vol_bear_spot = False
    bear = _indicator_pack_from_series_bearish(
        closes,
        vols,
        score_threshold,
        max_cross,
        rsi_min,
        rsi_max,
        vol_min,
        include_volume_in_score=inc_vol_bear_spot,
        include_ema_crossover_in_score=inc_cross_bear_spot,
        leg_score_mode=leg_mode_spot or "legacy",
        rsi_below_for_weak=rsi_below_spot,
    )
    bs = int(bull["score"])
    be = int(bear["score"])
    st = int(score_threshold)
    if bs >= st and be < st:
        regime: str | None = "bullish"
    elif be >= st and bs < st:
        regime = "bearish"
    else:
        regime = None
    return {"spotBullishScore": bs, "spotBearishScore": be, "spotRegime": regime}


def _indicator_pack_from_series(
    ltps: list[float],
    vols: list[float],
    score_threshold: int = 3,
    max_candles_since_cross: int | None = None,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_min_ratio: float = 1.5,
    *,
    include_ema_crossover_in_score: bool = True,
    strict_bullish_comparisons: bool = False,
    include_volume_in_score: bool = True,
    require_rsi_for_eligible: bool = False,
) -> dict[str, Any]:
    if not ltps:
        return {
            "ema9": 0.0,
            "ema21": 0.0,
            "rsi": 50.0,
            "vwap": 0.0,
            "avgVolume": 0.0,
            "volumeSpikeRatio": 0.0,
            "score": 0,
            "primaryOk": False,
            "emaOk": False,
            "emaCrossoverOk": False,
            "rsiOk": False,
            "volumeOk": False,
            "signalEligible": False,
        }
    close_now = ltps[-1]
    vol_now = vols[-1] if vols else 0.0
    ema9 = _ema(ltps[-30:], 9)
    ema21 = _ema(ltps[-30:], 21)
    ema_crossover = False
    if len(ltps) >= 22:
        if max_candles_since_cross is not None:
            bars_since = _bars_since_bullish_cross(ltps, 9, 21)
            ema_crossover = bars_since is not None and bars_since <= max_candles_since_cross
        else:
            ema9_prev = _ema(ltps[-31:-1], 9)
            ema21_prev = _ema(ltps[-31:-1], 21)
            ema_crossover = (ema9_prev <= ema21_prev) and (ema9 > ema21)
    if len(ltps) >= 3:
        rsi = _rsi(ltps[-30:], min(14, len(ltps) - 1))
    else:
        rsi = 50.0
    v_sum = sum(max(0.0, v) for v in vols)
    if v_sum > 0:
        vwap = sum(p * max(0.0, v) for p, v in zip(ltps, vols)) / v_sum
    else:
        vwap = statistics.mean(ltps)
    avg_vol = statistics.mean(vols[:-1]) if len(vols) > 1 else max(1.0, vol_now)
    vol_ratio = (vol_now / avg_vol) if avg_vol > 0 else 0.0
    if strict_bullish_comparisons:
        primary_ok = close_now > vwap
        ema_ok = ema9 > ema21
    else:
        primary_ok = close_now >= vwap
        ema_ok = ema9 >= ema21
    rsi_ok = rsi_min <= rsi <= rsi_max
    raw_vol_ok = vol_ratio > volume_min_ratio
    volume_ok = raw_vol_ok if include_volume_in_score else True
    cross_pts = (1 if ema_crossover else 0) if include_ema_crossover_in_score else 0
    vol_pts = (1 if raw_vol_ok else 0) if include_volume_in_score else 0
    score = (
        (1 if primary_ok else 0)
        + (1 if ema_ok else 0)
        + cross_pts
        + (1 if rsi_ok else 0)
        + vol_pts
    )
    return {
        "ema9": round(ema9, 2),
        "ema21": round(ema21, 2),
        "rsi": round(rsi, 2),
        "vwap": round(vwap, 2),
        "avgVolume": float(round(avg_vol, 2)),
        "volumeSpikeRatio": round(vol_ratio, 2),
        "score": score,
        "primaryOk": primary_ok,
        "emaOk": ema_ok,
        "emaCrossoverOk": ema_crossover,
        "rsiOk": rsi_ok,
        "volumeOk": volume_ok,
        "signalEligible": primary_ok
        and score >= score_threshold
        and (rsi_ok if require_rsi_for_eligible else True),
    }


def _indicator_pack_from_quote_fallback(
    quote: dict[str, Any],
    last_price: float,
    last_vol: float,
    score_threshold: int = 3,
    max_candles_since_cross: int | None = None,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_min_ratio: float = 1.5,
    *,
    include_ema_crossover_in_score: bool = True,
    strict_bullish_comparisons: bool = False,
    include_volume_in_score: bool = True,
    require_rsi_for_eligible: bool = False,
) -> dict[str, Any]:
    ohlc = quote.get("ohlc") or {}
    o = float(ohlc.get("open") or last_price or 0.0)
    h = float(ohlc.get("high") or o or last_price or 0.0)
    l = float(ohlc.get("low") or o or last_price or 0.0)
    c = float(ohlc.get("close") or o or last_price or 0.0)
    lp = float(last_price or c or o or 0.0)
    base_vol = max(1.0, float(last_vol or 0.0))
    ltps = [x for x in [o, l, h, c, lp] if x > 0]
    vols = [base_vol * 0.7, base_vol * 0.8, base_vol * 0.9, base_vol * 0.95, base_vol]
    return _indicator_pack_from_series(
        ltps,
        vols[: len(ltps)],
        score_threshold,
        max_candles_since_cross,
        rsi_min,
        rsi_max,
        volume_min_ratio,
        include_ema_crossover_in_score=include_ema_crossover_in_score,
        strict_bullish_comparisons=strict_bullish_comparisons,
        include_volume_in_score=include_volume_in_score,
        require_rsi_for_eligible=require_rsi_for_eligible,
    )


def _indicator_pack_from_quote_fallback_bearish(
    quote: dict[str, Any],
    last_price: float,
    last_vol: float,
    score_threshold: int = 3,
    max_candles_since_cross: int | None = None,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_min_ratio: float = 1.5,
    *,
    include_volume_in_score: bool = True,
    include_ema_crossover_in_score: bool = True,
    leg_score_mode: str = "legacy",
    rsi_below_for_weak: float = 50.0,
    rsi_direct_band: bool = False,
) -> dict[str, Any]:
    """Same synthetic OHLC path as bullish fallback, but bearish pack (premium weakness on option LTP)."""
    ohlc = quote.get("ohlc") or {}
    o = float(ohlc.get("open") or last_price or 0.0)
    h = float(ohlc.get("high") or o or last_price or 0.0)
    l = float(ohlc.get("low") or o or last_price or 0.0)
    c = float(ohlc.get("close") or o or last_price or 0.0)
    lp = float(last_price or c or o or 0.0)
    base_vol = max(1.0, float(last_vol or 0.0))
    ltps = [x for x in [o, l, h, c, lp] if x > 0]
    vols = [base_vol * 0.7, base_vol * 0.8, base_vol * 0.9, base_vol * 0.95, base_vol]
    return _indicator_pack_from_series_bearish(
        ltps,
        vols[: len(ltps)],
        score_threshold,
        max_candles_since_cross,
        rsi_min,
        rsi_max,
        volume_min_ratio,
        include_volume_in_score=include_volume_in_score,
        include_ema_crossover_in_score=include_ema_crossover_in_score,
        leg_score_mode=leg_score_mode,
        rsi_below_for_weak=rsi_below_for_weak,
        rsi_direct_band=rsi_direct_band,
    )


def _parse_expiry_frontend(expiry_str: str) -> date:
    return datetime.strptime(expiry_str.strip().upper(), "%d%b%Y").date()


def _format_expiry(d: date) -> str:
    return d.strftime("%d%b%Y").upper()


def _next_weekday_dates(weekday: int, count: int) -> list[date]:
    out: list[date] = []
    d = date.today()
    while len(out) < count:
        d += timedelta(days=1)
        if d.weekday() == weekday:
            out.append(d)
    return out


def get_expiries_for_instrument(instrument: str) -> list[str]:
    """Fallback only: next few weekly-ish dates (may not match real NFO expiries). Prefer ``get_expiries_for_analytics``."""
    key = instrument.strip().upper()
    weekday_map = {
        "NIFTY": 1,  # Tuesday
        "BANKNIFTY": 1,
        "FINNIFTY": 1,
        "SENSEX": 3,  # Thursday
    }
    wd = weekday_map.get(key, 1)
    return [_format_expiry(x) for x in _next_weekday_dates(wd, 6)]


def verify_kite_session_sync(kite: KiteConnect | None) -> bool:
    """True if access token is valid (lightweight profile call)."""
    if kite is None:
        return False
    try:
        kite.profile()
        return True
    except Exception:
        return False


def list_expiries_from_nfo_sync(kite: KiteConnect, instrument: str, max_expiries: int = 16) -> list[str]:
    """
    Distinct option expiries from Zerodha NFO for this underlying name.
    NIFTY / BANKNIFTY / FINNIFTY only (Sensex FNO is typically BFO — not covered by current chain builder).
    """
    inst = instrument.strip().upper()
    if inst not in {"NIFTY", "BANKNIFTY", "FINNIFTY"}:
        return []
    rows = _load_option_instruments(kite, inst)
    today = _calendar_today_ist()
    seen: set[date] = set()
    for row in rows:
        exp = _expiry_as_date(row.get("expiry"))
        if exp is None or exp < today:
            continue
        seen.add(exp)
    out_dates = sorted(seen)[: max(1, int(max_expiries))]
    return [_format_expiry(d) for d in out_dates]


def get_expiries_for_analytics(kite: KiteConnect | None, instrument: str) -> tuple[list[str], str]:
    """
    Prefer broker-listed NFO expiries; fallback to estimated weeklies.
    Returns (expiries_ddmmmyyyy, source) where source is ``zerodha_nfo`` or ``estimated_weeklies``.
    """
    inst = instrument.strip().upper()
    if kite is not None and inst in ("NIFTY", "BANKNIFTY", "FINNIFTY"):
        try:
            broker_list = list_expiries_from_nfo_sync(kite, inst)
            if broker_list:
                return broker_list, "zerodha_nfo"
        except Exception:
            pass
    return get_expiries_for_instrument(inst), "estimated_weeklies"


def pick_primary_expiry_str(kite: KiteConnect | None, instrument: str = "NIFTY") -> str | None:
    """Nearest future NFO expiry when Kite is available; else estimated weekly list."""
    inst = instrument.strip().upper()
    expiries, _ = get_expiries_for_analytics(kite, inst)
    return expiries[0] if expiries else None


def _calendar_today_ist() -> date:
    """IST calendar date for NSE DTE math (avoids UTC server date skew vs exchange)."""
    return datetime.now(_NSE_IST).date()


def select_expiry_min_dte_and_weekday(
    expiries: list[str],
    today: date,
    *,
    min_dte_days: int,
    weekday: int | None,
) -> str | None:
    """
    Among broker-listed expiries, pick the **earliest** date that satisfies:
    - calendar DTE >= min_dte_days (IST ``today`` vs expiry date), and
    - if ``weekday`` is not None, expiry **date** must be that Python weekday (Mon=0..Sun=6).

    Returns None if nothing qualifies (no silent fallback to nearer weekly).
    """
    need = max(0, int(min_dte_days))
    qualified: list[tuple[date, str]] = []
    for exp_str in expiries:
        try:
            d = datetime.strptime(exp_str.strip().upper(), "%d%b%Y").date()
        except ValueError:
            continue
        if (d - today).days >= need:
            qualified.append((d, exp_str))
    if not qualified:
        return None
    qualified.sort(key=lambda x: x[0])
    if weekday is None:
        return qualified[0][1]
    for d, exp_str in qualified:
        if d.weekday() == weekday:
            return exp_str
    return None


def first_expiry_meeting_min_calendar_dte(
    expiries: list[str],
    today: date,
    *,
    min_dte_days: int,
) -> str | None:
    """Backward-compatible: earliest expiry meeting min DTE only (no weekday filter)."""
    return select_expiry_min_dte_and_weekday(
        expiries, today, min_dte_days=min_dte_days, weekday=None
    )


def resolve_expiry_min_dte_weekday_with_fallback(
    expiries: list[str],
    today: date,
    *,
    min_dte_days: int,
    weekday: int | None,
) -> str | None:
    """
    Prefer ``weekday`` when set; if no listed expiry matches that day while meeting min DTE,
    fall back to the earliest expiry that still satisfies min calendar DTE (same as weekday=None).
    Ensures chain fetch can proceed when the broker list has no qualifying weekly on the nominal day.
    """
    picked = select_expiry_min_dte_and_weekday(
        expiries, today, min_dte_days=min_dte_days, weekday=weekday
    )
    if picked is not None or weekday is None:
        return picked
    return select_expiry_min_dte_and_weekday(
        expiries, today, min_dte_days=min_dte_days, weekday=None
    )


def pick_expiry_with_min_calendar_dte(
    kite: KiteConnect | None,
    instrument: str = "NIFTY",
    *,
    min_dte_days: int = 2,
    weekday: int | None = 1,
) -> str | None:
    """
    Listed weekly expiry: calendar DTE (IST) >= min_dte_days, optionally matching NIFTY weekly expiry weekday.
    ``weekday`` None = ignore weekday (earliest qualifying). Default weekday 1 = Tuesday (typical NIFTY weekly).
    If no expiry matches ``weekday`` but some meet min DTE, falls back to earliest min-DTE expiry (any weekday).
    Returns None when no expiry qualifies min DTE.
    """
    inst = instrument.strip().upper()
    expiries, _ = get_expiries_for_analytics(kite, inst)
    if not expiries:
        return None
    today = _calendar_today_ist()
    return resolve_expiry_min_dte_weekday_with_fallback(
        expiries, today, min_dte_days=min_dte_days, weekday=weekday
    )


def _ltp_change_pct(last_price: float, prev_close: float) -> float:
    if prev_close == 0:
        return 0.0
    return round((last_price - prev_close) / prev_close * 100, 2)


def fetch_indices_spot_sync(kite: KiteConnect | None = None) -> dict[str, dict[str, Any]]:
    keys = ("NIFTY", "BANKNIFTY", "SENSEX")
    if kite:
        symbols = [SPOT_SYMBOLS[k] for k in keys]
        try:
            q = kite.quote(symbols)
            data = q.get("data") if isinstance(q, dict) and "data" in q else q
            result: dict[str, dict[str, Any]] = {}
            for key in keys:
                entry = data.get(SPOT_SYMBOLS[key], {})
                spot = float(entry.get("last_price", 0) or 0)
                o = entry.get("ohlc") or {}
                prev = float(o.get("close") or o.get("open") or spot or 1)
                result[key] = {"spot": spot, "spotChgPct": _ltp_change_pct(spot, prev)}
            return result
        except Exception:
            pass
    return {k: {"spot": _BASE_SPOTS[k], "spotChgPct": _BASE_CHG[k]} for k in keys}


def _buildup(prev_oi: float, curr_oi: float, prev_ltp: float, curr_ltp: float) -> str:
    if prev_oi <= 0 and prev_ltp <= 0:
        return "—"
    oi_up = curr_oi > prev_oi
    ltp_up = curr_ltp > prev_ltp
    if oi_up and ltp_up:
        return "Long Buildup"
    if oi_up and not ltp_up:
        return "Short Buildup"
    if not oi_up and ltp_up:
        return "Short Covering"
    return "Long Unwinding"


def _synthetic_option_ltp(spot: float, strike: float, is_call: bool, t_factor: float) -> float:
    intrinsic = max(0.0, spot - strike) if is_call else max(0.0, strike - spot)
    distance = abs(spot - strike)
    time_value = max(8.0, (120 - min(110, distance / max(1, spot) * 10000)) * t_factor)
    return round(max(0.5, intrinsic + time_value), 2)


def _resolve_spot(instrument: str, kite: KiteConnect | None) -> tuple[float, float]:
    key = instrument.strip().upper()
    if kite:
        try:
            indices = fetch_indices_spot_sync(kite)
            if key in indices:
                return float(indices[key]["spot"]), float(indices[key]["spotChgPct"])
        except Exception:
            pass
    return _BASE_SPOTS.get(key, 22450.0), _BASE_CHG.get(key, 0.0)


def _get_spot_token(kite: KiteConnect, instrument: str) -> int | None:
    """Resolve instrument (e.g. NIFTY) to NSE index instrument_token for historical data."""
    key = instrument.strip().upper()
    sym = SPOT_SYMBOLS.get(key)  # e.g. "NSE:NIFTY 50"
    if not sym:
        return None
    now_ts = datetime.utcnow().timestamp()
    cached = _SPOT_TOKEN_CACHE.get(key)
    if cached and (now_ts - cached[0]) < _INSTRUMENTS_CACHE_TTL_SEC:
        return cached[1]
    try:
        for exch in ("NSE", "BSE"):
            rows = kite.instruments(exch)
            for r in rows or []:
                ts = str(r.get("tradingsymbol", "")).strip()
                name = str(r.get("name", "")).strip()
                if ts == sym.split(":")[-1] or name == sym.split(":")[-1]:
                    tok = int(r.get("instrument_token", 0))
                    if tok:
                        _SPOT_TOKEN_CACHE[key] = (now_ts, tok)
                        return tok
    except Exception:
        pass
    return None


def _fetch_spot_candles(kite: KiteConnect, instrument: str, interval: str = "minute") -> list[dict[str, Any]]:
    """Fetch recent spot candles for ADX. Returns list of {open, high, low, close, volume}."""
    tok = _get_spot_token(kite, instrument)
    if not tok:
        return []
    try:
        to_dt = datetime.now(_NSE_IST)
        from_dt = to_dt - timedelta(days=1)
        data = kite.historical_data(tok, from_dt, to_dt, interval)
        if isinstance(data, list):
            return [{"open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"], "volume": d.get("volume", 0)} for d in data]
    except Exception:
        pass
    return []


def fetch_index_candles_sync(
    kite: KiteConnect | None,
    instrument: str,
    interval: str = "5minute",
    days_back: int = 5,
) -> list[dict[str, Any]]:
    """Fetch OHLCV for an index (NIFTY, etc.) via Kite historical_data.

    ``interval`` examples: ``minute``, ``3minute``, ``5minute``, ``15minute``, ``60minute``, ``day``.

    Uses **IST** bounds for ``from``/``to``. Passing UTC wall clock makes Zerodha treat it as IST,
    so e.g. 10:52 IST becomes an effective end time of ~05:22 IST and drops the current session.
    """
    if kite is None:
        return []
    tok = _get_spot_token(kite, instrument)
    if not tok:
        return []
    try:
        to_dt = datetime.now(_NSE_IST)
        from_dt = to_dt - timedelta(days=max(1, int(days_back)))
        data = kite.historical_data(tok, from_dt, to_dt, interval)
        if isinstance(data, list):
            out: list[dict[str, Any]] = []
            for d in data:
                dt = d.get("date")
                if hasattr(dt, "isoformat"):
                    t_iso = dt.isoformat()
                else:
                    t_iso = str(dt) if dt else ""
                out.append(
                    {
                        "open": float(d["open"]),
                        "high": float(d["high"]),
                        "low": float(d["low"]),
                        "close": float(d["close"]),
                        "volume": float(d.get("volume") or 0),
                        "time": t_iso,
                    }
                )
            return out
    except Exception:
        pass
    return []


def _build_synthetic_chain(
    instrument: str,
    expiry_date: date,
    spot: float,
    strikes_up: int,
    strikes_down: int,
) -> list[dict[str, Any]]:
    step = 50 if instrument == "NIFTY" else 100
    atm = round(spot / step) * step
    strikes = [atm + (i * step) for i in range(-strikes_down, strikes_up + 1)]
    t_days = max(1, (expiry_date - date.today()).days)
    t_factor = max(0.8, min(1.35, t_days / 15))

    chain: list[dict[str, Any]] = []
    for idx, strike in enumerate(strikes):
        call_ltp = _synthetic_option_ltp(spot, strike, True, t_factor)
        put_ltp = _synthetic_option_ltp(spot, strike, False, t_factor)

        call_oi = max(50000, int(170000 - abs(atm - strike) * 220 + idx * 900))
        put_oi = max(50000, int(168000 - abs(atm - strike) * 210 + (len(strikes) - idx) * 820))
        call_prev_oi = call_oi * (0.96 + (idx % 5) * 0.01)
        put_prev_oi = put_oi * (0.95 + (idx % 4) * 0.012)
        call_prev_ltp = call_ltp * (0.97 + (idx % 3) * 0.01)
        put_prev_ltp = put_ltp * (0.97 + ((idx + 1) % 3) * 0.01)

        c_delta, c_theta, c_iv = compute_greeks(spot, strike, expiry_date, call_ltp, "CE")
        p_delta, p_theta, p_iv = compute_greeks(spot, strike, expiry_date, put_ltp, "PE")

        call_oi_chg = round(((call_oi - call_prev_oi) / call_prev_oi) * 100, 2) if call_prev_oi else 0.0
        put_oi_chg = round(((put_oi - put_prev_oi) / put_prev_oi) * 100, 2) if put_prev_oi else 0.0
        call_ltp_chg = round(((call_ltp - call_prev_ltp) / call_prev_ltp) * 100, 2) if call_prev_ltp else 0.0
        put_ltp_chg = round(((put_ltp - put_prev_ltp) / put_prev_ltp) * 100, 2) if put_prev_ltp else 0.0

        chain.append(
            {
                "strike": strike,
                "call": {
                    "buildup": _buildup(call_prev_oi, call_oi, call_prev_ltp, call_ltp),
                    "oiChgPct": call_oi_chg,
                    "theta": c_theta,
                    "delta": c_delta,
                    "iv": c_iv,
                    "volume": str(max(1000, int(call_oi * 0.18))),
                    "oi": str(int(call_oi)),
                    "ltpChg": call_ltp_chg,
                    "ltp": call_ltp,
                    "ema9": round(call_ltp * 0.985, 2),
                    "ema21": round(call_ltp * 0.965, 2),
                    "rsi": round(57 + ((idx + 1) % 6) * 2.3, 2),
                    "vwap": round(call_ltp * 0.975, 2),
                    "avgVolume": float(max(1000, int(call_oi * 0.11))),
                    "volumeSpikeRatio": round((max(1000, int(call_oi * 0.18))) / max(1000, int(call_oi * 0.11)), 2),
                    "score": 3 if abs(atm - strike) <= step * 2 else 2,
                    "primaryOk": True,
                    "emaOk": True,
                    "emaCrossoverOk": False,
                    "rsiOk": True,
                    "volumeOk": abs(atm - strike) <= step * 2,
                    "signalEligible": abs(atm - strike) <= step * 2,
                },
                "put": {
                    "pcr": round((put_oi / call_oi), 2) if call_oi else 0.0,
                    "ltp": put_ltp,
                    "ltpChg": put_ltp_chg,
                    "oi": str(int(put_oi)),
                    "oiChgPct": put_oi_chg,
                    "volume": str(max(1000, int(put_oi * 0.17))),
                    "iv": p_iv,
                    "delta": p_delta,
                    "theta": p_theta,
                    "buildup": _buildup(put_prev_oi, put_oi, put_prev_ltp, put_ltp),
                    "ema9": round(put_ltp * 0.985, 2),
                    "ema21": round(put_ltp * 0.965, 2),
                    "rsi": round(55 + ((idx + 2) % 6) * 2.4, 2),
                    "vwap": round(put_ltp * 0.975, 2),
                    "avgVolume": float(max(1000, int(put_oi * 0.11))),
                    "volumeSpikeRatio": round((max(1000, int(put_oi * 0.17))) / max(1000, int(put_oi * 0.11)), 2),
                    "score": 3 if abs(atm - strike) <= step * 2 else 2,
                    "primaryOk": True,
                    "emaOk": True,
                    "emaCrossoverOk": False,
                    "rsiOk": True,
                    "volumeOk": abs(atm - strike) <= step * 2,
                    "signalEligible": abs(atm - strike) <= step * 2,
                },
            }
        )
    return chain


def _step_for_instrument(instrument: str) -> int:
    return 50 if instrument == "NIFTY" else 100


def _window_size() -> int:
    """
    Snapshots kept per (instrument, expiry) for option-leg LTP/volume series.
    EMA21 + fresh-cross detection needs ~23+ points (_bars_since_* uses slow_period+2).
    Default 30 so short-premium ``ema_cross_vwap`` regime can become true after warm-up.
    """
    try:
        value = int(os.getenv("OPTION_CHAIN_RECENT_WINDOW", "30"))
    except ValueError:
        value = 30
    return max(10, min(60, value))


def _expiry_as_date(raw: Any) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
    return None


def _load_option_instruments(kite: KiteConnect, instrument: str) -> list[dict[str, Any]]:
    now_ts = datetime.utcnow().timestamp()
    cached = _INSTRUMENTS_CACHE.get(instrument)
    if cached and (now_ts - cached[0]) < _INSTRUMENTS_CACHE_TTL_SEC:
        return cached[1]

    all_rows = kite.instruments("NFO")
    filtered: list[dict[str, Any]] = []
    for row in all_rows:
        if str(row.get("name", "")).upper() != instrument:
            continue
        i_type = str(row.get("instrument_type", "")).upper()
        if i_type not in {"CE", "PE"}:
            continue
        filtered.append(row)

    _INSTRUMENTS_CACHE[instrument] = (now_ts, filtered)
    return filtered


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _vix_from_quote(kite: KiteConnect | None) -> float | None:
    if kite is None:
        return None
    try:
        q = kite.quote(["NSE:INDIA VIX"])
        data = q.get("data") if isinstance(q, dict) and "data" in q else q
        v = data.get("NSE:INDIA VIX", {}).get("last_price")
        if v is None:
            return None
        return round(float(v), 2)
    except Exception:
        return None


def _extract_volume_from_quote(quote: dict[str, Any]) -> float:
    # Zerodha quote payloads can expose traded volume under different keys
    # depending on segment/instrument shape.
    return float(
        quote.get("volume")
        or quote.get("volume_traded")
        or quote.get("traded_volume")
        or quote.get("volumeTraded")
        or 0.0
    )


def _build_live_chain(
    kite: KiteConnect,
    instrument: str,
    expiry_date: date,
    spot: float,
    strikes_up: int,
    strikes_down: int,
    score_threshold: int = 3,
    indicator_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    step = _step_for_instrument(instrument)
    atm = round(spot / step) * step
    strikes = [atm + (i * step) for i in range(-strikes_down, strikes_up + 1)]
    strike_set = set(strikes)

    instruments = _load_option_instruments(kite, instrument)
    by_key: dict[tuple[int, str], dict[str, Any]] = {}
    for row in instruments:
        exp = _expiry_as_date(row.get("expiry"))
        if exp != expiry_date:
            continue
        strike = int(float(row.get("strike", 0)))
        if strike not in strike_set:
            continue
        opt_type = str(row.get("instrument_type", "")).upper()
        by_key[(strike, opt_type)] = row

    quote_symbols: list[str] = []
    symbol_to_key: dict[str, tuple[int, str]] = {}
    for strike in strikes:
        for opt_type in ("CE", "PE"):
            inst = by_key.get((strike, opt_type))
            if not inst:
                continue
            symbol = f"NFO:{inst['tradingsymbol']}"
            quote_symbols.append(symbol)
            symbol_to_key[symbol] = (strike, opt_type)

    if not quote_symbols:
        raise ValueError(
            f"No NFO option contracts for {instrument} on expiry {expiry_date.isoformat()} "
            f"in the selected strike window. Choose an expiry from the dropdown (Zerodha-listed dates)."
        )

    quote_data: dict[str, Any] = {}
    for chunk_symbols in _chunk(quote_symbols, 200):
        q = kite.quote(chunk_symbols)
        data = q.get("data") if isinstance(q, dict) and "data" in q else q
        if isinstance(data, dict):
            quote_data.update(data)

    prev_key = f"{instrument}:{expiry_date.isoformat()}"
    previous = _RECENT_FETCHES.get(prev_key)
    prev_snapshot = previous[-1] if previous and len(previous) else {}
    history_snapshots = list(previous) if previous else []
    current_snapshot: dict[int, dict[str, float]] = {}
    chain: list[dict[str, Any]] = []
    ip_global = indicator_params or {}
    short_premium_legs = str(ip_global.get("positionIntent", "")).lower() == "short_premium"
    reg_srm = str(ip_global.get("spotRegimeMode") or ip_global.get("spot_regime_mode") or "").strip().lower()
    leg_mode_short = str(ip_global.get("shortPremiumLegScoreMode") or "").strip().lower()
    req_rsi_eligible = bool(ip_global.get("requireRsiForEligible"))
    rsi_below_short = float(ip_global.get("shortPremiumRsiBelow", 50) or 50)
    rsi_direct_short = bool(ip_global.get("shortPremiumRsiDirectBand"))

    for strike in strikes:
        call_q = quote_data.get(f"NFO:{by_key[(strike, 'CE')]['tradingsymbol']}") if (strike, "CE") in by_key else {}
        put_q = quote_data.get(f"NFO:{by_key[(strike, 'PE')]['tradingsymbol']}") if (strike, "PE") in by_key else {}
        if not call_q and not put_q:
            continue

        call_ltp = float(call_q.get("last_price") or 0.0)
        put_ltp = float(put_q.get("last_price") or 0.0)
        call_oi = float(call_q.get("oi") or 0.0)
        put_oi = float(put_q.get("oi") or 0.0)
        call_vol = _extract_volume_from_quote(call_q)
        put_vol = _extract_volume_from_quote(put_q)
        call_prev_close = float((call_q.get("ohlc") or {}).get("close") or call_ltp or 1.0)
        put_prev_close = float((put_q.get("ohlc") or {}).get("close") or put_ltp or 1.0)
        if call_ltp <= 0:
            call_ltp = call_prev_close
        if put_ltp <= 0:
            put_ltp = put_prev_close

        prev = prev_snapshot.get(strike, {})
        prev_call_oi = float(prev.get("call_oi", call_oi))
        prev_put_oi = float(prev.get("put_oi", put_oi))
        prev_call_ltp = float(prev.get("call_ltp", call_ltp))
        prev_put_ltp = float(prev.get("put_ltp", put_ltp))
        prev_call_vol = float(prev.get("call_vol", call_vol))
        prev_put_vol = float(prev.get("put_vol", put_vol))

        call_ltp_series = [float(s.get(strike, {}).get("call_ltp", 0.0)) for s in history_snapshots]
        put_ltp_series = [float(s.get(strike, {}).get("put_ltp", 0.0)) for s in history_snapshots]
        call_vol_series = [float(s.get(strike, {}).get("call_vol", 0.0)) for s in history_snapshots]
        put_vol_series = [float(s.get(strike, {}).get("put_vol", 0.0)) for s in history_snapshots]
        call_ltp_series = [x for x in call_ltp_series if x > 0]
        put_ltp_series = [x for x in put_ltp_series if x > 0]
        call_vol_series = [max(0.0, x) for x in call_vol_series[-len(call_ltp_series) :]]
        put_vol_series = [max(0.0, x) for x in put_vol_series[-len(put_ltp_series) :]]
        call_ltp_series.append(max(0.0, call_ltp))
        put_ltp_series.append(max(0.0, put_ltp))
        call_vol_series.append(max(0.0, call_vol))
        put_vol_series.append(max(0.0, put_vol))
        ip = ip_global
        max_cross = ip.get("max_candles_since_cross")
        rsi_min = float(ip.get("rsi_min", 50))
        rsi_max = float(ip.get("rsi_max", 75))
        vol_min = float(ip.get("volume_min_ratio", 1.5))
        inc_cross = bool(ip_global.get("include_ema_crossover_in_score", True))
        strict_bull = bool(ip_global.get("strict_bullish_comparisons", False))
        inc_vol_score = bool(ip_global.get("include_volume_in_leg_score", True))
        inc_cross_bear = inc_cross
        inc_vol_bear = inc_vol_score
        if short_premium_legs and leg_mode_short == "three_factor":
            inc_cross_bear = False
            inc_vol_bear = False
        if short_premium_legs:
            call_ind = _indicator_pack_from_series_bearish(
                call_ltp_series,
                call_vol_series,
                score_threshold,
                max_cross,
                rsi_min,
                rsi_max,
                vol_min,
                include_volume_in_score=inc_vol_bear,
                include_ema_crossover_in_score=inc_cross_bear,
                leg_score_mode=leg_mode_short or "legacy",
                rsi_below_for_weak=rsi_below_short,
                rsi_direct_band=rsi_direct_short,
            )
            put_ind = _indicator_pack_from_series_bearish(
                put_ltp_series,
                put_vol_series,
                score_threshold,
                max_cross,
                rsi_min,
                rsi_max,
                vol_min,
                include_volume_in_score=inc_vol_bear,
                include_ema_crossover_in_score=inc_cross_bear,
                leg_score_mode=leg_mode_short or "legacy",
                rsi_below_for_weak=rsi_below_short,
                rsi_direct_band=rsi_direct_short,
            )
        else:
            call_ind = _indicator_pack_from_series(
                call_ltp_series,
                call_vol_series,
                score_threshold,
                max_cross,
                rsi_min,
                rsi_max,
                vol_min,
                include_ema_crossover_in_score=inc_cross,
                strict_bullish_comparisons=strict_bull,
                include_volume_in_score=inc_vol_score,
                require_rsi_for_eligible=req_rsi_eligible,
            )
            put_ind = _indicator_pack_from_series(
                put_ltp_series,
                put_vol_series,
                score_threshold,
                max_cross,
                rsi_min,
                rsi_max,
                vol_min,
                include_ema_crossover_in_score=inc_cross,
                strict_bullish_comparisons=strict_bull,
                include_volume_in_score=inc_vol_score,
                require_rsi_for_eligible=req_rsi_eligible,
            )
        # Fallback when history is thin or indicators collapsed to zeros — not when EMA≈VWAP on a flat premium (that is valid).
        c_z = (
            float(call_ind.get("ema9") or 0) == 0.0
            and float(call_ind.get("ema21") or 0) == 0.0
            and float(call_ind.get("vwap") or 0) == 0.0
        )
        if len(call_ltp_series) < 5 or c_z:
            if short_premium_legs:
                call_ind = _indicator_pack_from_quote_fallback_bearish(
                    call_q,
                    call_ltp,
                    call_vol,
                    score_threshold,
                    max_cross,
                    rsi_min,
                    rsi_max,
                    vol_min,
                    include_volume_in_score=inc_vol_bear,
                    include_ema_crossover_in_score=inc_cross_bear,
                    leg_score_mode=leg_mode_short or "legacy",
                    rsi_below_for_weak=rsi_below_short,
                    rsi_direct_band=rsi_direct_short,
                )
            else:
                call_ind = _indicator_pack_from_quote_fallback(
                    call_q,
                    call_ltp,
                    call_vol,
                    score_threshold,
                    max_cross,
                    rsi_min,
                    rsi_max,
                    vol_min,
                    include_ema_crossover_in_score=inc_cross,
                    strict_bullish_comparisons=strict_bull,
                    include_volume_in_score=inc_vol_score,
                    require_rsi_for_eligible=req_rsi_eligible,
                )
        p_z = (
            float(put_ind.get("ema9") or 0) == 0.0
            and float(put_ind.get("ema21") or 0) == 0.0
            and float(put_ind.get("vwap") or 0) == 0.0
        )
        if len(put_ltp_series) < 5 or p_z:
            if short_premium_legs:
                put_ind = _indicator_pack_from_quote_fallback_bearish(
                    put_q,
                    put_ltp,
                    put_vol,
                    score_threshold,
                    max_cross,
                    rsi_min,
                    rsi_max,
                    vol_min,
                    include_volume_in_score=inc_vol_bear,
                    include_ema_crossover_in_score=inc_cross_bear,
                    leg_score_mode=leg_mode_short or "legacy",
                    rsi_below_for_weak=rsi_below_short,
                    rsi_direct_band=rsi_direct_short,
                )
            else:
                put_ind = _indicator_pack_from_quote_fallback(
                    put_q,
                    put_ltp,
                    put_vol,
                    score_threshold,
                    max_cross,
                    rsi_min,
                    rsi_max,
                    vol_min,
                    include_ema_crossover_in_score=inc_cross,
                    strict_bullish_comparisons=strict_bull,
                    include_volume_in_score=inc_vol_score,
                    require_rsi_for_eligible=req_rsi_eligible,
                )

        regime_sell_pe = False
        regime_sell_ce = False
        if short_premium_legs and reg_srm == "ema_cross_vwap":
            mxi = _max_candles_since_cross_int(max_cross, 5)
            regime_sell_pe, regime_sell_ce = _resolve_regime_sell_pe_ce_at_strike(
                put_ltp_series,
                put_vol_series,
                call_ltp_series,
                call_vol_series,
                mxi,
            )

        call_oi_chg = round(((call_oi - prev_call_oi) / prev_call_oi) * 100, 2) if prev_call_oi else 0.0
        put_oi_chg = round(((put_oi - prev_put_oi) / prev_put_oi) * 100, 2) if prev_put_oi else 0.0
        call_vol_chg = round(((call_vol - prev_call_vol) / prev_call_vol) * 100, 2) if prev_call_vol else 0.0
        put_vol_chg = round(((put_vol - prev_put_vol) / prev_put_vol) * 100, 2) if prev_put_vol else 0.0

        c_delta, c_theta, c_iv = compute_greeks(spot, strike, expiry_date, call_ltp, "CE")
        p_delta, p_theta, p_iv = compute_greeks(spot, strike, expiry_date, put_ltp, "PE")

        current_snapshot[strike] = {
            "call_oi": call_oi,
            "put_oi": put_oi,
            "call_ltp": call_ltp,
            "put_ltp": put_ltp,
            "call_vol": call_vol,
            "put_vol": put_vol,
        }

        call_inst = by_key.get((strike, "CE"), {})
        put_inst = by_key.get((strike, "PE"), {})
        strike_pcr_row = round((put_oi / call_oi), 2) if call_oi else 0.0
        call_tech = int(call_ind.get("technicalScore", call_ind["score"]))
        put_tech = int(put_ind.get("technicalScore", put_ind["score"]))
        chain.append(
            {
                "strike": strike,
                "call": {
                    "tradingsymbol": str(call_inst.get("tradingsymbol", "")),
                    "buildup": _buildup(prev_call_oi, call_oi, prev_call_ltp, call_ltp),
                    "oiChgPct": call_oi_chg,
                    "volChgPct": call_vol_chg,
                    "theta": c_theta,
                    "delta": c_delta,
                    "iv": c_iv,
                    "volume": str(int(call_vol)),
                    "oi": str(int(call_oi)),
                    "ltpChg": _ltp_change_pct(call_ltp, call_prev_close),
                    "ltp": round(call_ltp, 2),
                    "ema9": call_ind["ema9"],
                    "ema21": call_ind["ema21"],
                    "rsi": call_ind["rsi"],
                    "vwap": call_ind["vwap"],
                    "avgVolume": call_ind["avgVolume"],
                    "volumeSpikeRatio": call_ind["volumeSpikeRatio"],
                    "technicalScore": call_tech,
                    "scoreBonusSkew": 0,
                    "scoreBonusPcr": 0,
                    "score": call_ind["score"],
                    "strikePcr": strike_pcr_row,
                    "primaryOk": call_ind["primaryOk"],
                    "emaOk": call_ind["emaOk"],
                    "emaCrossoverOk": call_ind["emaCrossoverOk"],
                    "rsiOk": call_ind["rsiOk"],
                    "volumeOk": call_ind["volumeOk"],
                    "signalEligible": call_ind["signalEligible"],
                    "regimeSellCe": regime_sell_ce,
                },
                "put": {
                    "tradingsymbol": str(put_inst.get("tradingsymbol", "")),
                    "pcr": strike_pcr_row,
                    "ltp": round(put_ltp, 2),
                    "ltpChg": _ltp_change_pct(put_ltp, put_prev_close),
                    "oi": str(int(put_oi)),
                    "oiChgPct": put_oi_chg,
                    "volChgPct": put_vol_chg,
                    "volume": str(int(put_vol)),
                    "iv": p_iv,
                    "delta": p_delta,
                    "theta": p_theta,
                    "buildup": _buildup(prev_put_oi, put_oi, prev_put_ltp, put_ltp),
                    "ema9": put_ind["ema9"],
                    "ema21": put_ind["ema21"],
                    "rsi": put_ind["rsi"],
                    "vwap": put_ind["vwap"],
                    "avgVolume": put_ind["avgVolume"],
                    "volumeSpikeRatio": put_ind["volumeSpikeRatio"],
                    "technicalScore": put_tech,
                    "scoreBonusSkew": 0,
                    "scoreBonusPcr": 0,
                    "score": put_ind["score"],
                    "strikePcr": strike_pcr_row,
                    "primaryOk": put_ind["primaryOk"],
                    "emaOk": put_ind["emaOk"],
                    "emaCrossoverOk": put_ind["emaCrossoverOk"],
                    "rsiOk": put_ind["rsiOk"],
                    "volumeOk": put_ind["volumeOk"],
                    "signalEligible": put_ind["signalEligible"],
                    "regimeSellPe": regime_sell_pe,
                },
            }
        )

    if not chain:
        raise ValueError("No option chain quotes returned from broker.")

    bucket = _RECENT_FETCHES.setdefault(prev_key, deque(maxlen=_window_size()))
    bucket.append(current_snapshot)
    return chain


def _add_ivr_to_chain(chain: list[dict[str, Any]]) -> None:
    """Add ivr (IV Rank proxy) to each call/put leg. IVR = percentile of IV within chain (0-100). Low IV -> low ivr."""
    all_ivs: list[float] = []
    for row in chain:
        for leg_key in ("call", "put"):
            leg = row.get(leg_key) or {}
            iv = float(leg.get("iv") or 0.0)
            if iv > 0:
                all_ivs.append(iv)
    if not all_ivs:
        return
    min_iv = min(all_ivs)
    max_iv = max(all_ivs)
    iv_range = max_iv - min_iv
    if iv_range < 1e-6:
        iv_range = 1e-6
    for row in chain:
        for leg_key in ("call", "put"):
            leg = row.get(leg_key) or {}
            if leg:
                iv = float(leg.get("iv") or 0.0)
                ivr = round((iv - min_iv) / iv_range * 100, 2) if iv > 0 else 50.0
                leg["ivr"] = ivr


def _apply_short_premium_skew_pcr_leg_scores(chain: list[dict[str, Any]], ip: dict[str, Any]) -> None:
    """For ``shortPremiumLegScoreMode`` = ``three_factor``: add skew (CE vs PE IVR) and strike PCR bonuses to leg scores."""
    mode = str(ip.get("shortPremiumLegScoreMode") or "").strip().lower()
    if mode != "three_factor":
        return
    try:
        leg_cap = int(ip.get("scoreMaxLeg") or 5)
    except (TypeError, ValueError):
        leg_cap = 5
    leg_cap = max(3, min(10, leg_cap))
    try:
        skew_min = float(ip.get("shortPremiumIvrSkewMin", 5) or 0)
    except (TypeError, ValueError):
        skew_min = 5.0
    vs_raw = ip.get("shortPremiumPcrBonusVsChain", True)
    if isinstance(vs_raw, str):
        vs_chain = vs_raw.strip().lower() in {"1", "true", "yes"}
    else:
        vs_chain = bool(vs_raw)
    try:
        eps = float(ip.get("shortPremiumPcrChainEpsilon", 0) or 0)
    except (TypeError, ValueError):
        eps = 0.0
    pcr_min_ce = ip.get("shortPremiumPcrMinForSellCe")
    pcr_max_pe = ip.get("shortPremiumPcrMaxForSellPe")

    def _oi_f(leg: dict[str, Any]) -> float:
        raw = leg.get("oi")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    total_call_oi = sum(_oi_f(x.get("call") or {}) for x in chain)
    total_put_oi = sum(_oi_f(x.get("put") or {}) for x in chain)
    chain_pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else 0.0

    for row in chain:
        call = row.get("call") or {}
        put = row.get("put") or {}
        if not call or not put:
            continue
        coi = _oi_f(call)
        poi = _oi_f(put)
        strike_pcr = (poi / coi) if coi > 0 else None
        if strike_pcr is not None:
            sp = round(strike_pcr, 2)
            call["strikePcr"] = sp
            put["strikePcr"] = sp

        ce_ivr = float(call.get("ivr") or 0)
        pe_ivr = float(put.get("ivr") or 0)
        skew_ce = 1 if (ce_ivr - pe_ivr) >= skew_min else 0
        skew_pe = 1 if (pe_ivr - ce_ivr) >= skew_min else 0

        pcr_ce = 0
        pcr_pe = 0
        if vs_chain and strike_pcr is not None and coi > 0:
            pcr_ce = 1 if strike_pcr > chain_pcr + eps else 0
            pcr_pe = 1 if strike_pcr < chain_pcr - eps else 0
        elif strike_pcr is not None and coi > 0:
            if pcr_min_ce is not None:
                try:
                    pcr_ce = 1 if strike_pcr >= float(pcr_min_ce) else 0
                except (TypeError, ValueError):
                    pcr_ce = 0
            if pcr_max_pe is not None:
                try:
                    pcr_pe = 1 if strike_pcr <= float(pcr_max_pe) else 0
                except (TypeError, ValueError):
                    pcr_pe = 0
            if pcr_min_ce is None and pcr_max_pe is None:
                pcr_ce = 1 if strike_pcr > chain_pcr + eps else 0
                pcr_pe = 1 if strike_pcr < chain_pcr - eps else 0

        for leg_key, skew_b, pcr_b in (("call", skew_ce, pcr_ce), ("put", skew_pe, pcr_pe)):
            leg = row.get(leg_key) or {}
            if not leg:
                continue
            try:
                tech = int(leg.get("technicalScore", leg.get("score", 0)))
            except (TypeError, ValueError):
                tech = 0
            leg["technicalScore"] = tech
            leg["scoreBonusSkew"] = skew_b
            leg["scoreBonusPcr"] = pcr_b
            leg["score"] = min(leg_cap, tech + skew_b + pcr_b)


def fetch_option_chain_sync(
    kite: KiteConnect | None,
    instrument: str,
    expiry_str: str,
    strikes_up: int = 10,
    strikes_down: int = 10,
    score_threshold: int = 3,
    indicator_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instrument = instrument.strip().upper()
    expiry_date = _parse_expiry_frontend(expiry_str)
    spot, spot_chg = _resolve_spot(instrument, kite)
    require_live = os.getenv("OPTION_CHAIN_REQUIRE_LIVE", "1").strip().lower() not in {"0", "false", "no"}
    if kite is None and require_live:
        raise RuntimeError("Live Zerodha connection is required for option chain data.")

    ip = dict(indicator_params or {})
    ip.setdefault("spotScoreThreshold", score_threshold)
    short_pm = str(ip.get("positionIntent", "")).lower() == "short_premium"
    reg_mode_spot = str(ip.get("spotRegimeMode") or ip.get("spot_regime_mode") or "").strip().lower()
    long_spot_align = bool(ip.get("longPremiumSpotAlign"))

    spot_candles: list[dict[str, Any]] = []
    if kite and (
        (short_pm and reg_mode_spot != "ema_cross_vwap")
        or (ip.get("adx_min_threshold") is not None and float(ip.get("adx_min_threshold") or 0) > 0)
        or (long_spot_align and not short_pm)
    ):
        spot_candles = _fetch_spot_candles(kite, instrument)

    if kite is None:
        chain = _build_synthetic_chain(instrument, expiry_date, spot, strikes_up, strikes_down)
    else:
        chain = _build_live_chain(
            kite, instrument, expiry_date, spot, strikes_up, strikes_down,
            score_threshold, ip,
        )
    _add_ivr_to_chain(chain)
    if short_pm:
        _apply_short_premium_skew_pcr_leg_scores(chain, ip)

    adx_val: float | None = None
    adx_min = ip.get("adx_min_threshold")
    if spot_candles and adx_min is not None and float(adx_min) > 0:
        adx_period = int(ip.get("adx_period", 14))
        adx_val = _adx_from_candles(spot_candles, adx_period)
        if adx_val < float(adx_min):
            for row in chain:
                for leg_key in ("call", "put"):
                    leg = row.get(leg_key)
                    if leg:
                        leg["signalEligible"] = False

    spot_trend: dict[str, Any] = {"spotBullishScore": 0, "spotBearishScore": 0, "spotRegime": None}
    if spot_candles and short_pm and reg_mode_spot != "ema_cross_vwap":
        spot_trend = _spot_trend_payload_from_candles(spot_candles, ip, int(score_threshold))
    elif spot_candles and long_spot_align and not short_pm:
        spot_trend = _spot_trend_payload_from_candles(spot_candles, ip, int(score_threshold))

    total_call_oi = sum(float(x["call"]["oi"]) for x in chain)
    total_put_oi = sum(float(x["put"]["oi"]) for x in chain)
    total_call_vol = sum(float(x["call"]["volume"]) for x in chain)
    total_put_vol = sum(float(x["put"]["volume"]) for x in chain)
    pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0.0

    out: dict[str, Any] = {
        "spot": round(spot, 2),
        "spotChgPct": round(spot_chg, 2),
        "vix": _vix_from_quote(kite) if kite else round(12.5 + abs(math.sin(spot / 1000) * 4.4), 2),
        "synFuture": round(spot + 4.5, 2),
        "pcr": pcr,
        "pcrVol": round(total_put_vol / total_call_vol, 2) if total_call_vol else 0.0,
        "updated": datetime.utcnow().isoformat() + "Z",
        "chain": chain,
        "from_cache": False,
        "using_live_broker": bool(kite),
        "window_size": _window_size(),
        **spot_trend,
    }
    if adx_val is not None:
        out["adx"] = adx_val
        out["adxOk"] = adx_val >= float(adx_min or 0)
    return out
