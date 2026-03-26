"""
SQL fragments for IST calendar dates.

Assumption: `opened_at` / `closed_at` are TIMESTAMP WITHOUT TIME ZONE storing **UTC wall time**
(the same convention used across auto-execute, landing trade markers, and dashboard).
"""

IST_TODAY = "(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date"


def closed_at_ist_date(alias: str = "t") -> str:
    return f"(({alias}.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date"


def opened_at_ist_date(alias: str = "t") -> str:
    return f"(({alias}.opened_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date"


def closed_at_ist_date_bare() -> str:
    return "((closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date"


def opened_at_ist_date_bare() -> str:
    return "((opened_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date"
