"""BS gamma from LTP (TrendPulse strike ranking)."""

from datetime import date

from app.services.option_greeks import compute_gamma_from_ltp


def test_gamma_positive_atm_like():
    spot = 25000.0
    strike = 25000.0
    exp = date(2026, 4, 30)
    g = compute_gamma_from_ltp(spot, strike, exp, 150.0, "CE")
    assert g > 0
