"""PS/VS MTF: 3m Bank Nifty spot RSI stack + 15m resampled permission/filters; long ATM (see master spec)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.option_chain_zerodha import (
    _parse_candle_time_ist,
    _true_range_series,
    _wilder_smooth_list,
    adx_series_from_candles,
)
from zoneinfo import ZoneInfo

from app.strategies.stochastic_bnf import _ema_series, _rsi_wilder_series

_IST = ZoneInfo("Asia/Kolkata")


def resolve_ps_vs_mtf_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = raw if isinstance(raw, dict) else {}
    return {
        "rsiPeriod": int(r.get("rsiPeriod", 9) or 9),
        "psEmaPeriod": int(r.get("psEmaPeriod", 9) or 9),
        "vsWmaPeriod": int(r.get("vsWmaPeriod", 21) or 21),
        "atrPeriod": int(r.get("atrPeriod", 14) or 14),
        "adxPeriod": int(r.get("adxPeriod", 14) or 14),
        "adxMin": float(r.get("adxMin", 10.0) or 10.0),
        "adxRef": float(r.get("adxRef", 30.0) or 30.0),
        "atrRangeMin": float(r.get("atrRangeMin", 0.5) or 0.5),
        "atrRangeMax": float(r.get("atrRangeMax", 2.5) or 2.5),
        "rsiBandLow": float(r.get("rsiBandLow", 40.0) or 40.0),
        "rsiBandHigh": float(r.get("rsiBandHigh", 70.0) or 70.0),
        "minConvictionPct": float(r.get("minConvictionPct", 80.0) or 80.0),
        "volumeVsPriorMult": float(r.get("volumeVsPriorMult", 1.10) or 1.10),
        "strict15m": bool(r.get("strict15m", True)),
        "candleDaysBack": int(r.get("candleDaysBack", 8) or 8),
        "sessionStart": str(r.get("sessionStart", "09:15") or "09:15"),
        "sessionEnd": str(r.get("sessionEnd", "15:15") or "15:15"),
        "wVolume": int(r.get("wVolume", 29) or 29),
        "wPsVs": int(r.get("wPsVs", 33) or 33),
        "wRsi": int(r.get("wRsi", 19) or 19),
        "wAlign": int(r.get("wAlign", 14) or 14),
        "wAdx": int(r.get("wAdx", 5) or 5),
        "psVsScale": float(r.get("psVsScale", 15.0) or 15.0),
    }


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = str(s).strip().split(":")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 9, 15


def _session_ok(now_ist: datetime, cfg: dict[str, Any]) -> bool:
    sh, sm = _parse_hhmm(str(cfg.get("sessionStart", "09:15")))
    eh, em_ = _parse_hhmm(str(cfg.get("sessionEnd", "15:15")))
    t = now_ist.time()
    start = now_ist.replace(hour=sh, minute=sm, second=0, microsecond=0).time()
    end = now_ist.replace(hour=eh, minute=em_, second=59, microsecond=0).time()
    return start <= t <= end


def _wma_series(vals: list[float], period: int) -> list[float]:
    p = max(1, int(period))
    n = len(vals)
    out = [50.0] * n
    if n == 0:
        return out
    denom = p * (p + 1) / 2.0
    for i in range(n):
        if i < p - 1:
            continue
        window = vals[i - p + 1 : i + 1]
        weights = [float(j) for j in range(1, p + 1)]
        out[i] = sum(w * x for w, x in zip(weights, window)) / denom
    return out


def _bucket_key_15m_ist(dt: datetime | None) -> tuple[int, int, int, int] | None:
    """(year, month, day, bucket_index) where bucket_index = floor(minutes_from_midnight/15)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return None
    m = dt.hour * 60 + dt.minute
    b = m // 15
    return (dt.year, dt.month, dt.day, b)


def resample_3m_to_15m(candles_3m: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate 3m OHLCV to 15m bars (IST bucket). Single pass; no extra IO."""
    from collections import OrderedDict

    buckets: OrderedDict[tuple[int, int, int, int], list[dict[str, Any]]] = OrderedDict()
    for c in candles_3m:
        dt = _parse_candle_time_ist(c)
        bk = _bucket_key_15m_ist(dt)
        if bk is None:
            continue
        buckets.setdefault(bk, []).append(c)
    out: list[dict[str, Any]] = []
    for _bk, group in buckets.items():
        group.sort(key=lambda x: str(x.get("time") or ""))
        o = float(group[0].get("open") or 0)
        h = max(float(x.get("high") or 0) for x in group)
        lows = [float(x.get("low") or 0) for x in group]
        l_ = min(lows) if lows else 0.0
        cl = float(group[-1].get("close") or 0)
        v = sum(float(x.get("volume") or 0) for x in group)
        t = str(group[-1].get("time") or "")
        out.append({"open": o, "high": h, "low": l_, "close": cl, "volume": v, "time": t})
    return out


def _compute_ps_vs(
    closes: list[float],
    cfg: dict[str, Any],
) -> tuple[list[float], list[float], list[float]]:
    rp = max(2, int(cfg.get("rsiPeriod", 9)))
    rsi = _rsi_wilder_series(closes, rp)
    ps_p = max(1, int(cfg.get("psEmaPeriod", 9)))
    vs_p = max(1, int(cfg.get("vsWmaPeriod", 21)))
    ps = _ema_series(rsi, ps_p)
    vs = _wma_series(rsi, vs_p)
    return rsi, ps, vs


def _atr_wilder_last(candles: list[dict[str, Any]], period: int) -> float:
    if len(candles) < period + 1:
        return 0.0
    tr = _true_range_series(candles)
    atr_s = _wilder_smooth_list(tr, period)
    return float(atr_s[-1]) if atr_s else 0.0


def _conviction(
    *,
    v_last: float,
    v_prev: float,
    vol_mult: float,
    ps3: float,
    vs3: float,
    rsi15: float,
    align_ok: bool,
    adx15: float,
    adx_min: float,
    adx_ref: float,
    rsi_low: float,
    rsi_high: float,
    cfg: dict[str, Any],
) -> float:
    wv = int(cfg.get("wVolume", 29))
    wp = int(cfg.get("wPsVs", 33))
    wr = int(cfg.get("wRsi", 19))
    wa = int(cfg.get("wAlign", 14))
    wd = int(cfg.get("wAdx", 5))
    scale = float(cfg.get("psVsScale", 15.0))

    if v_prev > 0 and v_last >= vol_mult * v_prev:
        s_vol = 100.0
    elif v_prev > 0:
        s_vol = min(100.0, 100.0 * v_last / (vol_mult * v_prev))
    else:
        s_vol = 0.0

    d = abs(ps3 - vs3)
    s_psvs = min(100.0, 25.0 + 75.0 * min(1.0, d / max(scale, 1e-6)))

    if rsi15 < rsi_low or rsi15 > rsi_high:
        s_rsi = 0.0
    else:
        s_rsi = max(0.0, min(100.0, 100.0 - abs(rsi15 - 55.0) / 15.0 * 100.0))

    s_align = 100.0 if align_ok else 0.0

    if adx15 < adx_min:
        s_adx = 0.0
    else:
        den = max(adx_ref - adx_min, 1e-6)
        s_adx = min(100.0, 100.0 * min(1.0, (adx15 - adx_min) / den))

    return (wv * s_vol + wp * s_psvs + wr * s_rsi + wa * s_align + wd * s_adx) / 100.0


def evaluate_ps_vs_mtf_signal(
    candles_3m: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    now_ist: datetime | None = None,
) -> dict[str, Any]:
    """
    Single-pass evaluation on pre-fetched 3m candles. Derives 15m in memory.
    See docs/strategies/PS_VS_15M_3M_MASTER_SPEC.md.
    """
    now = now_ist or datetime.now(_IST)  # type: ignore[arg-type]
    if not _session_ok(now, cfg):
        return {"ok": False, "reason": "outside_session", "direction": None, "conviction": 0.0}

    need_3m = max(80, int(cfg.get("vsWmaPeriod", 21)) + 40)
    if not candles_3m or len(candles_3m) < need_3m:
        return {"ok": False, "reason": "insufficient_3m", "direction": None, "conviction": 0.0}

    c15 = resample_3m_to_15m(candles_3m)
    adx_p = int(cfg.get("adxPeriod", 14))
    atr_p = int(cfg.get("atrPeriod", 14))
    if len(c15) < max(adx_p + 2, atr_p + 2, 3):
        if cfg.get("strict15m"):
            return {"ok": False, "reason": "insufficient_15m", "direction": None, "conviction": 0.0}

    closes3 = [float(c.get("close") or 0) for c in candles_3m]
    rsi3, ps3, vs3 = _compute_ps_vs(closes3, cfg)
    closes15 = [float(c.get("close") or 0) for c in c15]
    rsi15, ps15, vs15 = _compute_ps_vs(closes15, cfg)

    i3 = len(candles_3m) - 1
    j15 = len(c15) - 1
    if i3 < 2 or j15 < 1:
        return {"ok": False, "reason": "insufficient_bars", "direction": None, "conviction": 0.0}

    ps15_l = ps15[j15]
    vs15_l = vs15[j15]
    rsi15_l = rsi15[j15]
    if ps15_l != ps15_l or vs15_l != vs15_l:
        return {"ok": False, "reason": "nan_15m", "direction": None, "conviction": 0.0}

    adx_series = adx_series_from_candles(c15, adx_p)
    adx_l = float(adx_series[j15]) if j15 < len(adx_series) else 0.0
    atr_l = _atr_wilder_last(c15, atr_p)
    rng = float(c15[j15].get("high", 0)) - float(c15[j15].get("low", 0))
    r_atr = rng / atr_l if atr_l > 1e-9 else 0.0
    v_last = float(c15[j15].get("volume") or 0)
    v_prev = float(c15[j15 - 1].get("volume") or 0)
    vol_mult = float(cfg.get("volumeVsPriorMult", 1.10))

    rl = float(cfg.get("rsiBandLow", 40))
    rh = float(cfg.get("rsiBandHigh", 70))
    adx_min = float(cfg.get("adxMin", 10))
    rmin = float(cfg.get("atrRangeMin", 0.5))
    rmax = float(cfg.get("atrRangeMax", 2.5))

    def gates_ok(ce_direction: bool) -> tuple[bool, str]:
        if ce_direction:
            if ps15_l < vs15_l:
                return False, "15m_bias_ce"
        else:
            if ps15_l > vs15_l:
                return False, "15m_bias_pe"
        if not (rl <= rsi15_l <= rh):
            return False, "rsi15_band"
        if adx_l < adx_min:
            return False, "adx15"
        if not (rmin <= r_atr <= rmax):
            return False, "atr_range"
        if v_prev <= 0 or v_last < vol_mult * v_prev:
            return False, "volume15"
        return True, "ok"

    ps_a, ps_b, ps_c = ps3[i3 - 2], ps3[i3 - 1], ps3[i3]
    vs_a, vs_b, vs_c = vs3[i3 - 2], vs3[i3 - 1], vs3[i3]

    cross_ce = ps_b < vs_b and ps_c >= vs_c
    cross_pe = ps_b > vs_b and ps_c <= vs_c
    dip_rec_ce = ps_a >= vs_a and ps_b < vs_b and ps_c >= vs_c
    dip_rec_pe = ps_a <= vs_a and ps_b > vs_b and ps_c <= vs_c

    min_c = float(cfg.get("minConvictionPct", 80))
    adx_ref = float(cfg.get("adxRef", 30))

    direction: str | None = None
    conv_final = 0.0

    if cross_ce or dip_rec_ce:
        ok_g, _ = gates_ok(True)
        if ok_g:
            conv_final = _conviction(
                v_last=v_last,
                v_prev=v_prev,
                vol_mult=vol_mult,
                ps3=ps_c,
                vs3=vs_c,
                rsi15=rsi15_l,
                align_ok=ps15_l >= vs15_l,
                adx15=adx_l,
                adx_min=adx_min,
                adx_ref=adx_ref,
                rsi_low=rl,
                rsi_high=rh,
                cfg=cfg,
            )
            if conv_final >= min_c:
                direction = "bull"

    if direction is None and (cross_pe or dip_rec_pe):
        ok_g, _ = gates_ok(False)
        if ok_g:
            conv_final = _conviction(
                v_last=v_last,
                v_prev=v_prev,
                vol_mult=vol_mult,
                ps3=ps_c,
                vs3=vs_c,
                rsi15=rsi15_l,
                align_ok=ps15_l <= vs15_l,
                adx15=adx_l,
                adx_min=adx_min,
                adx_ref=adx_ref,
                rsi_low=rl,
                rsi_high=rh,
                cfg=cfg,
            )
            if conv_final >= min_c:
                direction = "bear"

    base_metrics = {
        "ps3": round(ps_c, 3),
        "vs3": round(vs_c, 3),
        "rsi15": round(rsi15_l, 2),
        "rsi3": round(rsi3[i3], 2),
        "adx15": round(adx_l, 2),
        "r_atr": round(r_atr, 3),
        "ps15": round(ps15_l, 3),
        "vs15": round(vs15_l, 3),
    }

    if direction is None:
        return {
            "ok": False,
            "reason": "no_signal_or_gates",
            "direction": None,
            "conviction": 0.0,
            "metrics": base_metrics,
        }

    return {
        "ok": True,
        "reason": "signal",
        "direction": direction,
        "conviction": round(conv_final, 2),
        "metrics": base_metrics,
    }


def compute_ps_vs_mtf_observability_series(
    candles: list[dict[str, Any]],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Aligned 3m + resampled 15m series for Observability charts (same data path as signal engine)."""
    if not candles or len(candles) < 30:
        return {"ok": False, "reason": "insufficient_candles"}
    closes = [float(c.get("close") or 0) for c in candles]
    rsi3, ps3, vs3 = _compute_ps_vs(closes, cfg)
    times: list[int] = []
    for c in candles:
        dti = _parse_candle_time_ist(c)
        times.append(int(dti.timestamp()) if dti else 0)

    c15 = resample_3m_to_15m(candles)
    times15: list[int] = []
    rsi15: list[float] = []
    ps15: list[float] = []
    vs15: list[float] = []
    adx15: list[float] = []
    if len(c15) >= 2:
        closes15 = [float(c.get("close") or 0) for c in c15]
        rsi15, ps15, vs15 = _compute_ps_vs(closes15, cfg)
        for c in c15:
            dti = _parse_candle_time_ist(c)
            times15.append(int(dti.timestamp()) if dti else 0)
        adx_p = int(cfg.get("adxPeriod", 14) or 14)
        adx15 = adx_series_from_candles(c15, adx_p)

    return {
        "ok": True,
        "times": times,
        "open": [float(c.get("open") or 0) for c in candles],
        "high": [float(c.get("high") or 0) for c in candles],
        "low": [float(c.get("low") or 0) for c in candles],
        "close": closes,
        "rsi": rsi3,
        "ps": ps3,
        "vs": vs3,
        "times15": times15,
        "rsi15": rsi15,
        "ps15": ps15,
        "vs15": vs15,
        "adx15": adx15,
    }
