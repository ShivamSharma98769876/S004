from app.services.option_symbol_compact import parse_compact_option_symbol


def test_parse_nifty_weekly():
    r = parse_compact_option_symbol("NIFTY24110722000CE")
    assert r is not None
    assert r["strike"] == 22000
    assert r["optionType"] == "CE"
    assert r["underlying"] == "NIFTY"


def test_parse_nifty_pe():
    r = parse_compact_option_symbol("NIFTY24110722000PE")
    assert r is not None
    assert r["strike"] == 22000
    assert r["optionType"] == "PE"


def test_invalid_returns_none():
    assert parse_compact_option_symbol("") is None
    assert parse_compact_option_symbol("RELIANCE") is None


def test_parse_nifty_weekly_compact():
    r = parse_compact_option_symbol("NIFTY2631723250CE")
    assert r is not None
    assert r["strike"] == 23250
    assert r["optionType"] == "CE"
