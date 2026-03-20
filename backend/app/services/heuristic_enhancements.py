"""
Post-scoring enhancements for Multi-Heuristic Strike Selector:
- Moneyness % hard cap + DTE × moneyness matrix (eligibility + score cap)
- Spot × OI joint interpretation (multiplier)
- Volume vs OI churn dampening
- One best CE + one best PE (optional gap / single-direction)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


def _parse_optional_float_or_none(d: dict[str, Any], key: str, *, default: float) -> float | None:
    """Key absent -> default; explicit JSON null -> None (disable); number -> float."""
    if key not in d:
        return default
    v = d.get(key)
    if v is None:
        return None
    return float(v)


# Default (moneyness_bucket, dte_bucket) -> (eligible, score_cap or None)
# ineligible => signal forced off; score_cap => min(raw_score, cap) after multipliers
DEFAULT_MONEYNESS_DTE_RULES: dict[tuple[str, str], tuple[bool, float | None]] = {
    ("atm_core", "ultra"): (True, 3.5),
    ("atm_core", "short"): (True, None),
    ("atm_core", "week"): (True, None),
    ("atm_core", "far"): (True, None),
    ("near", "ultra"): (True, 3.2),
    ("near", "short"): (True, None),
    ("near", "week"): (True, None),
    ("near", "far"): (True, None),
    ("mid", "ultra"): (False, None),
    ("mid", "short"): (True, 3.4),
    ("mid", "week"): (True, None),
    ("mid", "far"): (True, None),
    ("wide", "ultra"): (False, None),
    ("wide", "short"): (False, None),
    ("wide", "week"): (True, 3.6),
    ("wide", "far"): (True, None),
}


@dataclass
class HeuristicEnhancementConfig:
    """Config from strategy_details_json.heuristicEnhancements or defaults."""

    enabled: bool = True
    # Moneyness hard filter: |K-S|/S * 100 > max_pct => skip unless score >= override_min_score
    max_moneyness_pct: float = 1.2
    moneyness_override_min_score: float = 4.5
    # Spot / OI classification bands (%)
    flat_spot_band_pct: float = 0.08
    flat_oi_pct: float = 0.5
    # Volume vs OI churn
    volume_high_ratio: float = 1.5
    oi_churn_abs_pct: float = 0.35
    churn_score_multiplier: float = 0.94
    # Decorrelation (also applied in heuristic_scorer when passing these)
    ltp_strong_pct: float = 2.0
    oi_weight_when_ltp_strong: float = 0.45
    max_ltp_oi_combined_weight_share: float | None = 0.88
    # Joint table strength (scores multiplied before cap)
    joint_min_mult: float = 0.72
    joint_max_mult: float = 1.08
    # One best per side
    best_per_side_min_gap: float = 0.35
    single_direction_only: bool = False
    single_direction_min_spread: float = 0.4
    # Optional directional gates (skip entire opt type)
    ce_requires_spot_not_down: bool = False
    pe_requires_spot_not_up: bool = False
    directional_gate_flat_band_pct: float = 0.05
    # Custom matrix entries override DEFAULT_MONEYNESS_DTE_RULES key "atm_core|ultra" -> [eligible, cap]
    matrix_overrides: dict[str, list[bool | float | None]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> HeuristicEnhancementConfig:
        if not isinstance(d, dict):
            return cls(enabled=False)
        if len(d) == 0:
            return cls(enabled=False)
        raw_overrides = d.get("moneynessDteMatrix") or d.get("matrixOverrides")
        overrides: dict[str, list[bool | float | None]] = {}
        if isinstance(raw_overrides, dict):
            for k, v in raw_overrides.items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    elig = bool(v[0])
                    cap = v[1]
                    cap_f: float | None = float(cap) if cap is not None and isinstance(cap, (int, float)) else None
                    overrides[str(k)] = [elig, cap_f]
        return cls(
            enabled=bool(d.get("enabled", True)),
            max_moneyness_pct=float(d.get("maxMoneynessPct", 1.2)),
            moneyness_override_min_score=float(d.get("moneynessOverrideMinScore", 4.5)),
            flat_spot_band_pct=float(d.get("flatSpotBandPct", 0.08)),
            flat_oi_pct=float(d.get("flatOiPct", 0.5)),
            volume_high_ratio=float(d.get("volumeHighRatio", 1.5)),
            oi_churn_abs_pct=float(d.get("oiChurnAbsPct", 0.35)),
            churn_score_multiplier=float(d.get("churnScoreMultiplier", 0.94)),
            ltp_strong_pct=float(d.get("ltpStrongPct", 2.0)),
            oi_weight_when_ltp_strong=float(d.get("oiWeightWhenLtpStrong", 0.45)),
            max_ltp_oi_combined_weight_share=_parse_optional_float_or_none(
                d, "maxLtpOiCombinedWeightShare", default=0.88
            ),
            joint_min_mult=float(d.get("jointMinMult", 0.72)),
            joint_max_mult=float(d.get("jointMaxMult", 1.08)),
            best_per_side_min_gap=float(d.get("bestPerSideMinGap", 0.35)),
            single_direction_only=bool(d.get("singleDirectionOnly", False)),
            single_direction_min_spread=float(d.get("singleDirectionMinSpread", 0.4)),
            ce_requires_spot_not_down=bool(d.get("ceRequiresSpotNotDown", False)),
            pe_requires_spot_not_up=bool(d.get("peRequiresSpotNotUp", False)),
            directional_gate_flat_band_pct=float(d.get("directionalGateFlatBandPct", 0.05)),
            matrix_overrides=overrides,
        )


def moneyness_pct_abs(strike: float, spot: float) -> float:
    """|K-S|/S * 100"""
    if spot <= 0:
        return 0.0
    return abs(float(strike) - float(spot)) / float(spot) * 100.0


def classify_moneyness_bucket(pct: float) -> str:
    if pct <= 0.25:
        return "atm_core"
    if pct <= 0.6:
        return "near"
    if pct <= 1.0:
        return "mid"
    return "wide"


def classify_dte_bucket(dte: int) -> str:
    if dte <= 1:
        return "ultra"
    if dte <= 3:
        return "short"
    if dte <= 7:
        return "week"
    return "far"


def days_to_expiry(expiry_day: date, today: date | None = None) -> int:
    t = today or date.today()
    return max(0, (expiry_day - t).days)


def _matrix_lookup(
    m_bucket: str,
    d_bucket: str,
    cfg: HeuristicEnhancementConfig,
) -> tuple[bool, float | None]:
    key = f"{m_bucket}|{d_bucket}"
    if key in cfg.matrix_overrides:
        o = cfg.matrix_overrides[key]
        cap = o[1] if len(o) > 1 else None
        cap_f = float(cap) if cap is not None and isinstance(cap, (int, float)) else None
        return bool(o[0]), cap_f
    return DEFAULT_MONEYNESS_DTE_RULES.get((m_bucket, d_bucket), (True, None))


def spot_direction(spot_chg_pct: float | None, flat_band: float) -> str:
    if spot_chg_pct is None:
        return "flat"
    v = float(spot_chg_pct)
    if v > flat_band:
        return "up"
    if v < -flat_band:
        return "down"
    return "flat"


def oi_direction(oi_chg_pct: float | None, flat_band: float) -> str:
    if oi_chg_pct is None:
        return "flat"
    v = float(oi_chg_pct)
    if v > flat_band:
        return "up"
    if v < -flat_band:
        return "down"
    return "flat"


def joint_score_multiplier(
    opt_type: str,
    spot_dir: str,
    oi_dir: str,
    cfg: HeuristicEnhancementConfig,
) -> float:
    """
    Classic long-option framing: CE prefers supportive spot; PE prefers spot down.
    Returns multiplier in [joint_min_mult, joint_max_mult].
    """
    # Tables: (spot, oi) -> raw mult in ~0.75-1.06
    ce_table: dict[tuple[str, str], float] = {
        ("up", "up"): 0.92,
        ("up", "down"): 1.04,
        ("up", "flat"): 0.96,
        ("down", "up"): 0.78,
        ("down", "down"): 0.86,
        ("down", "flat"): 0.84,
        ("flat", "up"): 0.90,
        ("flat", "down"): 0.96,
        ("flat", "flat"): 1.0,
    }
    pe_table: dict[tuple[str, str], float] = {
        ("down", "up"): 0.92,
        ("down", "down"): 1.04,
        ("down", "flat"): 0.96,
        ("up", "up"): 0.78,
        ("up", "down"): 0.86,
        ("up", "flat"): 0.84,
        ("flat", "up"): 0.90,
        ("flat", "down"): 0.96,
        ("flat", "flat"): 1.0,
    }
    t = ce_table if opt_type == "CE" else pe_table
    m = t.get((spot_dir, oi_dir), 1.0)
    lo, hi = cfg.joint_min_mult, cfg.joint_max_mult
    return max(lo, min(hi, m))


def volume_oi_multiplier(vol_ratio: float, oi_chg_pct: float | None, cfg: HeuristicEnhancementConfig) -> float:
    """High relative volume but OI barely moves => churn; dampen."""
    if oi_chg_pct is None:
        return 1.0
    if vol_ratio >= cfg.volume_high_ratio and abs(float(oi_chg_pct)) < cfg.oi_churn_abs_pct:
        return cfg.churn_score_multiplier
    return 1.0


def apply_moneyness_dte_rules(
    score: float,
    strike: float,
    spot: float,
    expiry_day: date,
    cfg: HeuristicEnhancementConfig,
    today: date | None = None,
) -> tuple[float, bool, str | None]:
    """
    Apply DTE × moneyness matrix: returns (possibly_capped_score, matrix_eligible, note).
    """
    pct = moneyness_pct_abs(strike, spot)
    m_bucket = classify_moneyness_bucket(pct)
    dte = days_to_expiry(expiry_day, today)
    d_bucket = classify_dte_bucket(dte)
    eligible, cap = _matrix_lookup(m_bucket, d_bucket, cfg)
    out = score
    note = None
    if cap is not None:
        out = min(out, cap)
        note = f"moneyness×DTE cap {cap}"
    if not eligible:
        note = f"blocked moneyness×DTE ({m_bucket},{d_bucket})"
    return (round(out, 2), eligible, note)


def passes_moneyness_hard_filter(
    moneyness_pct: float,
    score_before_filters: float,
    cfg: HeuristicEnhancementConfig,
) -> bool:
    if moneyness_pct <= cfg.max_moneyness_pct:
        return True
    return score_before_filters >= cfg.moneyness_override_min_score


def passes_directional_gate(
    opt_type: str,
    spot_chg_pct: float | None,
    cfg: HeuristicEnhancementConfig,
) -> bool:
    if spot_chg_pct is None:
        return True
    v = float(spot_chg_pct)
    b = cfg.directional_gate_flat_band_pct
    if cfg.ce_requires_spot_not_down and opt_type == "CE":
        return v >= -b
    if cfg.pe_requires_spot_not_up and opt_type == "PE":
        return v <= b
    return True


def select_best_per_side(
    recs: list[dict[str, Any]],
    cfg: HeuristicEnhancementConfig,
) -> list[dict[str, Any]]:
    """
    Keep at most one CE and one PE among rows with signal_eligible=True.
    If best_per_side_min_gap > 0, drop a side when #1 - #2 < gap (noise filter).
    If single_direction_only, keep only the stronger side when spread >= single_direction_min_spread.
    """
    if not recs:
        return []

    eligible = [r for r in recs if r.get("signal_eligible")]
    by_side: dict[str, list[dict[str, Any]]] = {"CE": [], "PE": []}
    for r in eligible:
        ot = str(r.get("option_type") or "")
        if ot in by_side:
            by_side[ot].append(r)

    out: list[dict[str, Any]] = []

    for side in ("CE", "PE"):
        rows = sorted(by_side[side], key=lambda x: float(x.get("score", 0)), reverse=True)
        if not rows:
            continue
        top = rows[0]
        if cfg.best_per_side_min_gap > 0 and len(rows) > 1:
            if float(top["score"]) - float(rows[1]["score"]) < cfg.best_per_side_min_gap:
                continue
        out.append(top)

    if cfg.single_direction_only and len(out) == 2:
        a, b = out[0], out[1]
        sa, sb = float(a["score"]), float(b["score"])
        hi, lo = (a, b) if sa >= sb else (b, a)
        if float(hi["score"]) - float(lo["score"]) >= cfg.single_direction_min_spread:
            out = [hi]

    out.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    for i, r in enumerate(out, start=1):
        r["rank_after_enhancements"] = i
    return out
