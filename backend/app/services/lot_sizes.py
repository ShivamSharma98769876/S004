"""Resolve F&O contract multiplier (lot size) per underlying from user settings."""

from __future__ import annotations


def contract_multiplier_for_trade(
    *,
    strategy_id: str | None = None,
    symbol: str | None = None,
    instrument: str | None = None,
    nifty_lot: int = 65,
    banknifty_lot: int = 30,
) -> int:
    """
    NIFTY strategies use ``nifty_lot`` (default 65). Bank Nifty (StochasticBNF, BANKNIFTY chain)
    uses ``banknifty_lot`` (NSE contract size — set in Settings).
    """
    ins = (instrument or "").strip().upper()
    if ins == "BANKNIFTY":
        return max(1, int(banknifty_lot))
    sid = (strategy_id or "").strip().lower()
    if sid in ("strat-stochastic-bnf", "strat-ps-vs-mtf"):
        return max(1, int(banknifty_lot))
    sym = (symbol or "").strip().upper()
    if "BANKNIFTY" in sym:
        return max(1, int(banknifty_lot))
    return max(1, int(nifty_lot))
