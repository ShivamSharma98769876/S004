"""
Black-Scholes Greeks and IV for index options (European style).
Uses only stdlib math.
"""

from __future__ import annotations

import math
from datetime import date

DEFAULT_R = 0.07


def _n(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, S - K)
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    return S * _n(d1) - K * math.exp(-r * T) * _n(d2)


def bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return max(0.0, K - S)
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    return K * math.exp(-r * T) * _n(-d2) - S * _n(-d1)


def call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return 1.0 if S > K else (0.5 if S == K else 0.0)
    return _n(_d1(S, K, T, r, sigma))


def put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0:
        return -1.0 if S < K else (-0.5 if S == K else 0.0)
    return _n(_d1(S, K, T, r, sigma)) - 1.0


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def call_theta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    term1 = -S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T))
    term2 = r * K * math.exp(-r * T) * _n(d2)
    return (term1 - term2) / 365.0


def put_theta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)
    term1 = -S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T))
    term2 = r * K * math.exp(-r * T) * _n(-d2)
    return (term1 + term2) / 365.0


def iv_call_bisection(S: float, K: float, T: float, r: float, price: float, max_iter: int = 80) -> float:
    if T <= 0 or price <= 0:
        return 0.0
    intrinsic = max(0.0, S - K)
    if price <= intrinsic:
        return 0.001
    low, high = 0.001, 3.0
    for _ in range(max_iter):
        mid = (low + high) / 2
        p = bs_call_price(S, K, T, r, mid)
        if abs(p - price) < 1e-6:
            return mid
        if p > price:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def iv_put_bisection(S: float, K: float, T: float, r: float, price: float, max_iter: int = 80) -> float:
    if T <= 0 or price <= 0:
        return 0.0
    intrinsic = max(0.0, K - S)
    if price <= intrinsic:
        return 0.001
    low, high = 0.001, 3.0
    for _ in range(max_iter):
        mid = (low + high) / 2
        p = bs_put_price(S, K, T, r, mid)
        if abs(p - price) < 1e-6:
            return mid
        if p > price:
            high = mid
        else:
            low = mid
    return (low + high) / 2


def time_to_expiry_years(expiry_date: date) -> float:
    today = date.today()
    delta = (expiry_date - today).days
    if delta < 0:
        return 1e-4
    return max(1e-4, delta / 365.25)


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black–Scholes gamma (same for CE and PE)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def compute_gamma_from_ltp(
    spot: float,
    strike: float,
    expiry_date: date,
    ltp: float,
    option_type: str,
    r: float = DEFAULT_R,
) -> float:
    """Gamma from market LTP via implied vol (same BS pipeline as ``compute_greeks``)."""
    if spot <= 0 or ltp <= 0:
        return 0.0
    ot = (option_type or "").upper().strip()
    if ot not in ("CE", "PE"):
        return 0.0
    T = max(1e-4, time_to_expiry_years(expiry_date))
    if ot == "CE":
        iv = iv_call_bisection(spot, strike, T, r, ltp)
    else:
        iv = iv_put_bisection(spot, strike, T, r, ltp)
    if iv <= 1e-9:
        return 0.0
    return float(bs_gamma(spot, strike, T, r, iv))


def compute_greeks(
    spot: float,
    strike: float,
    expiry_date: date,
    ltp: float,
    option_type: str,
    r: float = DEFAULT_R,
) -> tuple[float, float, float]:
    if spot <= 0 or ltp <= 0:
        return 0.0, 0.0, 0.0
    T = max(1e-4, time_to_expiry_years(expiry_date))
    if option_type == "CE":
        iv = iv_call_bisection(spot, strike, T, r, ltp)
        delta = call_delta(spot, strike, T, r, iv)
        theta = call_theta(spot, strike, T, r, iv)
    else:
        iv = iv_put_bisection(spot, strike, T, r, ltp)
        delta = put_delta(spot, strike, T, r, iv)
        theta = put_theta(spot, strike, T, r, iv)
    return round(delta, 4), round(theta, 2), round(iv * 100.0, 2)
