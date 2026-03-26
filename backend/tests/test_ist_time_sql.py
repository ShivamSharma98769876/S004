from app.services.ist_time_sql import IST_TODAY, closed_at_ist_date, closed_at_ist_date_bare


def test_closed_at_ist_date_contains_expected_tokens():
    s = closed_at_ist_date("t")
    assert "t.closed_at" in s
    assert "Asia/Kolkata" in s
    assert "UTC" in s


def test_ist_today_is_postgres_expression():
    assert "Asia/Kolkata" in IST_TODAY
    assert "CURRENT_TIMESTAMP" in IST_TODAY


def test_bare_matches_alias_t():
    assert "closed_at" in closed_at_ist_date_bare()
    assert "t." not in closed_at_ist_date_bare()
