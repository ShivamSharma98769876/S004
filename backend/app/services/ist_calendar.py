"""IST calendar helpers for Python defaults (must match DB ist_time_sql)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_today() -> date:
    return datetime.now(IST).date()
