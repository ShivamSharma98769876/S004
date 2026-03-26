"""TrendPulse Z Phase 3 — risk profiles, session filters, optional breadth gates (see docs/strategies/TRENDPULSE_Z_IMPLEMENTATION_PLAN.md)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.services.trendpulse_z import TrendPulseEval

PROFILE_PRESETS: dict[str, dict[str, Any]] = {
    "conservative": {
        "zWindow": 80,
        "slopeLookback": 6,
        "adxMin": 22.0,
        "ivRankMaxPercentile": 60.0,
    },
    "balanced": {
        "zWindow": 50,
        "slopeLookback": 4,
        "adxMin": 18.0,
        "ivRankMaxPercentile": 70.0,
    },
    "aggressive": {
        "zWindow": 40,
        "slopeLookback": 3,
        "adxMin": 15.0,
        "ivRankMaxPercentile": 80.0,
    },
}

_NUMERIC_OVERRIDE_KEYS = (
    "stInterval",
    "htfInterval",
    "zWindow",
    "slopeLookback",
    "adxMin",
    "adxPeriod",
    "htfEmaFast",
    "htfEmaSlow",
    "ivRankMaxPercentile",
    "candleDaysBack",
)


def _session_defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "timezone": "Asia/Kolkata",
        "blockFirstMinutes": 15,
        "blockLastMinutes": 25,
        "marketOpenHour": 9,
        "marketOpenMinute": 15,
        "marketCloseHour": 15,
        "marketCloseMinute": 30,
    }


def _breadth_defaults() -> dict[str, Any]:
    return {
        "enabled": False,
        "requireSpotAligned": True,
        "minAbsSpotChgPct": 0.05,
        "requirePcrAligned": False,
        "pcrBullishMax": 1.05,
        "pcrBearishMin": 0.95,
    }


def merge_session_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = _session_defaults()
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out or k in ("enabled", "timezone"):
                out[k] = v
    return out


def merge_breadth_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = _breadth_defaults()
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in out:
                out[k] = v
    return out


_WEEKDAY_NAME_TO_INT: dict[str, int] = {
    "MONDAY": 0,
    "MON": 0,
    "TUESDAY": 1,
    "TUE": 1,
    "WEDNESDAY": 2,
    "WED": 2,
    "THURSDAY": 3,
    "THU": 3,
    "FRIDAY": 4,
    "FRI": 4,
    "SATURDAY": 5,
    "SAT": 5,
    "SUNDAY": 6,
    "SUN": 6,
}


def parse_nifty_weekly_expiry_weekday(raw: Any) -> int | None:
    """
    NIFTY weekly index expiry **calendar weekday** (Python: Mon=0 … Sun=6).
    Default **1 (Tuesday)** — current NSE NIFTY weekly expiry day for standard weeklies.
    Return ``None`` to skip weekday filtering (earliest expiry that satisfies ``minDteCalendarDays`` only).
    """
    if raw is None:
        return 1
    if isinstance(raw, str):
        s = raw.strip().upper()
        if s in ("ANY", "NONE", "OFF", "*", ""):
            return None
        if s.isdigit() and len(s) == 1:
            v = int(s)
            if 0 <= v <= 6:
                return v
        return _WEEKDAY_NAME_TO_INT.get(s, 1)
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        if raw < 0:
            return None
        if 0 <= raw <= 6:
            return raw
        return 1
    return 1


def resolve_trendpulse_z_config(tpz_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Merge profile preset with explicit trendPulseZ fields (explicit always wins).
    Returns a dict suitable for trendpulse_config including nested session/breadth.
    """
    raw = tpz_raw if isinstance(tpz_raw, dict) else {}
    profile_key = str(raw.get("profile") or raw.get("riskProfile") or "balanced").strip().lower()
    if profile_key not in PROFILE_PRESETS:
        profile_key = "balanced"
    preset = dict(PROFILE_PRESETS[profile_key])
    merged: dict[str, Any] = {
        "stInterval": str(raw.get("stInterval", "5minute")),
        "htfInterval": str(raw.get("htfInterval", "15minute")),
        "zWindow": int(preset["zWindow"]),
        "slopeLookback": int(preset["slopeLookback"]),
        "adxMin": float(preset["adxMin"]),
        "adxPeriod": int(raw.get("adxPeriod", 14)),
        "htfEmaFast": int(raw.get("htfEmaFast", 13)),
        "htfEmaSlow": int(raw.get("htfEmaSlow", 34)),
        "ivRankMaxPercentile": float(preset["ivRankMaxPercentile"]),
        "candleDaysBack": int(raw.get("candleDaysBack", 5)),
        "profile": profile_key,
        "session": merge_session_config(raw.get("session") if isinstance(raw.get("session"), dict) else None),
        "breadth": merge_breadth_config(raw.get("breadth") if isinstance(raw.get("breadth"), dict) else None),
        # Two-tier strike defaults (see TRENDPULSE_Z_TWO_TIER_IMPLEMENTATION_SPEC.md)
        "minDteCalendarDays": int(raw.get("minDteCalendarDays", 2)),
        "niftyWeeklyExpiryWeekday": parse_nifty_weekly_expiry_weekday(raw.get("niftyWeeklyExpiryWeekday")),
        "deltaMinAbs": float(raw.get("deltaMinAbs", 0.40)),
        "deltaMaxAbs": float(raw.get("deltaMaxAbs", 0.50)),
        "extrinsicShareMin": float(raw.get("extrinsicShareMin", 0.25)),
        # Strike selection: premium cap (₹ LTP) and max-gamma ranking (see trades_service TrendPulse Z)
        "maxOptionPremiumInr": float(raw.get("maxOptionPremiumInr", 80.0)),
        "selectStrikeByMaxGamma": bool(raw.get("selectStrikeByMaxGamma", True)),
        "maxStrikeRecommendations": int(raw.get("maxStrikeRecommendations", 1)),
    }
    for k in _NUMERIC_OVERRIDE_KEYS:
        if k in raw and raw[k] is not None:
            if k in ("zWindow", "slopeLookback", "adxPeriod", "htfEmaFast", "htfEmaSlow", "candleDaysBack"):
                merged[k] = int(raw[k])
            elif k in ("adxMin", "ivRankMaxPercentile"):
                merged[k] = float(raw[k])
            elif k in ("stInterval", "htfInterval"):
                merged[k] = str(raw[k])
    for k in ("minDteCalendarDays",):
        if k in raw and raw[k] is not None:
            merged[k] = int(raw[k])
    for k in ("deltaMinAbs", "deltaMaxAbs", "extrinsicShareMin", "maxOptionPremiumInr"):
        if k in raw and raw[k] is not None:
            merged[k] = float(raw[k])
    if "selectStrikeByMaxGamma" in raw:
        merged["selectStrikeByMaxGamma"] = bool(raw.get("selectStrikeByMaxGamma"))
    if "maxStrikeRecommendations" in raw and raw.get("maxStrikeRecommendations") is not None:
        merged["maxStrikeRecommendations"] = int(raw["maxStrikeRecommendations"])
    if "niftyWeeklyExpiryWeekday" in raw:
        merged["niftyWeeklyExpiryWeekday"] = parse_nifty_weekly_expiry_weekday(raw.get("niftyWeeklyExpiryWeekday"))
    return merged


def _minutes_since_midnight(t: time) -> int:
    return t.hour * 60 + t.minute


def session_block_reason(now_utc: datetime, session_cfg: dict[str, Any]) -> str | None:
    """Return a human-readable block reason, or None if session filter passes / disabled."""
    if not session_cfg.get("enabled"):
        return None
    tz_name = str(session_cfg.get("timezone") or "Asia/Kolkata")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Kolkata")
    local = now_utc.astimezone(tz) if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    if local.weekday() >= 5:
        return "Session filter: weekend (NSE closed)"
    oh = int(session_cfg.get("marketOpenHour", 9))
    om = int(session_cfg.get("marketOpenMinute", 15))
    ch = int(session_cfg.get("marketCloseHour", 15))
    cm = int(session_cfg.get("marketCloseMinute", 30))
    open_t = time(oh, om)
    close_t = time(ch, cm)
    cur = local.time()
    open_m = _minutes_since_midnight(open_t)
    close_m = _minutes_since_midnight(close_t)
    cur_m = _minutes_since_midnight(cur)
    if cur_m < open_m or cur_m > close_m:
        return "Session filter: outside market hours (IST)"
    first_block = max(0, int(session_cfg.get("blockFirstMinutes", 0) or 0))
    last_block = max(0, int(session_cfg.get("blockLastMinutes", 0) or 0))
    if first_block and cur_m < open_m + first_block:
        return f"Session filter: first {first_block} minutes after open (IST) blocked"
    if last_block and cur_m > close_m - last_block:
        return f"Session filter: last {last_block} minutes before close (IST) blocked"
    return None


def breadth_block_reason(
    ev: TrendPulseEval,
    breadth_cfg: dict[str, Any],
    *,
    spot_chg_pct: float | None,
    pcr: float | None,
) -> str | None:
    """Optional hard gates using spot % change and PCR as lightweight breadth proxies."""
    if not breadth_cfg.get("enabled") or not ev.ok:
        return None
    cross = ev.cross or ""
    min_abs = float(breadth_cfg.get("minAbsSpotChgPct") or 0.0)
    if breadth_cfg.get("requireSpotAligned") and min_abs > 0 and spot_chg_pct is not None:
        sc = float(spot_chg_pct)
        if cross == "bullish" and sc < min_abs:
            return f"Breadth filter: spot change {sc:.2f}% < required +{min_abs:.2f}% for bullish entry"
        if cross == "bearish" and sc > -min_abs:
            return f"Breadth filter: spot change {sc:.2f}% > required {(-min_abs):.2f}% for bearish entry"
    if breadth_cfg.get("requirePcrAligned") and pcr is not None:
        p = float(pcr)
        pmax = float(breadth_cfg.get("pcrBullishMax", 1.05))
        pmin = float(breadth_cfg.get("pcrBearishMin", 0.95))
        if cross == "bullish" and p > pmax:
            return f"Breadth filter: PCR {p:.2f} > {pmax:.2f} (not constructive for bullish)"
        if cross == "bearish" and p < pmin:
            return f"Breadth filter: PCR {p:.2f} < {pmin:.2f} (not cautious for bearish)"
    return None


def apply_trendpulse_hard_gates(
    ev: TrendPulseEval,
    trendpulse_config: dict[str, Any],
    *,
    spot_chg_pct: float | None,
    pcr: float | None,
    now_utc: datetime | None = None,
) -> TrendPulseEval:
    """If signal is OK, apply session + breadth; return updated TrendPulseEval."""
    if not ev.ok:
        return ev
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    session_cfg = trendpulse_config.get("session")
    if not isinstance(session_cfg, dict):
        session_cfg = {}
    msg = session_block_reason(now, session_cfg)
    if msg:
        return replace(ev, ok=False, reason=msg)
    breadth_cfg = trendpulse_config.get("breadth")
    if not isinstance(breadth_cfg, dict):
        breadth_cfg = {}
    msg2 = breadth_block_reason(ev, breadth_cfg, spot_chg_pct=spot_chg_pct, pcr=pcr)
    if msg2:
        return replace(ev, ok=False, reason=msg2)
    return ev
