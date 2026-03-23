"""Parse Zerodha-style compact NFO symbols built by trades_service (e.g. NIFTY24110722000CE)."""

from __future__ import annotations

from typing import Any


def parse_compact_option_symbol(symbol: str) -> dict[str, Any] | None:
    """
    Extract strike and option type from stored compact symbol.

    Format: {UNDERLYING}{expiryDigits}{strike}{CE|PE}
    Expiry digits length varies (5–8); strike is the trailing integer before CE/PE.
    """
    sym = str(symbol or "").upper().replace(" ", "").strip()
    if sym.endswith("CE"):
        opt = "CE"
        body = sym[:-2]
    elif sym.endswith("PE"):
        opt = "PE"
        body = sym[:-2]
    else:
        return None

    for under in ("BANKNIFTY", "MIDCPNIFTY", "FINNIFTY", "NIFTY"):
        if body.startswith(under):
            num = body[len(under) :]
            break
    else:
        return None

    if not num.isdigit() or len(num) < 6:
        return None

    # Try expiry prefix lengths; remainder must be a plausible index-option strike.
    # (Reject huge false positives when expiry length is wrong, e.g. 24110722000 → not strike 722000.)
    max_strike = 130_000 if under == "NIFTY" else 200_000
    min_strike = 5000

    for exp_len in (6, 5, 7, 8):
        if len(num) <= exp_len:
            continue
        strike_part = num[exp_len:]
        if not strike_part.isdigit():
            continue
        strike = int(strike_part)
        if min_strike <= strike <= max_strike:
            return {"underlying": under, "optionType": opt, "strike": strike}

    return None
