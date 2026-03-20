from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class GreeksResult:
    delta: float
    theta: float
    iv_pct: float


def compute_option_greeks(
    spot: float,
    strike: float,
    expiry_date: date,
    ltp: float,
    option_type: str,
) -> GreeksResult:
    """
    W03 scaffold adapter.

    Replace this placeholder with production integration to the project's
    `compute_greeks` implementation (currently available in backup component).
    """
    if spot <= 0 or ltp <= 0:
        return GreeksResult(delta=0.0, theta=0.0, iv_pct=0.0)

    # Minimal deterministic stub values for integration testing.
    moneyness = (spot - strike) / max(spot, 1.0)
    if option_type.upper() == "CE":
        delta = max(0.0, min(1.0, 0.5 + moneyness))
    else:
        delta = min(0.0, max(-1.0, -0.5 + moneyness))
    theta = -abs(ltp) * 0.01
    iv_pct = 15.0 + abs(moneyness) * 100

    return GreeksResult(delta=round(delta, 4), theta=round(theta, 2), iv_pct=round(iv_pct, 2))

