"""Phase-1 sentiment engine for landing decision snapshot."""

from __future__ import annotations

from typing import Any


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sum_chain_metric(
    chain: list[dict[str, Any]],
    leg: str,
    key: str,
) -> float:
    s = 0.0
    for row in chain:
        v = _to_float((row.get(leg) or {}).get(key))
        s += v
    return s


def _mean_chain_metric(
    chain: list[dict[str, Any]],
    leg: str,
    key: str,
) -> float:
    vals = [_to_float((row.get(leg) or {}).get(key)) for row in chain]
    vals = [v for v in vals if v == v]  # strip NaN
    return (sum(vals) / len(vals)) if vals else 0.0


def _direction_label(score: float, threshold: float) -> str:
    if score >= threshold:
        return "BULLISH"
    if score <= -threshold:
        return "BEARISH"
    return "NEUTRAL"


def _regime_label(abs_spot_chg: float, confidence: int) -> str:
    if abs_spot_chg >= 1.0 and confidence >= 65:
        return "TRENDING"
    if abs_spot_chg >= 0.7:
        return "VOLATILE_EVENT"
    return "RANGE_CHOP"


def _sentiment_label(direction: str, confidence: int) -> str:
    if direction == "NEUTRAL":
        return "Balanced"
    if direction == "BULLISH":
        return "Constructive" if confidence >= 65 else "Mildly constructive"
    return "Cautious" if confidence >= 65 else "Mildly cautious"


def compute_sentiment_snapshot(
    *,
    chain_payload: dict[str, Any] | None,
    spot_chg_pct: float,
    trendpulse_signal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute direction, confidence, regime and explainable drivers from option-chain payload."""
    payload = chain_payload or {}
    chain = payload.get("chain") or []
    if not isinstance(chain, list):
        chain = []

    pcr = _to_float(payload.get("pcr"), 1.0)
    pcr_vol = _to_float(payload.get("pcrVol"), 1.0)

    ce_oi = _sum_chain_metric(chain, "call", "oi")
    pe_oi = _sum_chain_metric(chain, "put", "oi")
    ce_vol = _sum_chain_metric(chain, "call", "volume")
    pe_vol = _sum_chain_metric(chain, "put", "volume")
    ce_oi_chg = _mean_chain_metric(chain, "call", "oiChgPct")
    pe_oi_chg = _mean_chain_metric(chain, "put", "oiChgPct")
    ce_ltp_chg = _mean_chain_metric(chain, "call", "ltpChg")
    pe_ltp_chg = _mean_chain_metric(chain, "put", "ltpChg")
    ce_ivr = _mean_chain_metric(chain, "call", "ivr")
    pe_ivr = _mean_chain_metric(chain, "put", "ivr")

    # Normalized feature signals in [-1, +1] (positive means bullish).
    f_spot = _clamp(spot_chg_pct / 1.2)
    f_pcr = _clamp((1.0 - pcr) / 0.35)
    f_pcr_vol = _clamp((1.0 - pcr_vol) / 0.35)
    f_oi_imbalance = _clamp((ce_oi - pe_oi) / max(1.0, ce_oi + pe_oi))
    f_vol_imbalance = _clamp((ce_vol - pe_vol) / max(1.0, ce_vol + pe_vol))
    f_oi_momentum = _clamp((ce_oi_chg - pe_oi_chg) / 12.0)
    f_ltp_momentum = _clamp((ce_ltp_chg - pe_ltp_chg) / 8.0)
    f_ivr_spread = _clamp((pe_ivr - ce_ivr) / 40.0)

    tp_raw = trendpulse_signal or {}
    tp_cross = str(tp_raw.get("cross") or "")
    tp_ok = bool(tp_raw.get("entryEligible"))
    f_tp = 0.0
    if tp_ok and tp_cross == "bullish":
        f_tp = 1.0
    elif tp_ok and tp_cross == "bearish":
        f_tp = -1.0

    weights = {
        "spot_trend": 0.16,
        "pcr": 0.14,
        "pcr_volume": 0.10,
        "oi_balance": 0.14,
        "volume_balance": 0.10,
        "oi_momentum": 0.10,
        "ltp_momentum": 0.10,
        "ivr_spread": 0.06,
        "trendpulse_alignment": 0.10,
    }
    features = {
        "spot_trend": f_spot,
        "pcr": f_pcr,
        "pcr_volume": f_pcr_vol,
        "oi_balance": f_oi_imbalance,
        "volume_balance": f_vol_imbalance,
        "oi_momentum": f_oi_momentum,
        "ltp_momentum": f_ltp_momentum,
        "ivr_spread": f_ivr_spread,
        "trendpulse_alignment": f_tp,
    }

    contributions = {k: weights[k] * features[k] for k in weights}
    raw_score = sum(contributions.values())  # [-1,+1] approx
    direction_score = round(_clamp(raw_score) * 100.0, 2)

    non_trivial = [v for v in features.values() if abs(v) >= 0.15]
    agreement = (sum(1 for v in non_trivial if v * raw_score > 0) / len(non_trivial)) if non_trivial else 0.5
    magnitude = min(1.0, abs(raw_score) * 1.7)
    quality = 1.0 if len(chain) >= 10 else 0.75 if len(chain) >= 6 else 0.55
    confidence = int(round(100.0 * magnitude * (0.45 + 0.55 * agreement) * quality))
    confidence = max(5, min(99, confidence))

    threshold = 28.0 if abs(spot_chg_pct) < 0.7 else 22.0
    direction = _direction_label(direction_score, threshold)
    regime = _regime_label(abs(spot_chg_pct), confidence)
    sentiment = _sentiment_label(direction, confidence)

    labels = {
        "spot_trend": "Spot trend",
        "pcr": "PCR",
        "pcr_volume": "PCR volume",
        "oi_balance": "OI balance",
        "volume_balance": "Volume balance",
        "oi_momentum": "OI momentum",
        "ltp_momentum": "Option LTP momentum",
        "ivr_spread": "IVR spread",
        "trendpulse_alignment": "TrendPulse alignment",
    }
    ranked = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)[:4]
    drivers = [
        {
            "key": k,
            "label": labels[k],
            "direction": "bullish" if v >= 0 else "bearish",
            "impact": round(v * 100.0, 2),
            "value": round(features[k], 4),
        }
        for k, v in ranked
    ]

    alert_items: list[str] = []
    if direction != "NEUTRAL" and confidence >= 65:
        alert_items.append(f"{direction} bias with {confidence}% confidence.")
    if tp_ok:
        alert_items.append(f"TrendPulse entry is active ({tp_cross}).")
    if regime == "VOLATILE_EVENT":
        alert_items.append("High-volatility regime: consider tighter risk controls.")
    if not alert_items:
        alert_items.append("No high-conviction directional alert right now.")

    return {
        "sentimentLabel": sentiment,
        "directionLabel": direction,
        "directionScore": direction_score,
        "confidence": confidence,
        "regime": regime,
        "drivers": drivers,
        "alerts": alert_items,
        "inputs": {
            "pcr": round(pcr, 3),
            "pcrVol": round(pcr_vol, 3),
            "spotChgPct": round(spot_chg_pct, 3),
            "ceOi": int(round(ce_oi)),
            "peOi": int(round(pe_oi)),
            "ceVol": int(round(ce_vol)),
            "peVol": int(round(pe_vol)),
        },
    }
