"""StochasticBNF: Bank Nifty spot EMA5/15/50 + ADX + Stochastic RSI; short ATM (2 trading-DTE Tuesday series)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.option_chain_zerodha import (
    _adx_from_candles,
    _parse_candle_time_ist,
    adx_series_from_candles,
    running_typical_price_average_series,
)
from app.strategies.supertrend_trail import map_settings_timeframe_to_kite_interval

_IST = ZoneInfo("Asia/Kolkata")


def resolve_stochastic_bnf_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = raw if isinstance(raw, dict) else {}
    return {
        "emaFast": 5,
        "emaMid": 15,
        "emaSlow": 50,
        "adxPeriod": int(r.get("adxPeriod", 14) or 14),
        "adxThreshold": float(r.get("adxThreshold", 20) or 20),
        "rsiLength": int(r.get("rsiLength", 14) or 14),
        "stochLength": int(r.get("stochLength", 14) or 14),
        "stochK": int(r.get("stochK", 3) or 3),
        "stochD": int(r.get("stochD", 3) or 3),
        "overbought": float(r.get("overbought", 70) or 70),
        "oversold": float(r.get("oversold", 30) or 30),
        "candleDaysBack": int(r.get("candleDaysBack", 8) or 8),
        "usePullbackEntry": bool(r.get("usePullbackEntry", False)),
        "stochConfirmation": bool(r.get("stochConfirmation", True)),
        "vwapFilter": bool(r.get("vwapFilter", True)),
        "timeFilter": bool(r.get("timeFilter", False)),
        "timeFilterStart": str(r.get("timeFilterStart", "09:30") or "09:30"),
        "timeFilterEnd": str(r.get("timeFilterEnd", "14:30") or "14:30"),
        "exitTimeIst": str(r.get("exitTimeIst", "15:15") or "15:15"),
    }


def _ema_series(closes: list[float], period: int) -> list[float]:
    p = max(1, int(period))
    if not closes:
        return []
    k = 2.0 / (p + 1)
    ema_v = closes[0]
    out = [ema_v]
    for c in closes[1:]:
        ema_v = c * k + ema_v * (1.0 - k)
        out.append(ema_v)
    return out


def _rsi_wilder_series(closes: list[float], period: int = 14) -> list[float]:
    n = len(closes)
    out = [50.0] * n
    if n < period + 1:
        return out
    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        ch = closes[i] - closes[i - 1]
        gains[i] = max(ch, 0.0)
        losses[i] = max(-ch, 0.0)
    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period
    if avg_loss < 1e-12:
        rs = 100.0
    else:
        rs = avg_gain / avg_loss
    out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss < 1e-12:
            rs = 100.0
        else:
            rs = avg_gain / avg_loss
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _sma_series(vals: list[float], period: int) -> list[float]:
    p = max(1, int(period))
    n = len(vals)
    out = [50.0] * n
    for i in range(n):
        lo = max(0, i - p + 1)
        window = vals[lo : i + 1]
        out[i] = sum(window) / len(window)
    return out


def _stoch_rsi_kd(
    rsi: list[float],
    stoch_len: int,
    k_sm: int,
    d_sm: int,
) -> tuple[list[float], list[float]]:
    n = len(rsi)
    raw = [50.0] * n
    sl = max(1, int(stoch_len))
    for i in range(n):
        lo = max(0, i - sl + 1)
        w = rsi[lo : i + 1]
        hi = max(w)
        lo_v = min(w)
        if hi - lo_v < 1e-9:
            raw[i] = 50.0
        else:
            raw[i] = 100.0 * (rsi[i] - lo_v) / (hi - lo_v)
    k = _sma_series(raw, k_sm)
    d = _sma_series(k, d_sm)
    return k, d


def _in_ema5_15_band(close: float, ema5: float, ema15: float) -> bool:
    lo = min(ema5, ema15)
    hi = max(ema5, ema15)
    return lo - 1e-9 <= close <= hi + 1e-9


def _bullish_candle(c: dict[str, Any]) -> bool:
    o = float(c.get("open") or 0)
    cl = float(c.get("close") or 0)
    return cl > o


def _bearish_candle(c: dict[str, Any]) -> bool:
    o = float(c.get("open") or 0)
    cl = float(c.get("close") or 0)
    return cl < o


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = str(s).strip().split(":")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 9, 30


def _time_filter_ok(now_ist: datetime, cfg: dict[str, Any]) -> bool:
    if not bool(cfg.get("timeFilter")):
        return True
    sh, sm = _parse_hhmm(str(cfg.get("timeFilterStart", "09:30")))
    eh, em_ = _parse_hhmm(str(cfg.get("timeFilterEnd", "14:30")))
    t = now_ist.time()
    start = datetime.now(_IST).replace(hour=sh, minute=sm, second=0, microsecond=0).time()
    end = datetime.now(_IST).replace(hour=eh, minute=em_, second=0, microsecond=0).time()
    return start <= t <= end


def session_vwap_from_ohlcv(candles: list[dict[str, Any]]) -> float | None:
    """Typical-price volume-weighted average for session bars (Bank Nifty spot)."""
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


def evaluate_stochastic_bnf_signal(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    now_ist: datetime | None = None,
) -> dict[str, Any]:
    """
    Evaluate the **last closed** bar (index -1) for StochasticBNF entry.
    Compare spot **last price** to VWAP using the last bar's close as LTP proxy.
    """
    rlen = int(cfg.get("rsiLength", 14) or 14)
    slen = int(cfg.get("stochLength", 14) or 14)
    k_sm = int(cfg.get("stochK", 3) or 3)
    d_sm = int(cfg.get("stochD", 3) or 3)
    need = max(60, rlen + slen + k_sm + d_sm + 55)
    if not candles or len(candles) < need:
        return {"ok": False, "reason": f"need>={need}_candles", "direction": None}

    now = now_ist or datetime.now(_IST)
    if not _time_filter_ok(now, cfg):
        return {"ok": False, "reason": "outside_time_filter", "direction": None}

    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]

    ema5 = _ema_series(closes, 5)
    ema15 = _ema_series(closes, 15)
    ema50 = _ema_series(closes, 50)
    rsi = _rsi_wilder_series(closes, rlen)
    k_line, d_line = _stoch_rsi_kd(rsi, slen, k_sm, d_sm)

    i = len(closes) - 1
    j = i - 1
    if j < 1:
        return {"ok": False, "reason": "insufficient_history", "direction": None}

    adx_val = _adx_from_candles(candles, int(cfg.get("adxPeriod", 14) or 14))
    adx_thr = float(cfg.get("adxThreshold", 20) or 20)
    ob = float(cfg.get("overbought", 70) or 70)
    os_ = float(cfg.get("oversold", 30) or 30)
    stoch_conf = bool(cfg.get("stochConfirmation", True))
    use_pb = bool(cfg.get("usePullbackEntry", False))

    vwap = session_vwap_from_ohlcv(candles)
    ltp = closes[i]
    vwap_on = bool(cfg.get("vwapFilter", True))

    def _stoch_bull_ok(ii: int) -> bool:
        if k_line[ii] <= d_line[ii]:
            return False
        if not stoch_conf:
            return True
        return k_line[ii] >= ob

    def _stoch_bear_ok(ii: int) -> bool:
        if k_line[ii] >= d_line[ii]:
            return False
        if not stoch_conf:
            return True
        return k_line[ii] <= os_

    def _cross_up(ii: int) -> bool:
        if ii < 1:
            return False
        return k_line[ii - 1] <= d_line[ii - 1] and k_line[ii] > d_line[ii]

    def _cross_down(ii: int) -> bool:
        if ii < 1:
            return False
        return k_line[ii - 1] >= d_line[ii - 1] and k_line[ii] < d_line[ii]

    # --- Bullish regime ---
    bull_struct = ema5[i] > ema15[i] > ema50[i] and adx_val > adx_thr
    if bull_struct and _stoch_bull_ok(i):
        if vwap_on and vwap is not None and not (ltp > vwap):
            return {"ok": False, "reason": "vwap_filter_bull", "direction": None}

        if use_pb:
            if not _in_ema5_15_band(closes[j], ema5[j], ema15[j]):
                return {"ok": False, "reason": "pullback_not_in_ema5_15_band", "direction": None}
            if not (_bullish_candle(candles[i]) or _cross_up(i)):
                return {"ok": False, "reason": "pullback_no_bull_confirm", "direction": None}
        else:
            pass

        return {
            "ok": True,
            "reason": "bull_sell_pe" + ("_pullback" if use_pb else "_immediate"),
            "direction": "bull",
            "metrics": {
                "ema5": ema5[i],
                "ema15": ema15[i],
                "ema50": ema50[i],
                "adx": adx_val,
                "stochK": k_line[i],
                "stochD": d_line[i],
                "close": closes[i],
                "vwap": vwap,
            },
        }

    # --- Bearish regime ---
    bear_struct = ema5[i] < ema15[i] < ema50[i] and adx_val > adx_thr
    if bear_struct and _stoch_bear_ok(i):
        if vwap_on and vwap is not None and not (ltp < vwap):
            return {"ok": False, "reason": "vwap_filter_bear", "direction": None}

        if use_pb:
            if not _in_ema5_15_band(closes[j], ema5[j], ema15[j]):
                return {"ok": False, "reason": "pullback_not_in_ema5_15_band_bear", "direction": None}
            if not (_bearish_candle(candles[i]) or _cross_down(i)):
                return {"ok": False, "reason": "pullback_no_bear_confirm", "direction": None}

        return {
            "ok": True,
            "reason": "bear_sell_ce" + ("_pullback" if use_pb else "_immediate"),
            "direction": "bear",
            "metrics": {
                "ema5": ema5[i],
                "ema15": ema15[i],
                "ema50": ema50[i],
                "adx": adx_val,
                "stochK": k_line[i],
                "stochD": d_line[i],
                "close": closes[i],
                "vwap": vwap,
            },
        }

    return {"ok": False, "reason": "no_setup", "direction": None}


def snapshot_stochastic_bnf_ema_exit(
    candles: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Latest closed-bar EMA5/EMA15 for SL (EMA5 vs EMA15 cross)."""
    if not candles or len(candles) < 55:
        return None
    closes = [float(c.get("close") or 0) for c in candles]
    ema5 = _ema_series(closes, 5)
    ema15 = _ema_series(closes, 15)
    i = len(closes) - 1
    return {"ema5": ema5[i], "ema15": ema15[i]}


def should_exit_on_ema5_15_cross(*, option_type: str, ema5: float, ema15: float) -> bool:
    """Short PE: exit when EMA5 < EMA15. Short CE: exit when EMA5 > EMA15."""
    ot = str(option_type or "").upper().strip()
    if ot == "PE":
        return ema5 < ema15
    if ot == "CE":
        return ema5 > ema15
    return False


def parse_exit_time_ist(cfg: dict[str, Any]) -> tuple[int, int]:
    return _parse_hhmm(str(cfg.get("exitTimeIst", "15:15")))


def compute_stochastic_bnf_observability_series(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Aligned indicator series for charts (same candle list as signal evaluation)."""
    if not candles or len(candles) < 20:
        return {"ok": False, "reason": "insufficient_candles"}
    rlen = int(cfg.get("rsiLength", 14) or 14)
    slen = int(cfg.get("stochLength", 14) or 14)
    k_sm = int(cfg.get("stochK", 3) or 3)
    d_sm = int(cfg.get("stochD", 3) or 3)
    adx_p = int(cfg.get("adxPeriod", 14) or 14)

    highs = [float(c.get("high") or 0) for c in candles]
    lows = [float(c.get("low") or 0) for c in candles]
    closes = [float(c.get("close") or 0) for c in candles]
    ema5 = _ema_series(closes, 5)
    ema15 = _ema_series(closes, 15)
    ema50 = _ema_series(closes, 50)
    rsi = _rsi_wilder_series(closes, rlen)
    k_line, d_line = _stoch_rsi_kd(rsi, slen, k_sm, d_sm)
    adx_s = adx_series_from_candles(candles, adx_p)
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
        "ema5": ema5,
        "ema15": ema15,
        "ema50": ema50,
        "stochK": k_line,
        "stochD": d_line,
        "adx": adx_s,
        "vwap": vwap_run,
    }
