"""Landing OI pinboard: top strikes by open interest + short-premium context."""

from __future__ import annotations

from typing import Any

OI_WINDOW_NOTE = (
    "OI leaders are the heaviest strikes in the current ATM±chain window from your broker feed, "
    "not the entire exchange chain."
)


def oi_walls_stub(
    *,
    status: str,
    detail: str = "",
    spot: float = 0.0,
    expiry: str = "—",
) -> dict[str, Any]:
    """Structured empty payload so the UI can explain broker vs chain vs zero-OI states."""
    return {
        "status": status,
        "detail": detail,
        "expiry": expiry,
        "spot": round(spot, 2) if spot else 0.0,
        "ceLeaders": [],
        "peLeaders": [],
        "pinRangeHint": None,
        "windowNote": OI_WINDOW_NOTE,
        "spotTrail": [],
    }


def _oi_int(row: dict[str, Any], leg: str) -> int:
    try:
        return int(float((row.get(leg) or {}).get("oi") or 0))
    except (TypeError, ValueError):
        return 0


def _seller_note_ce(*, strike: int, spot: float, buildup: str, oi_chg: float) -> str:
    above = strike > spot
    pts = abs(int(round(strike - spot)))
    zone = "above spot" if above else "at/below spot"
    base = (
        f"Largest call OI in this chain window ({zone}, {pts} pts from spot). "
        "Heavy short-call positioning often caps upside near expiry as writers hedge."
    )
    if buildup and buildup != "—":
        base += f" Flow: {buildup}."
    if oi_chg > 3:
        base += " Fresh writing risk — watch for pin if price gravitates here."
    elif oi_chg < -3:
        base += " OI unwinding — wall may be softening."
    return base


def _seller_note_pe(*, strike: int, spot: float, buildup: str, oi_chg: float) -> str:
    below = strike < spot
    pts = abs(int(round(spot - strike)))
    zone = "below spot" if below else "at/above spot"
    base = (
        f"Largest put OI in this chain window ({zone}, {pts} pts from spot). "
        "Short puts cluster as a soft floor; sharp breaks can force covering."
    )
    if buildup and buildup != "—":
        base += f" Flow: {buildup}."
    if oi_chg > 3:
        base += " New put writing — support narrative strengthens until broken."
    elif oi_chg < -3:
        base += " Put OI cutting — downside cushion may be thinning."
    return base


def _theta_hint(theta: Any) -> str | None:
    try:
        t = float(theta)
    except (TypeError, ValueError):
        return None
    if abs(t) < 1e-6:
        return None
    # Per-lot theta from chain (typically negative for long; seller collects opposite sign context)
    return f"Theta ~{t:.1f}/day (per lot) — time decay works for shorts if spot stays tame."


def build_oi_walls_from_chain(
    chain: list[dict[str, Any]],
    spot: float,
    expiry_label: str,
    *,
    top_n: int = 2,
) -> dict[str, Any]:
    """
    Pick top-N call and put strikes by OI in the given chain slice.
    ``spot`` is underlying last; used for distance / seller copy only.
    """
    if not chain:
        return {
            "status": "no_rows",
            "detail": "Option chain returned no strikes (broker or instrument cache).",
            "expiry": expiry_label,
            "spot": round(spot, 2),
            "ceLeaders": [],
            "peLeaders": [],
            "pinRangeHint": None,
            "windowNote": OI_WINDOW_NOTE,
            "spotTrail": [],
        }

    ce_ranked = sorted(chain, key=lambda r: _oi_int(r, "call"), reverse=True)[:top_n]
    pe_ranked = sorted(chain, key=lambda r: _oi_int(r, "put"), reverse=True)[:top_n]

    def _shape_ce(row: dict[str, Any]) -> dict[str, Any]:
        c = row.get("call") or {}
        strike = int(row.get("strike") or 0)
        oi = _oi_int(row, "call")
        oi_chg = float(c.get("oiChgPct") or 0)
        buildup = str(c.get("buildup") or "—")
        dist = int(round(strike - spot))
        return {
            "strike": strike,
            "oi": oi,
            "oiChgPct": round(oi_chg, 2),
            "ltp": c.get("ltp"),
            "iv": c.get("iv"),
            "buildup": buildup,
            "distanceFromSpotPts": dist,
            "positionVsSpot": "OTM" if strike > spot else "ITM" if strike < spot else "ATM",
            "sellerNote": _seller_note_ce(strike=strike, spot=spot, buildup=buildup, oi_chg=oi_chg),
            "thetaHint": _theta_hint(c.get("theta")),
        }

    def _shape_pe(row: dict[str, Any]) -> dict[str, Any]:
        p = row.get("put") or {}
        strike = int(row.get("strike") or 0)
        oi = _oi_int(row, "put")
        oi_chg = float(p.get("oiChgPct") or 0)
        buildup = str(p.get("buildup") or "—")
        dist = int(round(strike - spot))
        return {
            "strike": strike,
            "oi": oi,
            "oiChgPct": round(oi_chg, 2),
            "ltp": p.get("ltp"),
            "iv": p.get("iv"),
            "buildup": buildup,
            "distanceFromSpotPts": dist,
            "positionVsSpot": "OTM" if strike < spot else "ITM" if strike > spot else "ATM",
            "sellerNote": _seller_note_pe(strike=strike, spot=spot, buildup=buildup, oi_chg=oi_chg),
            "thetaHint": _theta_hint(p.get("theta")),
        }

    ce_leaders = [_shape_ce(r) for r in ce_ranked if _oi_int(r, "call") > 0]
    pe_leaders = [_shape_pe(r) for r in pe_ranked if _oi_int(r, "put") > 0]

    all_wall = [x["strike"] for x in ce_leaders] + [x["strike"] for x in pe_leaders]
    pin_hint = None
    if len(all_wall) >= 2:
        lo, hi = min(all_wall), max(all_wall)
        if hi > lo:
            pin_hint = (
                f"Top OI strikes span {lo}–{hi} — expiry pin often drags spot toward heavy strikes; "
                "size short premium with gaps and time to expiry in mind."
            )

    if not ce_leaders and not pe_leaders:
        st = "zero_oi"
        detail = (
            "Strikes loaded but open interest is zero on every leg (pre-open, holiday, delayed OI, or quote filter). "
            "Retry after market open."
        )
    else:
        st = "ok"
        detail = ""

    return {
        "status": st,
        "detail": detail,
        "expiry": expiry_label,
        "spot": round(spot, 2),
        "ceLeaders": ce_leaders,
        "peLeaders": pe_leaders,
        "pinRangeHint": pin_hint,
        "windowNote": OI_WINDOW_NOTE,
        "spotTrail": [],
    }
