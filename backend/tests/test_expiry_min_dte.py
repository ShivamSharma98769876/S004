"""Expiry selection: min calendar DTE + optional NIFTY weekly expiry weekday (IST)."""

from datetime import date

from app.services.option_chain_zerodha import (
    first_expiry_meeting_min_calendar_dte,
    select_expiry_min_dte_and_weekday,
)


def test_min_dte_3_skips_short_dte():
    """IST 24 Mar 2026: 26 Mar has DTE 2; min_dte=3 needs 27+ → 31 Mar."""
    today = date(2026, 3, 24)
    exps = ["26MAR2026", "31MAR2026"]
    assert first_expiry_meeting_min_calendar_dte(exps, today, min_dte_days=3) == "31MAR2026"


def test_min_dte_3_none_when_only_short_dte_listed():
    today = date(2026, 3, 24)
    assert first_expiry_meeting_min_calendar_dte(["26MAR2026"], today, min_dte_days=3) is None


def test_min_dte_0_accepts_earliest():
    today = date(2026, 3, 24)
    assert select_expiry_min_dte_and_weekday(
        ["26MAR2026"], today, min_dte_days=0, weekday=None
    ) == "26MAR2026"


def test_weekday_prefers_tuesday_over_earlier_non_tuesday():
    """24 Mar 2026 is Tuesday. min_dte=2: 26 Thu and 31 Tue qualify; pick first Tuesday."""
    today = date(2026, 3, 24)
    exps = ["26MAR2026", "31MAR2026"]
    assert select_expiry_min_dte_and_weekday(exps, today, min_dte_days=2, weekday=1) == "31MAR2026"


def test_weekday_none_earliest_min_dte():
    today = date(2026, 3, 24)
    exps = ["26MAR2026", "31MAR2026"]
    assert select_expiry_min_dte_and_weekday(exps, today, min_dte_days=2, weekday=None) == "26MAR2026"


def test_weekday_strict_none_if_no_matching_day():
    """Qualified by DTE but no expiry on requested weekday."""
    today = date(2026, 3, 24)
    # Only Thursday 26 Mar meets min_dte>=2; no Tuesday in list
    assert select_expiry_min_dte_and_weekday(["26MAR2026"], today, min_dte_days=2, weekday=1) is None
