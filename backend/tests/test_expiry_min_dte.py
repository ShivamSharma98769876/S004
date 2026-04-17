"""Expiry selection: min calendar DTE + optional NIFTY weekly expiry weekday (IST)."""

from datetime import date

import pytest

import app.services.option_chain_zerodha as ocz
from app.services.option_chain_zerodha import (
    first_expiry_meeting_min_calendar_dte,
    get_expiries_for_instrument,
    pick_expiry_two_trading_dte_tuesday_preferred,
    resolve_expiry_min_dte_weekday_with_fallback,
    select_expiry_min_dte_and_weekday,
    trading_sessions_from_tomorrow_through_expiry,
)
from app.services.trades_service import _short_premium_datm_allows_leg


def test_min_dte_3_skips_short_dte():
    """IST 24 Mar 2026: 26 Mar has DTE 2; min_dte=3 needs 27+ → 31 Mar."""
    today = date(2026, 3, 24)
    exps = ["26MAR2026", "31MAR2026"]
    assert first_expiry_meeting_min_calendar_dte(exps, today, min_dte_days=3) == "31MAR2026"


def test_min_dte_3_none_when_only_short_dte_listed():
    today = date(2026, 3, 24)
    assert first_expiry_meeting_min_calendar_dte(["26MAR2026"], today, min_dte_days=3) is None


def test_trading_sessions_mon_to_wed_counts_two():
    """Monday → Wednesday: Tue + Wed sessions = 2 (StochasticBNF 2-DTE)."""
    today = date(2026, 4, 6)  # Mon
    expiry = date(2026, 4, 8)  # Wed
    assert trading_sessions_from_tomorrow_through_expiry(today, expiry, holidays=set()) == 2


def test_trading_sessions_mon_to_tue_next_day_counts_one():
    today = date(2026, 4, 6)
    expiry = date(2026, 4, 7)  # Tue
    assert trading_sessions_from_tomorrow_through_expiry(today, expiry, holidays=set()) == 1


def test_pick_two_trading_dte_wednesday_when_only_match():
    today = date(2026, 4, 6)  # Mon; Wed 08Apr has Tue+Wed = 2 sessions
    exps = ["08APR2026"]
    assert pick_expiry_two_trading_dte_tuesday_preferred(exps, today=today) == "08APR2026"


def test_pick_two_trading_dte_prefers_earlier_tuesday_when_both_qualify():
    """Fri 08 May 2026: Tue 12 May has 2 sessions (Mon+Tue); Wed 13 May has 3 — only 12MAY qualifies."""
    today = date(2026, 5, 8)
    exps = ["12MAY2026", "13MAY2026"]
    assert pick_expiry_two_trading_dte_tuesday_preferred(exps, today=today) == "12MAY2026"


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
    """Low-level selector: qualified by DTE but no expiry on requested weekday → None."""
    today = date(2026, 3, 24)
    assert select_expiry_min_dte_and_weekday(["26MAR2026"], today, min_dte_days=2, weekday=1) is None


def test_short_premium_asymmetric_datm_windows():
    ce_lo, ce_hi, pe_lo, pe_hi = 2, 4, -4, 2
    assert _short_premium_datm_allows_leg("CE", 3, ce_min=ce_lo, ce_max=ce_hi, pe_min=pe_lo, pe_max=pe_hi)
    assert not _short_premium_datm_allows_leg("CE", 1, ce_min=ce_lo, ce_max=ce_hi, pe_min=pe_lo, pe_max=pe_hi)
    assert _short_premium_datm_allows_leg("PE", -3, ce_min=ce_lo, ce_max=ce_hi, pe_min=pe_lo, pe_max=pe_hi)
    assert _short_premium_datm_allows_leg("PE", 2, ce_min=ce_lo, ce_max=ce_hi, pe_min=pe_lo, pe_max=pe_hi)
    assert not _short_premium_datm_allows_leg("PE", 3, ce_min=ce_lo, ce_max=ce_hi, pe_min=pe_lo, pe_max=pe_hi)


def test_resolve_weekday_fallback_when_min_dte_ok_but_wrong_weekday():
    """Trading helper: still return earliest min-DTE expiry so option chain can load strikes."""
    today = date(2026, 3, 24)
    assert (
        resolve_expiry_min_dte_weekday_with_fallback(
            ["26MAR2026"], today, min_dte_days=2, weekday=1
        )
        == "26MAR2026"
    )


def test_resolve_prefers_preponed_monday_before_next_tuesday():
    """
    NSE may list weekly index expiry on Monday when Tuesday is a holiday.
    Strict Tuesday pick would skip to the following week (e.g. 21 Apr vs 13 Apr).
    """
    today = date(2026, 3, 7)
    exps = ["13APR2026", "21APR2026"]
    assert (
        resolve_expiry_min_dte_weekday_with_fallback(
            exps, today, min_dte_days=2, weekday=1
        )
        == "13APR2026"
    )


def test_estimated_weekly_prepones_when_target_weekday_is_holiday(
    monkeypatch: pytest.MonkeyPatch,
):
    class _FakeDate(date):
        @classmethod
        def today(cls) -> "_FakeDate":
            return cls(2026, 4, 8)

    monkeypatch.setattr(ocz, "date", _FakeDate)
    monkeypatch.setenv("S004_NSE_HOLIDAYS", "14APR2026")
    expiries = get_expiries_for_instrument("NIFTY")
    assert expiries[0] == "13APR2026"
