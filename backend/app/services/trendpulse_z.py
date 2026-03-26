"""TrendPulse Z — PS_z vs VS_z cross on ST, HTF bias, ADX gate (see docs/strategies/TRENDPULSE_Z_IMPLEMENTATION_PLAN.md)."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.option_chain_zerodha import _adx_from_candles


def _bar_volume_proxy(c: dict[str, Any]) -> float:
    """Index candles often report volume=0; use range-based proxy when needed."""
    v = float(c.get("volume") or 0)
    if v > 0:
        return v
    h = float(c.get("high", 0))
    l_ = float(c.get("low", 0))
    cl = float(c.get("close", 1)) or 1.0
    return max(1.0, (h - l_) / max(cl, 1e-9) * 1e6)


def _ema_series(closes: list[float], period: int) -> list[float]:
    if not closes or period < 1:
        return []
    k = 2 / (period + 1)
    out: list[float] = []
    ema_val = closes[0]
    for v in closes:
        ema_val = (v * k) + (ema_val * (1 - k))
        out.append(ema_val)
    return out


def _raw_ps(closes: list[float], k: int) -> list[float]:
    n = len(closes)
    out = [0.0] * n
    for i in range(n):
        if i < k:
            continue
        prev = closes[i - k]
        cur = closes[i]
        if prev <= 0 or cur <= 0:
            out[i] = 0.0
        else:
            out[i] = math.log(cur / prev)
    return out


def _raw_vs(volumes: list[float], k: int) -> list[float]:
    """Log volume vs trailing mean (exclusive current bar for mean)."""
    n = len(volumes)
    out = [0.0] * n
    for i in range(n):
        if i < k:
            continue
        lo = max(0, i - k)
        trail = volumes[lo:i]
        if not trail:
            continue
        m = statistics.mean(trail)
        vi = max(1e-9, volumes[i])
        denom = max(1e-9, m)
        out[i] = math.log(vi / denom)
    return out


def _rolling_z(values: list[float], window: int) -> list[float]:
    n = len(values)
    out = [0.0] * n
    for i in range(n):
        start = i - window + 1
        if start < 0:
            continue
        w = values[start : i + 1]
        if len(w) < 2:
            out[i] = 0.0
            continue
        mu = statistics.mean(w)
        sd = statistics.stdev(w)
        if sd < 1e-12:
            out[i] = 0.0
        else:
            out[i] = (values[i] - mu) / sd
    return out


def htf_bias_from_closes(
    closes: list[float],
    ema_fast: int = 13,
    ema_slow: int = 34,
) -> str:
    """HTF bias: bullish if close > EMA_slow and EMA_fast > EMA_slow; bearish if mirrored; else neutral."""
    if len(closes) < max(ema_fast, ema_slow) + 2:
        return "neutral"
    ef = _ema_series(closes, ema_fast)
    es = _ema_series(closes, ema_slow)
    if not ef or not es:
        return "neutral"
    c, f, s = closes[-1], ef[-1], es[-1]
    if c > s and f > s:
        return "bullish"
    if c < s and f < s:
        return "bearish"
    return "neutral"


def detect_cross(ps_z: list[float], vs_z: list[float], idx: int) -> str | None:
    """Bullish: PS_z crosses above VS_z at idx. Bearish: PS_z crosses below VS_z. Close-of-bar."""
    if idx < 1:
        return None
    a0, a1 = ps_z[idx - 1], ps_z[idx]
    b0, b1 = vs_z[idx - 1], vs_z[idx]
    if a0 <= b0 and a1 > b1:
        return "bullish"
    if a0 >= b0 and a1 < b1:
        return "bearish"
    return None


@dataclass
class TrendPulseEval:
    ok: bool
    htf_bias: str
    cross: str | None
    ps_z: float
    vs_z: float
    adx_st: float
    reason: str


def evaluate_trendpulse_signal(
    st_candles: list[dict[str, Any]],
    htf_candles: list[dict[str, Any]],
    *,
    z_window: int = 50,
    slope_lookback: int = 4,
    adx_period: int = 14,
    adx_min: float = 18.0,
    htf_ema_fast: int = 13,
    htf_ema_slow: int = 34,
) -> TrendPulseEval:
    """
    ST = signal timeframe candles (e.g. 5m), HTF = higher timeframe (e.g. 15m).
    Requires sufficient history for z-window + slope lookback.
    """
    need = max(z_window, slope_lookback) + slope_lookback + 5
    if len(st_candles) < need:
        return TrendPulseEval(
            False, "neutral", None, 0.0, 0.0, 0.0, f"Insufficient ST candles ({len(st_candles)} < {need})"
        )

    closes = [float(c["close"]) for c in st_candles]
    vols = [_bar_volume_proxy(c) for c in st_candles]

    raw_ps = _raw_ps(closes, slope_lookback)
    raw_vs = _raw_vs(vols, slope_lookback)
    ps_z = _rolling_z(raw_ps, z_window)
    vs_z = _rolling_z(raw_vs, z_window)

    idx = len(st_candles) - 1
    cross = detect_cross(ps_z, vs_z, idx)

    htf_closes = [float(c["close"]) for c in htf_candles if float(c.get("close") or 0) > 0]
    bias = htf_bias_from_closes(htf_closes, htf_ema_fast, htf_ema_slow) if len(htf_closes) >= htf_ema_slow + 2 else "neutral"

    adx_st = float(_adx_from_candles(st_candles, adx_period))
    if adx_st < adx_min:
        return TrendPulseEval(
            False,
            bias,
            cross,
            ps_z[idx],
            vs_z[idx],
            adx_st,
            f"ADX {adx_st:.1f} < {adx_min}",
        )

    if cross is None:
        return TrendPulseEval(
            False, bias, None, ps_z[idx], vs_z[idx], adx_st, "No PS_z/VS_z cross on latest bar"
        )

    if cross == "bullish" and bias != "bullish":
        return TrendPulseEval(
            False, bias, cross, ps_z[idx], vs_z[idx], adx_st, f"Bullish cross but HTF bias is {bias}"
        )
    if cross == "bearish" and bias != "bearish":
        return TrendPulseEval(
            False, bias, cross, ps_z[idx], vs_z[idx], adx_st, f"Bearish cross but HTF bias is {bias}"
        )

    return TrendPulseEval(
        True,
        bias,
        cross,
        ps_z[idx],
        vs_z[idx],
        adx_st,
        "OK",
    )


def _bar_session_date(candle: dict[str, Any], exchange_tz: ZoneInfo) -> date | None:
    """Calendar date of the bar in the exchange timezone (IST for NSE).

    Kite ``historical_data`` returns timezone-**naive** datetimes in **exchange local** time, not UTC.
    Interpreting naive as UTC shifts the session date incorrectly. Aware timestamps are converted
    to ``exchange_tz`` for the calendar date.
    """
    v = candle.get("time")
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            return v.astimezone(exchange_tz).date()
        return v.replace(tzinfo=exchange_tz).date()
    if isinstance(v, str) and v.strip():
        s = v.strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is not None:
            return d.astimezone(exchange_tz).date()
        return d.replace(tzinfo=exchange_tz).date()
    return None


def _display_day_indices(
    st_candles: list[dict[str, Any]],
    t_min: int,
    *,
    tz: ZoneInfo,
    now_utc: datetime,
) -> tuple[list[int], date]:
    """
    Indices [t_min, n) for **current calendar day only** in ``tz`` (IST for NSE).

    No fallback to previous sessions — if the feed has no bars for today yet, returns ([], today).
    Z-scores are still computed on the full candle list elsewhere.
    """
    n = len(st_candles)
    today = now_utc.astimezone(tz).date()
    window = range(t_min, n)

    idx_today = [i for i in window if _bar_session_date(st_candles[i], tz) == today]
    if idx_today:
        return idx_today, today
    return [], today


def _candle_instant_utc_naive(time_raw: Any, exchange_tz: ZoneInfo) -> datetime | None:
    """Interpret Kite candle ``time`` as exchange-local when naive; return UTC wall as naive datetime."""
    if isinstance(time_raw, datetime):
        d = time_raw
        if d.tzinfo is None:
            d = d.replace(tzinfo=exchange_tz)
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(time_raw, str) and time_raw.strip():
        s = time_raw.strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=exchange_tz)
        return d.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def _candle_time_to_utc_z_str(time_raw: Any, exchange_tz: ZoneInfo) -> str:
    """API/chart timestamps as UTC ISO8601 with Z (matches DB trades + frontend parseBackendUtcNaive)."""
    u = _candle_instant_utc_naive(time_raw, exchange_tz)
    if u is None:
        return str(time_raw or "")
    return u.isoformat() + "Z"


def build_trendpulse_chart_series(
    st_candles: list[dict[str, Any]],
    *,
    z_window: int,
    slope_lookback: int,
    tail: int = 96,
    adx_period: int = 14,
    display_timezone: str = "Asia/Kolkata",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return aligned PS_z/VS_z for UI chart — one session day in ``display_timezone`` (default IST)."""
    need = max(z_window, slope_lookback) + slope_lookback + 3
    if len(st_candles) < need:
        return {
            "times": [],
            "ps_z": [],
            "vs_z": [],
            "adx_last": None,
            "warmup_bars": need,
            "tail_start_index": 0,
            "displayDate": None,
            "displayDateFallback": False,
            "noBarsForDisplayDate": False,
            "chartHint": None,
        }
    closes = [float(c["close"]) for c in st_candles]
    vols = [_bar_volume_proxy(c) for c in st_candles]
    raw_ps = _raw_ps(closes, slope_lookback)
    raw_vs = _raw_vs(vols, slope_lookback)
    ps_z = _rolling_z(raw_ps, z_window)
    vs_z = _rolling_z(raw_vs, z_window)
    warm = max(z_window, slope_lookback) + 2
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    try:
        tz = ZoneInfo(display_timezone)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")

    display_idx, session_day = _display_day_indices(
        st_candles,
        warm,
        tz=tz,
        now_utc=now,
    )
    n_bars = len(st_candles)
    adx_last = float(_adx_from_candles(st_candles, adx_period))

    if not display_idx:
        return {
            "times": [],
            "ps_z": [],
            "vs_z": [],
            "adx_last": round(adx_last, 2),
            "warmup_bars": warm,
            "tail_start_index": n_bars,
            "displayDate": session_day.isoformat(),
            "displayTimezone": display_timezone,
            "displayDateFallback": False,
            "noBarsForDisplayDate": True,
            "chartHint": (
                f"No candles yet for {session_day.isoformat()} ({display_timezone}) — "
                "pre-market, holiday, or broker feed has not published today’s bars. "
                "Refresh after live data arrives."
            ),
        }

    times: list[str] = []
    pz: list[float] = []
    vz: list[float] = []
    t0 = display_idx[0]
    for i in display_idx:
        times.append(_candle_time_to_utc_z_str(st_candles[i].get("time"), tz))
        pz.append(round(float(ps_z[i]), 6))
        vz.append(round(float(vs_z[i]), 6))
    return {
        "times": times,
        "ps_z": pz,
        "vs_z": vz,
        "adx_last": round(adx_last, 2),
        "warmup_bars": warm,
        "tail_start_index": t0,
        "displayDate": session_day.isoformat(),
        "displayTimezone": display_timezone,
        "displayDateFallback": False,
        "noBarsForDisplayDate": False,
        "chartHint": None,
    }


def build_trendpulse_entry_events(
    st_candles: list[dict[str, Any]],
    htf_candles: list[dict[str, Any]],
    *,
    z_window: int,
    slope_lookback: int,
    adx_period: int,
    adx_min: float,
    htf_ema_fast: int,
    htf_ema_slow: int,
    tail_start_index: int,
    exchange_timezone: str = "Asia/Kolkata",
) -> list[dict[str, Any]]:
    """Entry events where TrendPulse Z conditions are satisfied on each ST close."""
    n = len(st_candles)
    if n < 3:
        return []

    try:
        etz = ZoneInfo(exchange_timezone)
    except Exception:
        etz = ZoneInfo("Asia/Kolkata")

    closes = [float(c.get("close") or 0.0) for c in st_candles]
    vols = [_bar_volume_proxy(c) for c in st_candles]
    raw_ps = _raw_ps(closes, slope_lookback)
    raw_vs = _raw_vs(vols, slope_lookback)
    ps_z = _rolling_z(raw_ps, z_window)
    vs_z = _rolling_z(raw_vs, z_window)
    need = max(z_window, slope_lookback) + slope_lookback + 5

    htf_ts: list[datetime] = []
    htf_closes: list[float] = []
    for c in htf_candles:
        t = _candle_instant_utc_naive(c.get("time"), etz)
        cl = float(c.get("close") or 0.0)
        if t is None or cl <= 0:
            continue
        htf_ts.append(t)
        htf_closes.append(cl)
    if not htf_ts:
        return []

    events: list[dict[str, Any]] = []
    h_idx = 0
    for i in range(max(1, need), n):
        if i < tail_start_index:
            continue
        st_time = _candle_instant_utc_naive(st_candles[i].get("time"), etz)
        if st_time is None:
            continue
        while h_idx + 1 < len(htf_ts) and htf_ts[h_idx + 1] <= st_time:
            h_idx += 1

        cross = detect_cross(ps_z, vs_z, i)
        if cross is None:
            continue
        bias = htf_bias_from_closes(htf_closes[: h_idx + 1], htf_ema_fast, htf_ema_slow)
        if bias not in ("bullish", "bearish"):
            continue
        adx_i = float(_adx_from_candles(st_candles[: i + 1], adx_period))
        if adx_i < adx_min:
            continue
        if (cross == "bullish" and bias != "bullish") or (cross == "bearish" and bias != "bearish"):
            continue
        events.append(
            {
                "tailIndex": i - tail_start_index,
                "time": st_time.isoformat() + "Z",
                "cross": cross,
                "htfBias": bias,
                "psZ": round(float(ps_z[i]), 4),
                "vsZ": round(float(vs_z[i]), 4),
                "adxSt": round(adx_i, 2),
            }
        )
    return events
