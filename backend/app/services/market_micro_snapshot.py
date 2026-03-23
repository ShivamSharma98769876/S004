"""Lightweight NIFTY + option-chain snapshot for logging and trade entry context (no routes_landing import)."""

from __future__ import annotations

import asyncio
from typing import Any

from kiteconnect import KiteConnect

from app.services.option_chain_zerodha import (
    fetch_indices_spot_sync,
    fetch_option_chain_sync,
    get_expiries_for_instrument,
)
from app.services.sentiment_engine import compute_sentiment_snapshot

_BROKER_TIMEOUT_SEC = 8.0


def pcr_to_bucket(pcr: float | None) -> str:
    if pcr is None or pcr != pcr:
        return "unknown"
    if pcr < 0.75:
        return "very_low"
    if pcr < 0.95:
        return "low"
    if pcr < 1.15:
        return "neutral"
    if pcr < 1.4:
        return "high"
    return "very_high"


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


async def build_market_context_for_log(kite: KiteConnect | None) -> dict[str, Any]:
    """NIFTY spot/chg, PCR, sentiment/regime labels, optional India VIX. Safe when kite is None."""
    nifty_spot = 0.0
    nifty_chg = 0.0
    pcr: float | None = None
    chain_payload: dict[str, Any] | None = None
    india_vix: float | None = None

    if kite:
        try:
            idx = await asyncio.wait_for(asyncio.to_thread(fetch_indices_spot_sync, kite), timeout=_BROKER_TIMEOUT_SEC)
            n = idx.get("NIFTY") or {}
            nifty_spot = float(n.get("spot") or 0)
            nifty_chg = float(n.get("spotChgPct") or 0)
        except Exception:
            pass
        try:
            ex = get_expiries_for_instrument("NIFTY")
            if ex:
                chain_payload = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_option_chain_sync,
                        kite,
                        "NIFTY",
                        ex[0],
                        3,
                        3,
                        1,
                        None,
                    ),
                    timeout=_BROKER_TIMEOUT_SEC,
                )
                if chain_payload and chain_payload.get("pcr") is not None:
                    pcr = float(chain_payload.get("pcr"))
        except Exception:
            pass
        for vix_sym in ("NSE:INDIA VIX", "NSE:NIFTY 50 VIX"):
            try:
                q = await asyncio.wait_for(asyncio.to_thread(kite.quote, vix_sym), timeout=3.0)
                data = q.get("data") if isinstance(q, dict) and "data" in q else q
                entry = (data or {}).get(vix_sym) or {}
                lp = _to_float(entry.get("last_price"))
                if lp is not None and lp > 0:
                    india_vix = lp
                    break
            except Exception:
                continue

    sentiment = compute_sentiment_snapshot(
        chain_payload=chain_payload,
        spot_chg_pct=nifty_chg,
        trendpulse_signal=None,
    )
    return {
        "nifty_spot": round(nifty_spot, 2),
        "nifty_chg_pct": round(nifty_chg, 2),
        "pcr": round(pcr, 4) if pcr is not None else None,
        "pcr_bucket": pcr_to_bucket(pcr),
        "sentiment_label": sentiment.get("sentimentLabel"),
        "regime_label": sentiment.get("regimeLabel"),
        "direction_label": sentiment.get("directionLabel"),
        "direction_confidence": sentiment.get("confidence"),
        "india_vix": round(india_vix, 2) if india_vix is not None else None,
    }


def entry_snapshot_from_rec_and_market(
    rec: dict[str, Any],
    market: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist on s004_live_trades.entry_market_snapshot for heatmaps and review."""
    m = market or {}
    score = rec.get("score")
    try:
        score_i = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_i = None
    conf = rec.get("confidence_score")
    try:
        conf_f = float(conf) if conf is not None else None
    except (TypeError, ValueError):
        conf_f = None
    return {
        "reason_code": str(rec.get("reason_code") or ""),
        "score": score_i,
        "confidence": conf_f,
        "pcr": m.get("pcr"),
        "pcr_bucket": m.get("pcr_bucket"),
        "nifty_chg_pct": m.get("nifty_chg_pct"),
        "regime_label": m.get("regime_label"),
        "india_vix": m.get("india_vix"),
    }
