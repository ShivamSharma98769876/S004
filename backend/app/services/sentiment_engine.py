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


def _ternary_skew(score: float, *, thr: float) -> str:
    if score > thr:
        return "CE"
    if score < -thr:
        return "PE"
    return "EVEN"


def _build_options_intel(
    *,
    direction: str,
    regime: str,
    chain_len: int,
    direction_score: float,
    ce_oi: float,
    pe_oi: float,
    ce_vol: float,
    pe_vol: float,
    f_pcr: float,
    f_pcr_vol: float,
    f_oi_imbalance: float,
    f_vol_imbalance: float,
    f_oi_momentum: float,
    f_ltp_momentum: float,
) -> dict[str, Any]:
    """CE vs PE read: option features, PCR-heavy when few strikes, blended with index direction score when flow is flat."""
    oi_tot = ce_oi + pe_oi
    vol_tot = ce_vol + pe_vol
    ce_oi_pct = round(100.0 * ce_oi / oi_tot, 1) if oi_tot > 0 else None
    pe_oi_pct = round(100.0 * pe_oi / oi_tot, 1) if oi_tot > 0 else None

    oi_thr = 0.03  # ~3% imbalance of total OI
    oi_skew_raw = (ce_oi - pe_oi) / oi_tot if oi_tot > 0 else 0.0
    vol_skew_raw = (ce_vol - pe_vol) / vol_tot if vol_tot > 0 else 0.0
    oi_dominant = _ternary_skew(oi_skew_raw, thr=oi_thr)
    vol_dominant = _ternary_skew(vol_skew_raw, thr=oi_thr)

    opt_feats = [f_pcr, f_pcr_vol, f_oi_imbalance, f_vol_imbalance, f_oi_momentum, f_ltp_momentum]
    opt_avg = sum(opt_feats) / len(opt_feats)
    pcr_pair = (f_pcr + f_pcr_vol) / 2.0
    has_grid = chain_len >= 3
    # Few strikes: OI/vol legs are noisy — lean on PCR; still fold in full opt_avg weakly.
    options_only = opt_avg if has_grid else (0.78 * pcr_pair + 0.22 * opt_avg)

    idx_norm = _clamp(direction_score / 100.0)
    # When option-only signal is tiny, let index direction score inform the wing (so UI is not stuck on NEUTRAL).
    blend = float(options_only)
    if (not has_grid) or abs(blend) < 0.04:
        blend = 0.4 * blend + 0.6 * idx_norm
    blend = _clamp(blend)

    thr = 0.065 if has_grid else 0.055
    tilt = "NEUTRAL"
    if blend > thr:
        tilt = "CE"
    elif blend < -thr:
        tilt = "PE"

    if tilt == "CE":
        wing = "Calls (CE) favored"
        skew_line = "Flow composite favors the call (CE) wing versus puts for this index snapshot."
    elif tilt == "PE":
        wing = "Puts (PE) favored"
        skew_line = "Flow composite favors the put (PE) wing versus calls (hedging or bearish positioning)."
    else:
        wing = "CE / PE balanced"
        skew_line = "CE and PE pressure is balanced in the blended read (options + index tone)."

    data_caveat = ""
    if not has_grid:
        data_caveat = (
            "Strike grid is limited: PCR, PCR volume, and NIFTY direction fill in when per-strike OI is thin."
        )

    if tilt == "NEUTRAL" and direction == "BULLISH":
        skew_line = "Options are flat on skew, but the index model is constructive — watch for CE participation to confirm."
    elif tilt == "NEUTRAL" and direction == "BEARISH":
        skew_line = "Options skew is flat while the index read is soft — PE activity may pick up if spot weakens."

    # Playbook: concrete structures, not guaranteed signals.
    playbook_head: str
    playbook_detail: str
    if direction == "BULLISH":
        if tilt == "CE":
            playbook_head = "Playbook: debit CE or bull call spread (defined risk)."
            playbook_detail = "Aligned bullish index + CE-leaning flow; keep size small if regime is volatile."
        elif tilt == "PE":
            playbook_head = "Playbook: favor hedged CE (spreads) over naked long PE against the trend."
            playbook_detail = "Upside index read but put-heavy chain — often hedging; avoid fighting spot with naked long puts."
        else:
            playbook_head = "Playbook: iron fly / condor or small trial CE if scalping upside."
            playbook_detail = "No clean skew — use neutral structures or light directional risk only."
    elif direction == "BEARISH":
        if tilt == "PE":
            playbook_head = "Playbook: debit PE or bear put spread (defined risk)."
            playbook_detail = "Soft index read with PE-leaning flow supports bearish option structures; mind IV spikes."
        elif tilt == "CE":
            playbook_head = "Playbook: bearish spreads or hedged shorts — calls still show dip demand."
            playbook_detail = "Bearish score but CE interest present; undefined short calls are fragile."
        else:
            playbook_head = "Playbook: put spreads or cautious hedges until CE/PE picks a side."
            playbook_detail = "Mixed tape — avoid oversized naked exposure either way."
    else:
        if tilt == "CE":
            playbook_head = "Playbook: tactical long CE or call spreads for upside pops only."
            playbook_detail = "Neutral index with CE skew — quick scalps, not full conviction swings."
        elif tilt == "PE":
            playbook_head = "Playbook: protective PE or short-vol spreads if range-bound."
            playbook_detail = "Neutral index with PE skew — hedges or fade extremes with defined risk."
        else:
            playbook_head = "Playbook: non-directional (strangles/condors) or stay flat."
            playbook_detail = "No dominant wing — wait for PCR/OI to diverge or direction to break."

    sug = f"{playbook_detail}"
    if regime == "VOLATILE_EVENT":
        sug += " Volatile session: reduce size and assume wider gaps."

    ce_strength = int(round(_clamp(50.0 + 50.0 * blend, 0.0, 100.0)))
    pe_strength = 100 - ce_strength

    return {
        "hasChainData": has_grid,
        "ceOiPct": ce_oi_pct,
        "peOiPct": pe_oi_pct,
        "oiDominant": oi_dominant,
        "volDominant": vol_dominant,
        "modelOptionTilt": tilt,
        "optionFlowScore": round(opt_avg, 4),
        "flowBlendScore": round(blend, 4),
        "optionsOnlyScore": round(float(options_only), 4),
        "bullishWingLabel": wing,
        "headline": skew_line,
        "dataCaveat": data_caveat,
        "playbookHeadline": playbook_head,
        "playbookDetail": playbook_detail,
        "ceStrengthPct": ce_strength,
        "peStrengthPct": pe_strength,
        "suggestion": sug,
    }


def _driver_reading(
    key: str,
    *,
    spot_chg_pct: float,
    pcr: float,
    pcr_vol: float,
    ce_oi: float,
    pe_oi: float,
    ce_vol: float,
    pe_vol: float,
    ce_oi_chg: float,
    pe_oi_chg: float,
    ce_ltp_chg: float,
    pe_ltp_chg: float,
    ce_ivr: float,
    pe_ivr: float,
    tp_ok: bool,
    tp_cross: str,
) -> str:
    """Human-readable raw input; the main number on the widget is weighted *impact*, not this."""
    if key == "spot_trend":
        return f"NIFTY day {spot_chg_pct:+.2f}%"
    if key == "pcr":
        return f"put/call OI ratio {pcr:.2f} (1.0 ≈ balanced)"
    if key == "pcr_volume":
        return f"put/call vol ratio {pcr_vol:.2f}"
    if key == "oi_balance":
        tot = ce_oi + pe_oi
        if tot <= 0:
            return "no per-strike OI in window — ratio neutral"
        return f"CE OI {int(round(ce_oi)):,} · PE OI {int(round(pe_oi)):,}"
    if key == "volume_balance":
        tot = ce_vol + pe_vol
        if tot <= 0:
            return "no volume in window"
        return f"CE vol {int(round(ce_vol)):,} · PE vol {int(round(pe_vol)):,}"
    if key == "oi_momentum":
        return f"avg OI chg CE {ce_oi_chg:+.1f}% vs PE {pe_oi_chg:+.1f}%"
    if key == "ltp_momentum":
        return f"avg LTP chg CE {ce_ltp_chg:+.2f} vs PE {pe_ltp_chg:+.2f}"
    if key == "ivr_spread":
        return f"avg IVR CE {ce_ivr:.1f} vs PE {pe_ivr:.1f}"
    if key == "trendpulse_alignment":
        if tp_ok:
            return f"entry on · cross {tp_cross or '—'}"
        return "no active TrendPulse entry"
    return ""


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
            "reading": _driver_reading(
                k,
                spot_chg_pct=spot_chg_pct,
                pcr=pcr,
                pcr_vol=pcr_vol,
                ce_oi=ce_oi,
                pe_oi=pe_oi,
                ce_vol=ce_vol,
                pe_vol=pe_vol,
                ce_oi_chg=ce_oi_chg,
                pe_oi_chg=pe_oi_chg,
                ce_ltp_chg=ce_ltp_chg,
                pe_ltp_chg=pe_ltp_chg,
                ce_ivr=ce_ivr,
                pe_ivr=pe_ivr,
                tp_ok=tp_ok,
                tp_cross=tp_cross,
            ),
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

    options_intel = _build_options_intel(
        direction=direction,
        regime=regime,
        chain_len=len(chain),
        direction_score=direction_score,
        ce_oi=ce_oi,
        pe_oi=pe_oi,
        ce_vol=ce_vol,
        pe_vol=pe_vol,
        f_pcr=f_pcr,
        f_pcr_vol=f_pcr_vol,
        f_oi_imbalance=f_oi_imbalance,
        f_vol_imbalance=f_vol_imbalance,
        f_oi_momentum=f_oi_momentum,
        f_ltp_momentum=f_ltp_momentum,
    )

    return {
        "sentimentLabel": sentiment,
        "directionLabel": direction,
        "directionScore": direction_score,
        "confidence": confidence,
        "regime": regime,
        "drivers": drivers,
        "alerts": alert_items,
        "optionsIntel": options_intel,
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
