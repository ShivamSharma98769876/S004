"""Multi-heuristic scoring for option chain strikes. Each heuristic scores 1-5; weighted average produces final score."""

from __future__ import annotations

from typing import Any

DEFAULT_HEURISTICS = {
    "oiBuildup": {"enabled": True, "weight": 1.2},
    "ivr": {"enabled": True, "weight": 1.0},
    "volumeSpike": {"enabled": True, "weight": 1.0},
    "rsi": {"enabled": True, "weight": 0.8},
    "emaAlignment": {"enabled": True, "weight": 0.9},
    "primaryVwap": {"enabled": True, "weight": 1.0},
    "deltaFit": {"enabled": True, "weight": 0.8},
    "oiChange": {"enabled": True, "weight": 0.7},
    "ltpChange": {"enabled": True, "weight": 0.6},
}


def _score_oi_buildup(buildup: str, opt_type: str) -> tuple[float, str | None]:
    """Long Buildup / Short Covering = bullish for CE, bearish for PE. Score 1-5."""
    if not buildup or buildup == "—":
        return (2.5, None)
    bullish = buildup in ("Long Buildup", "Short Covering")
    bearish = buildup in ("Short Buildup", "Long Unwinding")
    if opt_type == "CE" and bullish:
        return (5.0, f"{buildup} (bullish)")
    if opt_type == "CE" and bearish:
        return (1.0, None)
    if opt_type == "PE" and bearish:
        return (5.0, f"{buildup} (bearish)")
    if opt_type == "PE" and bullish:
        return (1.0, None)
    return (3.0, None)


def _score_ivr(ivr: float | None) -> tuple[float, str | None]:
    """Low IVR = cheap premium. IVR 0-100 within chain."""
    if ivr is None:
        return (3.0, None)
    v = float(ivr)
    if v < 20:
        return (5.0, f"IVR {v:.0f} (cheap)")
    if v < 40:
        return (4.0, f"IVR {v:.0f}")
    if v < 60:
        return (3.0, None)
    if v < 80:
        return (2.0, None)
    return (1.0, None)


def _score_volume_spike(vol_ratio: float) -> tuple[float, str | None]:
    """Volume spike vs average. >2x=5, 1.5-2=4, 1.2-1.5=3, 1-1.2=2, <1=1."""
    if vol_ratio <= 0:
        return (1.0, None)
    if vol_ratio >= 2.0:
        return (5.0, f"Vol {vol_ratio:.1f}x")
    if vol_ratio >= 1.5:
        return (4.0, f"Vol {vol_ratio:.1f}x")
    if vol_ratio >= 1.2:
        return (3.0, f"Vol {vol_ratio:.1f}x")
    if vol_ratio >= 1.0:
        return (2.0, None)
    return (1.0, None)


def _score_rsi(rsi: float | None, rsi_min: float = 45, rsi_max: float = 75) -> tuple[float, str | None]:
    """RSI in zone = higher. 45-75 ideal for momentum."""
    if rsi is None:
        return (3.0, None)
    v = float(rsi)
    if rsi_min <= v <= rsi_max:
        return (5.0, f"RSI {v:.0f}")
    if 40 <= v < rsi_min or rsi_max < v <= 80:
        return (3.0, None)
    return (1.5, None)


def _score_ema_alignment(ema9: float, ema21: float) -> tuple[float, str | None]:
    """EMA9 > EMA21 = bullish momentum."""
    if ema9 > ema21:
        return (5.0, "EMA9>EMA21")
    if ema9 == ema21:
        return (3.0, None)
    return (1.0, None)


def _score_primary_vwap(ltp: float, vwap: float, opt_type: str) -> tuple[float, str | None]:
    """CE: LTP > VWAP. PE: LTP < VWAP (or at least not far above). Simplified: primaryOk-like."""
    if vwap <= 0:
        return (3.0, None)
    if opt_type == "CE":
        ok = ltp >= vwap
    else:
        ok = ltp <= vwap
    return (5.0, "Above VWAP") if ok else (2.0, None)


def _score_delta_fit(delta: float, opt_type: str, target_ce: float = 0.35, target_pe: float = -0.35) -> tuple[float, str | None]:
    """CE: delta 0.30-0.45. PE: delta -0.45 to -0.30. Closer = higher."""
    target = target_ce if opt_type == "CE" else target_pe
    dist = abs(delta - target)
    if dist <= 0.05:
        return (5.0, f"Delta {delta:.2f}")
    if dist <= 0.10:
        return (4.0, f"Delta {delta:.2f}")
    if dist <= 0.15:
        return (3.0, None)
    if dist <= 0.25:
        return (2.0, None)
    return (1.0, None)


def _score_oi_change(oi_chg_pct: float | None) -> tuple[float, str | None]:
    """Positive OI change = interest. >5%=5, 2-5%=4, 0-2%=3, neg=lower."""
    if oi_chg_pct is None:
        return (3.0, None)
    v = float(oi_chg_pct)
    if v >= 10:
        return (5.0, f"OI {v:+.1f}%")
    if v >= 5:
        return (4.5, f"OI {v:+.1f}%")
    if v >= 2:
        return (4.0, f"OI {v:+.1f}%")
    if v >= 0:
        return (3.0, None)
    if v >= -5:
        return (2.0, None)
    return (1.0, None)


def _score_ltp_change(ltp_chg: float | None) -> tuple[float, str | None]:
    """LTP change %. Positive = price moving. Used as momentum cue."""
    if ltp_chg is None:
        return (3.0, None)
    v = float(ltp_chg)
    if v >= 5:
        return (5.0, f"LTP {v:+.1f}%")
    if v >= 2:
        return (4.0, f"LTP {v:+.1f}%")
    if v >= 0:
        return (3.0, None)
    if v >= -3:
        return (2.0, None)
    return (1.5, None)


def score_leg(
    leg: dict[str, Any],
    opt_type: str,
    strike: int,
    atm_strike: int,
    chain_context: dict[str, Any],
    heuristics_config: dict[str, dict] | None = None,
    *,
    delta_ce: float = 0.35,
    delta_pe: float = -0.35,
    rsi_min: float = 45,
    rsi_max: float = 75,
    ltp_strong_pct: float | None = None,
    oi_weight_when_ltp_strong: float | None = None,
    max_ltp_oi_combined_weight_share: float | None = None,
) -> tuple[float, list[str]]:
    """
    Score a single leg (call or put) using all enabled heuristics.
    Returns (weighted_score, list of reason strings).

    When ltp_strong_pct and oi_weight_when_ltp_strong are set and |LTP change| >= strong threshold,
    the oiChange heuristic weight is multiplied by oi_weight_when_ltp_strong (reduces double-counting
    with ltpChange). If max_ltp_oi_combined_weight_share is set (0-1), caps the fraction of total weight
    coming from ltpChange+oiChange combined by scaling those two weights down proportionally.
    """
    config = heuristics_config or DEFAULT_HEURISTICS
    scores: list[tuple[str, float, float]] = []  # (key, score, weight)
    reasons: list[str] = []

    buildup = str(leg.get("buildup") or "—")
    ivr = leg.get("ivr")
    vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
    rsi = leg.get("rsi")
    ema9 = float(leg.get("ema9") or 0.0)
    ema21 = float(leg.get("ema21") or 0.0)
    ltp = float(leg.get("ltp") or 0.0)
    vwap = float(leg.get("vwap") or ltp)
    delta = float(leg.get("delta") or 0.0)
    oi_chg = leg.get("oiChgPct")
    ltp_chg = leg.get("ltpChg")

    for key, cfg in config.items():
        if not isinstance(cfg, dict) or not cfg.get("enabled", True):
            continue
        w = float(cfg.get("weight", 1.0))

        if key == "oiBuildup":
            s, r = _score_oi_buildup(buildup, opt_type)
        elif key == "ivr":
            s, r = _score_ivr(ivr)
        elif key == "volumeSpike":
            s, r = _score_volume_spike(vol_ratio)
        elif key == "rsi":
            s, r = _score_rsi(rsi, rsi_min, rsi_max)
        elif key == "emaAlignment":
            s, r = _score_ema_alignment(ema9, ema21)
        elif key == "primaryVwap":
            s, r = _score_primary_vwap(ltp, vwap, opt_type)
        elif key == "deltaFit":
            s, r = _score_delta_fit(delta, opt_type, delta_ce, delta_pe)
        elif key == "oiChange":
            s, r = _score_oi_change(oi_chg)
        elif key == "ltpChange":
            s, r = _score_ltp_change(ltp_chg)
        else:
            continue

        if (
            key == "oiChange"
            and ltp_strong_pct is not None
            and oi_weight_when_ltp_strong is not None
            and ltp_chg is not None
            and abs(float(ltp_chg)) >= float(ltp_strong_pct)
            and config.get("ltpChange", {}).get("enabled", True)
        ):
            w *= float(oi_weight_when_ltp_strong)

        scores.append((key, s, w))
        if r:
            reasons.append(r)

    if not scores:
        return (3.0, [])

    # Cap combined weight share of ltpChange + oiChange
    if max_ltp_oi_combined_weight_share is not None and 0 < max_ltp_oi_combined_weight_share < 1:
        keys_set = {t[0] for t in scores}
        if "ltpChange" in keys_set and "oiChange" in keys_set:
            other_w = sum(t[2] for t in scores if t[0] not in ("ltpChange", "oiChange"))
            ltp_w = sum(t[2] for t in scores if t[0] == "ltpChange")
            oi_w = sum(t[2] for t in scores if t[0] == "oiChange")
            pair = ltp_w + oi_w
            max_pair = max_ltp_oi_combined_weight_share * (other_w + pair)
            if pair > max_pair > 0:
                scale = max_pair / pair
                scores = [
                    (k, s, w * scale) if k in ("ltpChange", "oiChange") else (k, s, w)
                    for k, s, w in scores
                ]

    total_w = sum(t[2] for t in scores)
    if total_w <= 0:
        return (3.0, reasons)
    weighted = sum(s * w for _, s, w in scores) / total_w
    return (round(weighted, 2), reasons)
