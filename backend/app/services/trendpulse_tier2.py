"""TrendPulse Z two-tier — Tier-2 strike rules (spec: TRENDPULSE_Z_TWO_TIER_IMPLEMENTATION_SPEC.md)."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def trendpulse_opening_window_blocked(now_utc: datetime | None = None) -> bool:
    """
    First 5 minutes after cash open (09:15–09:20 IST, weekday): no new entries.
    Half-open interval [09:15:00, 09:20:00).
    """
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return False
    t = local.time()
    return time(9, 15) <= t < time(9, 20)


def option_extrinsic_share(premium: float, spot: float, strike: int, opt_type: str) -> float | None:
    """
    (premium − intrinsic) / premium — fraction of premium that is time value.
    Returns None if premium <= 0.
    """
    if premium <= 0:
        return None
    ot = (opt_type or "").upper().strip()
    if ot == "CE":
        intrinsic = max(0.0, float(spot) - float(strike))
    elif ot == "PE":
        intrinsic = max(0.0, float(strike) - float(spot))
    else:
        return None
    return max(0.0, (float(premium) - intrinsic) / float(premium))


def delta_abs_in_band(delta: float, lo: float, hi: float) -> bool:
    """|delta| in [lo, hi] (e.g. 0.40–0.50 around target 0.45)."""
    a = abs(float(delta))
    return float(lo) <= a <= float(hi)
