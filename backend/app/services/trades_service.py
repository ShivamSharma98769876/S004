from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from kiteconnect import KiteConnect

from app.db_client import execute, fetch, fetchrow
from app.services.platform_risk import (
    evaluate_trade_entry_allowed,
    evaluate_user_daily_pnl_limits,
    get_platform_trading_paused,
)
from app.services.heuristic_scorer import score_leg as heuristic_score_leg
from app.services.heuristic_enhancements import (
    DEFAULT_HEURISTIC_ENHANCEMENTS,
    HeuristicEnhancementConfig,
    apply_moneyness_dte_rules,
    joint_score_multiplier,
    moneyness_pct_abs,
    oi_direction,
    passes_directional_gate,
    passes_moneyness_hard_filter,
    select_best_per_side,
    spot_direction,
    volume_oi_multiplier,
)
from app.services.option_greeks import compute_gamma_from_ltp
from app.services.option_chain_zerodha import (
    _vix_from_quote,
    fetch_index_candles_sync,
    fetch_option_chain_sync,
    pick_expiry_with_min_calendar_dte,
    pick_primary_expiry_str,
    resolve_expiry_min_dte_weekday_with_fallback,
    verify_kite_session_sync,
)
from app.services.trendpulse_phase3 import (
    apply_trendpulse_hard_gates,
    parse_nifty_weekly_expiry_weekday,
    resolve_trendpulse_z_config,
)
from app.services.trendpulse_tier2 import (
    delta_abs_in_band,
    option_extrinsic_share,
    trendpulse_opening_window_blocked,
)
from app.services.trendpulse_z import evaluate_trendpulse_signal
from app.services.market_micro_snapshot import build_market_context_for_log, entry_snapshot_from_rec_and_market
from app.services.evaluation_log import append_evaluation_event
from app.services.sentiment_engine import compute_sentiment_snapshot

_logger = logging.getLogger(__name__)
_EVAL_IST = ZoneInfo("Asia/Kolkata")


def _normalize_sorted_expiries(expiries: list[Any]) -> list[str]:
    parsed: list[tuple[date, str]] = []
    seen: set[str] = set()
    for raw in expiries:
        label = str(raw or "").strip().upper()
        if not label:
            continue
        try:
            exp_day = datetime.strptime(label, "%d%b%Y").date()
        except ValueError:
            continue
        if label in seen:
            continue
        seen.add(label)
        parsed.append((exp_day, label))
    parsed.sort(key=lambda x: x[0])
    return [lbl for _, lbl in parsed]


def _pick_expiry_from_provider_list(
    expiries: list[Any],
    *,
    min_dte_calendar_days: int,
    nifty_weekly_expiry_weekday: int | None,
) -> str | None:
    normalized = _normalize_sorted_expiries(expiries)
    if not normalized:
        return None
    if int(min_dte_calendar_days) <= 0:
        return normalized[0]
    try:
        wd = int(nifty_weekly_expiry_weekday) if nifty_weekly_expiry_weekday is not None else None
    except (TypeError, ValueError):
        wd = None
    today_ist = datetime.now(_EVAL_IST).date()
    return resolve_expiry_min_dte_weekday_with_fallback(
        normalized,
        today_ist,
        min_dte_days=int(min_dte_calendar_days),
        weekday=wd,
    )


def _slim_candidate_for_evaluation_log(r: dict[str, Any]) -> dict[str, Any]:
    """Compact row for evaluation log (full chain scan before min-gamma cap, or persist list)."""
    return {
        "symbol": r.get("symbol"),
        "instrument": r.get("instrument"),
        "expiry": r.get("expiry"),
        "side": r.get("side"),
        "option_type": r.get("option_type"),
        "distance_to_atm": r.get("distance_to_atm"),
        "strike": r.get("strike"),
        "signal_eligible": r.get("signal_eligible"),
        "score": r.get("score"),
        "confidence_score": r.get("confidence_score"),
        "failed_conditions": r.get("failed_conditions"),
        "delta": r.get("delta"),
        "gamma": r.get("gamma"),
        "ivr": r.get("ivr"),
        "oi": r.get("oi"),
        "volume_spike_ratio": r.get("volume_spike_ratio"),
        "entry_price": r.get("entry_price"),
        "ema9": r.get("ema9"),
        "ema21": r.get("ema21"),
        "vwap": r.get("vwap"),
        "rsi": r.get("rsi"),
        "buildup": r.get("buildup"),
        "flow_rank_score": r.get("flow_rank_score"),
        "flow_pin_penalized": r.get("flow_pin_penalized"),
    }


def _emit_evaluation_snapshot(
    *,
    trigger_user_id: int,
    strategy_id: str,
    strategy_version: str,
    strategy_type: str,
    subscribed_user_ids: list[int],
    score_params: dict[str, Any],
    fetch_failed: bool,
    error: str | None,
    generated_rows: list[dict[str, Any]],
    scanned_candidates: list[dict[str, Any]] | None = None,
    chain_snapshot: dict[str, Any] | None = None,
) -> None:
    """Optional human-readable .log under S004_EVALUATION_LOG_DIR — see evaluation_log module."""
    rows = generated_rows or []
    scan = scanned_candidates if scanned_candidates is not None else rows
    eligible = sum(1 for r in rows if r.get("signal_eligible"))
    fc_samples: list[str] = []
    for r in rows:
        msg = str(r.get("failed_conditions") or "")
        if msg and msg != "PASS" and msg not in fc_samples:
            fc_samples.append(msg[:500])
        if len(fc_samples) >= 5:
            break
    max_c = int(os.getenv("S004_EVALUATION_LOG_MAX_CANDIDATES", "0") or "0")
    slim_scan = [_slim_candidate_for_evaluation_log(r) for r in scan]
    truncated = False
    if max_c > 0 and len(slim_scan) > max_c:
        slim_scan = slim_scan[:max_c]
        truncated = True
    event: dict[str, Any] = {
        "ts_ist": datetime.now(_EVAL_IST).isoformat(),
        "trigger_user_id": trigger_user_id,
        "subscribed_user_ids": subscribed_user_ids,
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "strategy_type": strategy_type,
        "fetch_failed": fetch_failed,
        "error": error,
        "candidate_count": len(rows),
        "scanned_candidate_count": len(scan),
        "eligible_count": eligible,
        "score_threshold": score_params.get("score_threshold"),
        "score_max": score_params.get("score_max"),
        "adx_min_threshold": score_params.get("adx_min_threshold"),
        "rsi_min": score_params.get("rsi_min"),
        "rsi_max": score_params.get("rsi_max"),
        "volume_min_ratio": score_params.get("volume_min_ratio"),
        "auto_trade_score_threshold": score_params.get("auto_trade_score_threshold"),
        "position_intent": score_params.get("position_intent"),
        "include_ema_crossover_in_score": score_params.get("include_ema_crossover_in_score"),
        "strict_bullish_comparisons": score_params.get("strict_bullish_comparisons"),
        "top_symbol": (rows[0].get("symbol") if rows else None),
        "failed_conditions_sample": fc_samples,
        "candidates": slim_scan,
        "candidates_truncated": truncated,
        "chain_snapshot": chain_snapshot or {},
    }
    uids: list[int] = sorted({int(trigger_user_id), *[int(x) for x in subscribed_user_ids]})
    append_evaluation_event(event, user_ids=uids)


async def _get_kite_for_user(user_id: int) -> KiteConnect | None:
    """User-scoped Zerodha Kite only (no server env fallback)."""
    from app.services import broker_accounts as ba

    return await ba.user_zerodha_kite(user_id, env_fallback=False)


async def _get_kite_for_any_user() -> KiteConnect | None:
    """Platform shared Zerodha only (admin paper pool)."""
    from app.services import broker_accounts as ba

    return await ba.platform_shared_zerodha_kite()


async def get_kite_for_quotes_with_source(user_id: int) -> tuple[KiteConnect | None, str]:
    """Compatibility helper: returns Kite client only when resolved provider is Zerodha."""
    from app.services.broker_runtime import ZerodhaProvider, resolve_broker_context

    ctx = await resolve_broker_context(user_id, mode="PAPER")
    provider = ctx.market_data
    if isinstance(provider, ZerodhaProvider):
        return provider.kite, ctx.source
    return None, ctx.source


async def get_kite_for_quotes(user_id: int) -> KiteConnect | None:
    """Quotes/LTP: user's Zerodha vault; else admin platform-shared Zerodha; no env/pool default."""
    kite, _ = await get_kite_for_quotes_with_source(user_id)
    return kite


_REC_DETAILS_CACHE: dict[int, dict[str, dict]] = {}


def _enrich_recommendation_item_from_storage(item: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Merge UI fields (EMA, VWAP, etc.) from in-memory cache, else from persisted details_json."""
    raw = item.pop("details_json", None)
    from_db: dict[str, Any] = {}
    if isinstance(raw, dict):
        from_db = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                from_db = parsed
        except json.JSONDecodeError:
            pass
    cache = _REC_DETAILS_CACHE.get(user_id, {})
    rid = item.get("recommendation_id")
    cached = cache.get(rid, {}) if rid else {}
    # DB details_json is source of truth; cache only overrides overlapping keys (avoids stale/partial cache hiding EMA/VWAP).
    merged: dict[str, Any] = dict(from_db)
    if cached:
        merged.update(cached)
    if merged:
        item.update(merged)
    return item
_REC_CACHE_TS: dict[int, float] = {}
# Serialize ensure_recommendations per user so 20s UI polls cannot overlap a slow chain fetch (Kite 429 / zero rows / cleared GENERATED).
_REC_ENSURE_LOCKS: dict[int, asyncio.Lock] = {}


def _ensure_lock_for(user_id: int) -> asyncio.Lock:
    lk = _REC_ENSURE_LOCKS.get(user_id)
    if lk is None:
        lk = asyncio.Lock()
        _REC_ENSURE_LOCKS[user_id] = lk
    return lk


# Chain/regen cadence: UI copy, API refresh_interval_sec, ensure_recommendations throttle, and auto-execute poll (main.py).
RECOMMENDATION_ENGINE_REFRESH_SEC = 20


def invalidate_recommendation_cache(user_id: int) -> None:
    """Clear recommendation cache for user so next run uses fresh strategy params from saved settings."""
    _REC_CACHE_TS.pop(user_id, None)
    _REC_DETAILS_CACHE.pop(user_id, None)


def _stable_recommendation_id(
    user_id: int,
    strategy_id: str,
    strategy_version: str,
    symbol: str,
    side: str,
) -> str:
    """Deterministic id per (user, strategy, symbol, side). Rank is not part of the key so re-sorting does not rotate ids."""
    sym = str(symbol or "").strip().upper()
    sd = str(side or "").strip().upper()
    key = f"{user_id}|{strategy_id}|{strategy_version}|{sym}|{sd}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"rec-{h}"


async def invalidate_recommendation_cache_for_strategy(strategy_id: str, strategy_version: str) -> None:
    """Clear recommendation cache for all users subscribed to this strategy. Call after Marketplace strategy details update."""
    user_ids = await _get_subscribed_user_ids(strategy_id, strategy_version)
    for uid in user_ids:
        invalidate_recommendation_cache(uid)


def _expiry_code(expiry_str: str) -> str:
    try:
        d = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y")
        yy = d.year % 100
        month = d.month
        dd = d.day
        return f"{yy:02d}{month}{dd:02d}"
    except ValueError:
        return "00000"


def _compact_option_symbol(instrument: str, expiry_str: str, strike: int, opt_type: str) -> str:
    return f"{instrument.upper()}{_expiry_code(expiry_str)}{int(strike)}{opt_type.upper()}"


def _leg_iv_optional(leg: dict[str, Any]) -> float | None:
    v = leg.get("ivr")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _failed_conditions(
    primary_ok: bool,
    ema_ok: bool,
    rsi_ok: bool,
    *,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_ok: bool | None = None,
    volume_min_ratio: float | None = None,
) -> str:
    """Build human-readable list of failed conditions. Uses actual strategy thresholds. Crossover is not shown."""
    failed: list[str] = []
    if not primary_ok:
        failed.append("Premium below VWAP")
    if not ema_ok:
        failed.append("EMA not bullish (9≤21)")
    if not rsi_ok:
        failed.append(f"RSI outside {rsi_min:.0f}–{rsi_max:.0f}")
    if volume_ok is False and volume_min_ratio is not None:
        failed.append(f"Vol < {float(volume_min_ratio):.2f}× avg")
    return "PASS" if not failed else "; ".join(failed)


def _failed_conditions_short_leg(
    primary_ok: bool,
    ema_ok: bool,
    rsi_ok: bool,
    *,
    rsi_min: float = 50,
    rsi_max: float = 75,
    leg_score_mode: str = "",
    rsi_below_for_weak: float = 50.0,
    rsi_direct_band: bool = False,
    rsi_require_decreasing: bool = False,
    rsi_zone_or_reversal: bool = False,
    rsi_reversal_falling_bars: int = 0,
    rsi_soft_zone_low: float = 20.0,
    rsi_soft_zone_high: float = 45.0,
    rsi_reversal_from_rsi: float = 70.0,
) -> str:
    """Bearish-style checks on option LTP series (short premium): LTP below VWAP, EMA9 below EMA21, RSI rule per leg score mode."""
    failed: list[str] = []
    if not primary_ok:
        failed.append("LTP not below VWAP (want premium weakness)")
    if not ema_ok:
        failed.append("EMA9 not below EMA21 (want premium weakness)")
    if not rsi_ok:
        if (
            rsi_zone_or_reversal
            and (leg_score_mode or "").strip().lower() == "three_factor"
        ):
            zlo, zhi = float(rsi_soft_zone_low), float(rsi_soft_zone_high)
            if zlo > zhi:
                zlo, zhi = zhi, zlo
            nfb = int(rsi_reversal_falling_bars)
            if nfb >= 2:
                failed.append(
                    f"RSI not in {zlo:.0f}-{zhi:.0f} nor strictly falling over last {nfb} bars (zone_or_reversal)"
                )
            else:
                failed.append(
                    f"RSI not in {zlo:.0f}-{zhi:.0f} nor reversal from ≥{float(rsi_reversal_from_rsi):.0f} "
                    "(zone_or_reversal)"
                )
        elif rsi_require_decreasing and (leg_score_mode or "").strip().lower() == "three_factor":
            rb = float(rsi_below_for_weak)
            failed.append(f"RSI not falling vs prior bar and below {rb:.0f} (three_factor)")
        elif rsi_direct_band:
            failed.append(f"RSI not in {rsi_min:.0f}-{rsi_max:.0f} (direct leg band)")
        elif (leg_score_mode or "").strip().lower() == "three_factor":
            rb = float(rsi_below_for_weak)
            failed.append(f"RSI not below {rb:.0f} (three_factor leg score)")
        else:
            failed.append(f"RSI not in bearish mirror band vs spot {rsi_min:.0f}-{rsi_max:.0f}")
    return "PASS" if not failed else "; ".join(failed)


def _effective_strike_min_volume(
    base_min_vol: int,
    *,
    early_session_vol: int | None,
    early_session_end_hour_ist: int,
) -> int:
    """Before early_session_end_hour_ist (IST), use min(base, early_session_vol) when early_session_vol is set."""
    bv = max(0, int(base_min_vol))
    if early_session_vol is None or int(early_session_end_hour_ist or 0) <= 0:
        return bv
    try:
        ev = int(early_session_vol)
    except (TypeError, ValueError):
        return bv
    if ev <= 0:
        return bv
    if datetime.now(_EVAL_IST).hour >= int(early_session_end_hour_ist):
        return bv
    return min(bv, ev)


def _parse_flow_ranking_cfg(raw: Any) -> dict[str, Any] | None:
    """Optional long-premium strike ranking using OI/volume/ΔOI + landing-style flow tilt."""
    if not isinstance(raw, dict):
        return None
    en = raw.get("enabled", False)
    if isinstance(en, str):
        en = en.strip().lower() in {"1", "true", "yes"}
    elif en is not None and not isinstance(en, bool):
        en = str(en).strip().lower() in {"1", "true", "yes"}
    if not bool(en):
        return None
    uct = raw.get("useChainFlowTilt", True)
    if isinstance(uct, str):
        uct = uct.strip().lower() in {"1", "true", "yes"}
    pin_raw = raw.get("pinPenaltyOnExpiryDay", False)
    if isinstance(pin_raw, str):
        pin_on = pin_raw.strip().lower() in {"1", "true", "yes"}
    else:
        pin_on = bool(pin_raw)
    return {
        "use_chain_flow_tilt": bool(uct),
        "tilt_weight": float(raw.get("tiltWeight", 0.22)),
        "percentile_oi_weight": float(raw.get("percentileOiWeight", 1.0)),
        "percentile_vol_weight": float(raw.get("percentileVolWeight", 1.0)),
        "oi_chg_scale_weight": float(raw.get("oiChgScaleWeight", 0.12)),
        "long_buildup_bonus": float(raw.get("longBuildupBonus", 0.28)),
        "short_covering_bonus": float(raw.get("shortCoveringBonus", 0.24)),
        "pin_penalty_on_expiry_day": pin_on,
        "pin_max_distance_pts": float(raw.get("pinMaxDistanceFromSpot", 150)),
        "pin_oi_dominance_ratio": float(raw.get("pinOiDominanceRatio", 1.2)),
        "pin_penalty_weight": float(raw.get("pinPenaltyWeight", 0.18)),
    }


def _chain_leg_oi_float(leg: dict[str, Any] | None) -> float:
    if not leg:
        return 0.0
    try:
        return float(leg.get("oi") or 0)
    except (TypeError, ValueError):
        return 0.0


def _pin_wall_strikes_from_chain(
    chain: list[dict[str, Any]],
    *,
    dominance_ratio: float,
) -> tuple[int | None, int | None]:
    """
    Top CE and top PE strikes by OI in this chain window.
    A wing counts as a "wall" only if top_oi >= ratio * second_oi (ratio <= 1.01 → top always).
    """
    ce: list[tuple[int, float]] = []
    pe: list[tuple[int, float]] = []
    for row in chain:
        try:
            st = int(float(row.get("strike") or 0))
        except (TypeError, ValueError):
            continue
        if st <= 0:
            continue
        coi = _chain_leg_oi_float(row.get("call") if isinstance(row.get("call"), dict) else None)
        poi = _chain_leg_oi_float(row.get("put") if isinstance(row.get("put"), dict) else None)
        ce.append((st, coi))
        pe.append((st, poi))

    def _wall(rows: list[tuple[int, float]]) -> int | None:
        if not rows:
            return None
        ordered = sorted(rows, key=lambda x: x[1], reverse=True)
        top_s, top_o = ordered[0]
        if top_o <= 0:
            return None
        rdom = float(dominance_ratio)
        if len(ordered) < 2 or rdom <= 1.01:
            return top_s
        second_o = ordered[1][1]
        if second_o <= 0:
            return top_s
        return top_s if top_o >= rdom * second_o else None

    return (_wall(ce), _wall(pe))


def _percentile_rank_map(rows: list[dict[str, Any]], attr: str) -> dict[int, float]:
    """Highest metric → 1.0; keys are id(row)."""
    if not rows:
        return {}
    if len(rows) == 1:
        return {id(rows[0]): 1.0}

    def metric(r: dict[str, Any]) -> float:
        try:
            return float(r.get(attr) or 0)
        except (TypeError, ValueError):
            return 0.0

    ordered = sorted(rows, key=metric, reverse=True)
    n = len(ordered)
    denom = float(n - 1)
    return {id(r): (n - 1 - i) / denom for i, r in enumerate(ordered)}


def _apply_long_premium_flow_ranking(
    recs: list[dict[str, Any]],
    chain: list[dict[str, Any]],
    chain_payload: dict[str, Any],
    cfg: dict[str, Any],
    *,
    expiry_date: date | None = None,
) -> dict[str, Any]:
    """Set flow_rank_score on each rec; return metadata for evaluation / chain snapshot."""
    if not recs or not cfg:
        return {}
    try:
        pcr = chain_payload.get("pcr")
        pcr_vol = chain_payload.get("pcrVol")
        spot_raw = chain_payload.get("spotChgPct")
        spot_chg = float(spot_raw) if spot_raw is not None else 0.0
        sent = compute_sentiment_snapshot(
            chain_payload={"chain": chain, "pcr": pcr, "pcrVol": pcr_vol},
            spot_chg_pct=spot_chg,
            trendpulse_signal=None,
        )
        oi = sent.get("optionsIntel") or {}
        tilt = str(oi.get("modelOptionTilt") or "NEUTRAL").strip().upper()
        blend = float(oi.get("flowBlendScore") or 0.0)
    except Exception:
        tilt, blend = "NEUTRAL", 0.0

    ce_list = [r for r in recs if str(r.get("option_type") or "").upper() == "CE"]
    pe_list = [r for r in recs if str(r.get("option_type") or "").upper() == "PE"]
    pct_oi_ce = _percentile_rank_map(ce_list, "oi")
    pct_oi_pe = _percentile_rank_map(pe_list, "oi")
    pct_vol_ce = _percentile_rank_map(ce_list, "volume")
    pct_vol_pe = _percentile_rank_map(pe_list, "volume")

    tw = float(cfg.get("tilt_weight", 0.22))
    wo = float(cfg.get("percentile_oi_weight", 1.0))
    wv = float(cfg.get("percentile_vol_weight", 1.0))
    wc = float(cfg.get("oi_chg_scale_weight", 0.12))
    wb = float(cfg.get("long_buildup_bonus", 0.28))
    wsc = float(cfg.get("short_covering_bonus", 0.24))
    use_tilt = bool(cfg.get("use_chain_flow_tilt", True))

    for r in recs:
        opt = str(r.get("option_type") or "").upper()
        wing = 0.0
        if use_tilt:
            if tilt == "CE":
                wing = 1.0 if opt == "CE" else -0.35
            elif tilt == "PE":
                wing = 1.0 if opt == "PE" else -0.35
        if opt == "CE":
            p_oi = pct_oi_ce.get(id(r), 0.5)
            p_vol = pct_vol_ce.get(id(r), 0.5)
        elif opt == "PE":
            p_oi = pct_oi_pe.get(id(r), 0.5)
            p_vol = pct_vol_pe.get(id(r), 0.5)
        else:
            p_oi = p_vol = 0.5
        try:
            och = float(r.get("oi_chg_pct") or 0.0)
        except (TypeError, ValueError):
            och = 0.0
        och_term = math.tanh(och / 20.0)
        bu = str(r.get("buildup") or "").strip()
        if bu == "Long Buildup":
            bun = wb
        elif bu == "Short Covering":
            bun = wsc
        else:
            bun = 0.0
        fr = tw * wing + wo * p_oi + wv * p_vol + wc * och_term + bun
        r["flow_rank_score"] = round(fr, 4)

    pin_note: dict[str, Any] = {"active": False}
    if (
        cfg.get("pin_penalty_on_expiry_day")
        and expiry_date is not None
        and chain
    ):
        today_ist = datetime.now(_EVAL_IST).date()
        dte = (expiry_date - today_ist).days
        pin_note["dte_ist"] = dte
        if dte == 0:
            spot = float(chain_payload.get("spot") or 0.0)
            ratio = float(cfg.get("pin_oi_dominance_ratio", 1.2))
            dist_max = float(cfg.get("pin_max_distance_pts", 150))
            pw = float(cfg.get("pin_penalty_weight", 0.18))
            ce_wall, pe_wall = _pin_wall_strikes_from_chain(chain, dominance_ratio=ratio)
            pin_note.update(
                {
                    "active": True,
                    "ce_wall_strike": ce_wall,
                    "pe_wall_strike": pe_wall,
                    "max_dist_pts": dist_max,
                    "penalty": pw,
                }
            )
            for r in recs:
                opt = str(r.get("option_type") or "").upper()
                try:
                    stk = int(r.get("strike") or 0)
                except (TypeError, ValueError):
                    continue
                wall = ce_wall if opt == "CE" else pe_wall if opt == "PE" else None
                if wall is None or stk != wall or spot <= 0:
                    continue
                if abs(float(stk) - spot) > dist_max:
                    continue
                r["flow_rank_score"] = round(float(r["flow_rank_score"]) - pw, 4)
                r["flow_pin_penalized"] = True

    return {
        "flow_ranking": {
            "enabled": True,
            "landing_flow_tilt": tilt,
            "flow_blend_score": round(blend, 4),
            "pin_expiry_soft_penalty": pin_note,
        }
    }


def _long_premium_rec_sort_key(r: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -int(r.get("score") or 0),
        -float(r.get("flow_rank_score") or 0.0),
        -float(r.get("volume_spike_ratio") or 0.0),
        -float(r.get("oi_chg_pct") or 0.0),
        float(r.get("delta_distance") or 0.0),
        abs(int(r.get("distance_to_atm") or 0)),
    )


def _short_premium_eligible_sort_key(
    r: dict[str, Any],
    *,
    rsi_decreasing_rank: bool = False,
) -> tuple[Any, ...]:
    """Rank short-premium legs: score/confidence first; optional RSI-momentum (prior bar → now) when decreasing mode."""
    head: tuple[Any, ...] = (
        -int(r.get("score") or 0),
        -float(r.get("confidence_score") or 0.0),
    )
    mid: tuple[Any, ...] = (
        (-float(r.get("short_premium_rsi_drop") or 0.0),)
        if rsi_decreasing_rank
        else ()
    )
    tail: tuple[Any, ...] = (
        -int(r.get("oi") or 0),
        -float(r.get("volume_spike_ratio") or 0.0),
        -float(r.get("oi_chg_pct") or 0.0),
        float(r.get("delta_distance") or 0.0),
        abs(int(r.get("distance_to_atm") or 0)),
        float(r.get("gamma") or 0.0),
    )
    return head + mid + tail


def _deep_merge_strategy_details(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge overlay onto base (overlay wins). Used so user Settings JSON overrides catalog defaults."""
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_strategy_details(out[k], v)
        else:
            out[k] = v
    return out


def _short_premium_datm_allows_leg(
    opt_type: str,
    distance_to_atm: int,
    *,
    ce_min: int,
    ce_max: int,
    pe_min: int,
    pe_max: int,
) -> bool:
    """Short premium: CE and PE use separate dATM (strike − ATM) step windows."""
    if opt_type == "CE":
        return ce_min <= distance_to_atm <= ce_max
    return pe_min <= distance_to_atm <= pe_max


def _short_premium_quad_from_side(d: dict[str, Any]) -> dict[str, float] | None:
    """Parse one branch of shortPremiumDeltaVixBands (camelCase keys). Returns ce_min/ce_max/pe_min/pe_max."""

    def _num(keys: tuple[str, ...]) -> float | None:
        for k in keys:
            v = d.get(k)
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                return float(v)
        return None

    ce_lo = _num(("deltaMinCE", "ceMin"))
    ce_hi = _num(("deltaMaxCE", "ceMax"))
    pe_lo = _num(("deltaMinPE", "peMin"))
    pe_hi = _num(("deltaMaxPE", "peMax"))
    if ce_lo is None or ce_hi is None or pe_lo is None or pe_hi is None:
        return None
    if ce_lo > ce_hi or pe_lo > pe_hi:
        return None
    if ce_lo <= 0 or ce_hi <= 0:
        return None
    if pe_lo >= 0 or pe_hi >= 0:
        return None
    return {"ce_min": ce_lo, "ce_max": ce_hi, "pe_min": pe_lo, "pe_max": pe_hi}


def _normalize_short_premium_delta_vix_bands(strike_cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Validate strikeSelection.shortPremiumDeltaVixBands; None if absent or invalid."""
    raw = strike_cfg.get("shortPremiumDeltaVixBands")
    if not isinstance(raw, dict) or not raw:
        return None
    thr = raw.get("threshold")
    if isinstance(thr, bool) or not isinstance(thr, (int, float)):
        return None
    above_raw = raw.get("vixAbove") or raw.get("aboveThreshold")
    below_raw = raw.get("vixAtOrBelow") or raw.get("belowThreshold")
    if not isinstance(above_raw, dict) or not isinstance(below_raw, dict):
        return None
    qa = _short_premium_quad_from_side(above_raw)
    qb = _short_premium_quad_from_side(below_raw)
    if qa is None or qb is None:
        return None
    return {"threshold": float(thr), "vix_above": qa, "vix_at_or_below": qb}


def _resolve_short_premium_delta_corners(
    *,
    strike_delta_min_abs: float,
    strike_delta_max_abs: float,
    short_premium_delta_vix_bands: dict[str, Any] | None,
    vix: Any,
) -> tuple[float, float, float, float, str]:
    """
    Short-premium delta gates: CE in [ce_lo, ce_hi], PE in [pe_lo, pe_hi] (PE negative).
    Without VIX bands: CE [deltaMinAbs, deltaMaxAbs], PE [-deltaMaxAbs, -deltaMinAbs].
    With VIX bands: VIX > threshold → vixAbove; VIX <= threshold → vixAtOrBelow; missing VIX → fallback to deltaMin/MaxAbs.
    """
    d_lo = float(strike_delta_min_abs)
    d_hi = float(strike_delta_max_abs)
    fb = f"CE [{d_lo:.2f},{d_hi:.2f}] PE [{-d_hi:.2f},{-d_lo:.2f}] (deltaMinAbs/deltaMaxAbs)"
    bands = short_premium_delta_vix_bands
    if not isinstance(bands, dict) or not bands:
        return d_lo, d_hi, -d_hi, -d_lo, fb
    thr = float(bands["threshold"])
    above = bands["vix_above"]
    below = bands["vix_at_or_below"]
    vx_f: float | None
    try:
        vx_f = float(vix) if vix is not None else None
    except (TypeError, ValueError):
        vx_f = None
    if vx_f is None:
        return d_lo, d_hi, -d_hi, -d_lo, f"{fb}; VIX unavailable — fallback"
    side = above if vx_f > thr else below
    ce_lo = float(side["ce_min"])
    ce_hi = float(side["ce_max"])
    pe_lo = float(side["pe_min"])
    pe_hi = float(side["pe_max"])
    rel = ">" if vx_f > thr else "<="
    note = (
        f"VIX={vx_f:.2f} {rel} {thr:g} → CE [{ce_lo:.2f},{ce_hi:.2f}] PE [{pe_lo:.2f},{pe_hi:.2f}]"
    )
    return ce_lo, ce_hi, pe_lo, pe_hi, note


def _short_premium_signed_delta_ok(
    delta: float,
    opt_type: str,
    *,
    ce_lo: float,
    ce_hi: float,
    pe_lo: float,
    pe_hi: float,
) -> bool:
    if opt_type == "CE":
        return ce_lo - 1e-9 <= delta <= ce_hi + 1e-9
    return pe_lo - 1e-9 <= delta <= pe_hi + 1e-9


def _short_premium_delta_blocker(
    delta: float,
    opt_type: str,
    *,
    ce_lo: float,
    ce_hi: float,
    pe_lo: float,
    pe_hi: float,
) -> str:
    if opt_type == "CE":
        return f"delta={delta:.4f} not in CE [{ce_lo:.2f},{ce_hi:.2f}]"
    return f"delta={delta:.4f} not in PE [{pe_lo:.2f},{pe_hi:.2f}]"


def _chain_eval_meta(
    *,
    expiry_str: str | None,
    expiry_date: date | None,
    chain_len: int,
    reason: str | None = None,
    short_leg_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    today_ist = datetime.now(_EVAL_IST).date()
    dte = (expiry_date - today_ist).days if (expiry_str and expiry_date is not None) else None
    m: dict[str, Any] = {
        "option_expiry": expiry_str,
        "chain_rows": chain_len,
        "calendar_dte_ist": dte,
    }
    if reason:
        m["reason"] = reason
    if short_leg_diagnostics:
        m["short_leg_diagnostics"] = short_leg_diagnostics
    return m


def _build_short_leg_diagnostics(
    chain: list[dict[str, Any]],
    *,
    spot: float,
    strike_max_otm_steps: int,
    short_premium_delta_only_strikes: bool,
    short_premium_asymmetric_datm: bool,
    short_premium_ce_datm_min: int,
    short_premium_ce_datm_max: int,
    short_premium_pe_datm_min: int,
    short_premium_pe_datm_max: int,
    strike_regime_mode: str,
    spot_regime: Any,
    spot_bull: int,
    spot_bear: int,
    score_threshold: int | float,
    ivr_min_threshold: float,
    ivr_leg_max_threshold: float,
    short_premium_ivr_min_ce: float | None = None,
    short_premium_ivr_min_pe: float | None = None,
    short_ce_delta_min: float,
    short_ce_delta_max: float,
    short_pe_delta_min: float,
    short_pe_delta_max: float,
    rsi_min: float,
    rsi_max: float,
    strike_min_oi: int,
    strike_min_volume: int,
    instrument: str,
    expiry_str: str,
    short_premium_leg_score_mode: str = "",
    short_premium_rsi_below: float = 50.0,
    short_premium_rsi_direct_band: bool = False,
    short_premium_rsi_decreasing: bool = False,
    short_premium_rsi_zone_or_reversal: bool = False,
    short_premium_rsi_reversal_falling_bars: int = 0,
    short_premium_rsi_soft_zone_low: float = 20.0,
    short_premium_rsi_soft_zone_high: float = 45.0,
    short_premium_rsi_reversal_from_rsi: float = 70.0,
    max_rows: int = 48,
    score_max: int = 5,
) -> list[dict[str, Any]]:
    """
    One row per option leg: OTM-step window unless short_premium_delta_only_strikes (then delta band only).
    """
    step = 50
    atm = round(spot / step) * step
    st_int = int(score_threshold)
    out: list[dict[str, Any]] = []
    srm = str(strike_regime_mode or "").strip().lower()
    skip_otm_geometry = bool(short_premium_delta_only_strikes)
    diag_include_oob_delta = os.getenv("S004_SHORT_DIAGNOSTICS_INCLUDE_OOB_DELTA", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    for row in chain:
        if len(out) >= max(1, int(max_rows)):
            break
        strike = int(float(row.get("strike", 0)))
        distance_to_atm = int((strike - atm) / step)
        if not skip_otm_geometry:
            if not short_premium_asymmetric_datm:
                if abs(distance_to_atm) > strike_max_otm_steps:
                    continue
        for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
            if len(out) >= max(1, int(max_rows)):
                break
            if short_premium_asymmetric_datm and not skip_otm_geometry:
                if not _short_premium_datm_allows_leg(
                    opt_type,
                    distance_to_atm,
                    ce_min=short_premium_ce_datm_min,
                    ce_max=short_premium_ce_datm_max,
                    pe_min=short_premium_pe_datm_min,
                    pe_max=short_premium_pe_datm_max,
                ):
                    continue
            leg = row.get(leg_key) or {}
            oi = int(float(leg.get("oi") or 0))
            volume = int(float(leg.get("volume") or 0))
            ltp = float(leg.get("ltp") or 0.0)
            delta = float(leg.get("delta") or 0.0)
            delta_abs = abs(delta)
            # By default only legs inside the active CE/PE delta band (VIX-resolved). See env below for full chain.
            if not diag_include_oob_delta and not _short_premium_signed_delta_ok(
                delta,
                opt_type,
                ce_lo=short_ce_delta_min,
                ce_hi=short_ce_delta_max,
                pe_lo=short_pe_delta_min,
                pe_hi=short_pe_delta_max,
            ):
                continue
            ivr = leg.get("ivr")
            rspe = bool(leg.get("regimeSellPe"))
            rsce = bool(leg.get("regimeSellCe"))
            leg_score = int(leg.get("score") or 0)
            leg_sig = bool(leg.get("signalEligible"))
            blockers: list[str] = []
            if oi < strike_min_oi or volume < strike_min_volume:
                blockers.append(
                    f"strict_liquidity(oi<{strike_min_oi} or vol<{strike_min_volume})"
                )
            if srm == "ema_cross_vwap":
                if opt_type == "PE" and not rspe:
                    blockers.append("regimeSellPe=false")
                if opt_type == "CE" and not rsce:
                    blockers.append("regimeSellCe=false")
            else:
                if spot_regime == "bullish" and opt_type != "PE":
                    blockers.append("spot_regime_bullish_needs_PE")
                elif spot_regime == "bearish" and opt_type != "CE":
                    blockers.append("spot_regime_bearish_needs_CE")
                elif spot_regime not in ("bullish", "bearish"):
                    blockers.append(f"spot_regime_unset_or_mixed(regime={spot_regime!r})")
            eff_ivr_min = float(ivr_min_threshold)
            if opt_type == "CE" and short_premium_ivr_min_ce is not None:
                eff_ivr_min = float(short_premium_ivr_min_ce)
            elif opt_type == "PE" and short_premium_ivr_min_pe is not None:
                eff_ivr_min = float(short_premium_ivr_min_pe)
            if eff_ivr_min > 0 or ivr_leg_max_threshold > 0:
                if ivr is None:
                    blockers.append("IVR=null")
                else:
                    try:
                        ivf = float(ivr)
                        if eff_ivr_min > 0 and ivf < eff_ivr_min:
                            blockers.append(f"IVR<{eff_ivr_min} (got {ivf:.1f})")
                        if ivr_leg_max_threshold > 0 and ivf > float(ivr_leg_max_threshold):
                            blockers.append(f"IVR>{ivr_leg_max_threshold} (got {ivf:.1f})")
                    except (TypeError, ValueError):
                        blockers.append("IVR=invalid")
            if diag_include_oob_delta and not _short_premium_signed_delta_ok(
                delta,
                opt_type,
                ce_lo=short_ce_delta_min,
                ce_hi=short_ce_delta_max,
                pe_lo=short_pe_delta_min,
                pe_hi=short_pe_delta_max,
            ):
                blockers.append(
                    _short_premium_delta_blocker(
                        delta,
                        opt_type,
                        ce_lo=short_ce_delta_min,
                        ce_hi=short_ce_delta_max,
                        pe_lo=short_pe_delta_min,
                        pe_hi=short_pe_delta_max,
                    )
                )
            if srm != "ema_cross_vwap":
                if spot_regime == "bullish" and opt_type == "PE" and spot_bull < st_int:
                    blockers.append(f"spot_bullish_score {spot_bull} < {st_int}")
                elif spot_regime == "bearish" and opt_type == "CE" and spot_bear < st_int:
                    blockers.append(f"spot_bearish_score {spot_bear} < {st_int}")
            leg_ok = leg_sig
            if leg.get("shortPremiumExpansionBlocked"):
                blockers.append("expansion_phase_block(RSI high + LTP>VWAP)")
            if leg.get("shortPremiumVwapDistanceBlocked"):
                blockers.append("VWAP_weakness_distance_below_min_pct")
            if leg.get("shortPremiumMomentumBlocked"):
                blockers.append("momentum_below_shortPremiumMinMomentumPoints")
            if leg.get("shortPremiumGhostBlocked"):
                blockers.append("ghost_timing_insufficient_RSI_drop_from_prior")
            if not leg_ok:
                _enrich_blocked = bool(
                    leg.get("shortPremiumExpansionBlocked")
                    or leg.get("shortPremiumVwapDistanceBlocked")
                    or leg.get("shortPremiumMomentumBlocked")
                    or leg.get("shortPremiumGhostBlocked")
                )
                if not _enrich_blocked:
                    detail = _failed_conditions_short_leg(
                        bool(leg.get("primaryOk")),
                        bool(leg.get("emaOk")),
                        bool(leg.get("rsiOk")),
                        rsi_min=rsi_min,
                        rsi_max=rsi_max,
                        leg_score_mode=short_premium_leg_score_mode,
                        rsi_below_for_weak=short_premium_rsi_below,
                        rsi_direct_band=short_premium_rsi_direct_band,
                        rsi_require_decreasing=short_premium_rsi_decreasing,
                        rsi_zone_or_reversal=short_premium_rsi_zone_or_reversal,
                        rsi_reversal_falling_bars=short_premium_rsi_reversal_falling_bars,
                        rsi_soft_zone_low=short_premium_rsi_soft_zone_low,
                        rsi_soft_zone_high=short_premium_rsi_soft_zone_high,
                        rsi_reversal_from_rsi=short_premium_rsi_reversal_from_rsi,
                    )
                    if detail != "PASS":
                        blockers.append(f"leg_conditions: {detail}")
                    else:
                        blockers.append(
                            f"leg_composite_score_low(leg_score={leg_score}, need>={st_int})"
                        )
            sym = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(
                instrument, expiry_str, strike, opt_type
            )
            non_liq = [b for b in blockers if not b.startswith("strict_liquidity")]
            vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
            smax = max(1, int(score_max))
            if srm == "ema_cross_vwap":
                eff_score_for_conf = min(smax, max(st_int, leg_score))
            else:
                eff_score_for_conf = int(spot_bull if opt_type == "PE" else spot_bear)
            conf_denom = smax
            base_conf = (eff_score_for_conf / conf_denom) * 100
            vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
            confidence_score = min(99.0, round(base_conf + vol_bonus, 2))
            trade_eligible = len(blockers) == 0
            out.append(
                {
                    "symbol": sym,
                    "strike": strike,
                    "option_type": opt_type,
                    "distance_to_atm": distance_to_atm,
                    "ltp": round(ltp, 2),
                    "delta": round(delta, 4),
                    "delta_abs": round(delta_abs, 4),
                    "ivr": round(float(ivr), 2) if ivr is not None else None,
                    "oi": oi,
                    "volume": volume,
                    "volume_spike_ratio": vol_ratio,
                    "ema9": float(leg.get("ema9") or 0.0),
                    "ema21": float(leg.get("ema21") or 0.0),
                    "vwap": float(leg.get("vwap") or 0.0),
                    "rsi": float(leg.get("rsi") or 0.0),
                    "regime_sell_pe": rspe,
                    "regime_sell_ce": rsce,
                    "leg_score": leg_score,
                    "leg_signal_eligible": leg_sig,
                    "trade_eligible": trade_eligible,
                    "confidence_score": confidence_score,
                    "ema_crossover_ok": bool(leg.get("emaCrossoverOk")),
                    "blockers": "; ".join(blockers) if blockers else "—",
                    "would_pass_non_liquidity_gates": len(non_liq) == 0,
                }
            )
    return out


async def _get_live_candidates(
    kite: KiteConnect | None,
    market_provider: Any | None,
    max_strike_distance: int,
    score_threshold: int = 3,
    score_max: int = 5,
    ivr_max_threshold: float = 20.0,
    ivr_bonus: int = 0,
    ema_crossover_max_candles: int | None = None,
    adx_period: int = 14,
    adx_min_threshold: int | float | None = None,
    strike_min_oi: int = 10000,
    strike_min_volume: int = 500,
    strike_delta_ce: float = 0.35,
    strike_delta_pe: float = -0.35,
    strike_max_otm_steps: int = 3,
    rsi_min: float = 50,
    rsi_max: float = 75,
    volume_min_ratio: float = 1.5,
    position_intent: str = "long_premium",
    execution_action_intent: str = "long_premium",
    ivr_min_threshold: float = 0.0,
    ivr_leg_max_threshold: float = 0.0,
    strike_delta_min_abs: float = 0.29,
    strike_delta_max_abs: float = 0.35,
    min_dte_calendar_days: int = 0,
    nifty_weekly_expiry_weekday: int | None = None,
    select_strike_by_min_gamma: bool = False,
    max_strike_recommendations: int = 1,
    include_ema_crossover_in_score: bool = True,
    strict_bullish_comparisons: bool = False,
    spot_regime_mode: str = "",
    include_volume_in_leg_score: bool = True,
    spot_regime_satisfied_score: int = 5,
    short_premium_asymmetric_datm: bool = False,
    short_premium_ce_datm_min: int = 2,
    short_premium_ce_datm_max: int = 4,
    short_premium_pe_datm_min: int = -4,
    short_premium_pe_datm_max: int = 2,
    short_premium_delta_vix_bands: dict[str, Any] | None = None,
    short_premium_delta_only_strikes: bool | None = None,
    short_premium_leg_score_mode: str = "",
    short_premium_rsi_below: float = 50.0,
    short_premium_rsi_direct_band: bool = False,
    short_premium_rsi_decreasing: bool = False,
    short_premium_ivr_skew_min: float = 5.0,
    short_premium_pcr_bonus_vs_chain: bool = True,
    short_premium_pcr_chain_epsilon: float = 0.0,
    short_premium_pcr_min_for_sell_ce: Any = None,
    short_premium_pcr_max_for_sell_pe: Any = None,
    short_premium_expansion_block_rsi: float = 0.0,
    short_premium_vwap_weakness_min_pct: float = 0.0,
    short_premium_min_momentum_points: int = 0,
    short_premium_ghost_rsi_drop_pts: float = 0.0,
    short_premium_rsi_zone_or_reversal: bool = False,
    short_premium_rsi_soft_zone_low: float = 20.0,
    short_premium_rsi_soft_zone_high: float = 45.0,
    short_premium_rsi_reversal_from_rsi: float = 70.0,
    short_premium_rsi_reversal_falling_bars: int = 0,
    short_premium_vwap_eligible_buffer_pct: float = 0.0,
    short_premium_three_factor_require_ltp_below_vwap: bool = True,
    short_premium_ivr_min_ce: float | None = None,
    short_premium_ivr_min_pe: float | None = None,
    require_rsi_for_eligible: bool = False,
    long_premium_spot_align: bool = False,
    min_volume_early_session: int | None = None,
    early_session_end_hour_ist: int = 0,
    flow_ranking: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    instrument = "NIFTY"
    if market_provider is not None:
        try:
            expiries, _src = await market_provider.expiries(instrument)
        except Exception:
            expiries = []
        expiry_str = _pick_expiry_from_provider_list(
            expiries,
            min_dte_calendar_days=min_dte_calendar_days,
            nifty_weekly_expiry_weekday=nifty_weekly_expiry_weekday,
        )
    elif min_dte_calendar_days > 0:
        expiry_str = pick_expiry_with_min_calendar_dte(
            kite,
            instrument,
            min_dte_days=int(min_dte_calendar_days),
            weekday=nifty_weekly_expiry_weekday,
        )
    else:
        expiry_str = pick_primary_expiry_str(kite, instrument)
    if not expiry_str:
        return [], [], _chain_eval_meta(expiry_str=None, expiry_date=None, chain_len=0, reason="no_expiry")
    try:
        expiry_date = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y").date()
    except ValueError:
        expiry_date = date.today()
    use_short = str(position_intent).strip().lower() == "short_premium"
    action_short = str(execution_action_intent or position_intent).strip().lower() == "short_premium"
    if short_premium_delta_only_strikes is None:
        short_premium_delta_only_strikes = bool(short_premium_delta_vix_bands) if use_short else False
    else:
        short_premium_delta_only_strikes = bool(short_premium_delta_only_strikes)
    if use_short and isinstance(short_premium_delta_vix_bands, dict) and short_premium_delta_vix_bands:
        short_premium_delta_only_strikes = True
    vix_prefetch: float | None = None
    if use_short and kite is not None:
        vix_prefetch = await asyncio.to_thread(_vix_from_quote, kite)
    chain_half_width = int(max_strike_distance)
    if use_short and short_premium_delta_only_strikes:
        try:
            floor = int(os.getenv("S004_SHORT_PREMIUM_DELTA_ONLY_STRIKES_EACH_SIDE", "12") or "12")
        except ValueError:
            floor = 12
        chain_half_width = max(chain_half_width, max(1, min(20, floor)))
    effective_max_otm_steps = int(strike_max_otm_steps)
    if use_short and not short_premium_delta_only_strikes:
        _dspan = float(strike_delta_max_abs) - float(strike_delta_min_abs)
        if _dspan <= 0.12:
            try:
                widen = int(os.getenv("S004_SHORT_PREMIUM_TIGHT_DELTA_STRIKES_EACH_SIDE", "12") or "12")
            except ValueError:
                widen = 12
            widen = max(1, min(20, widen))
            chain_half_width = max(chain_half_width, widen)
            effective_max_otm_steps = max(effective_max_otm_steps, widen)
            effective_max_otm_steps = min(effective_max_otm_steps, 20)
    indicator_params: dict[str, Any] = {
        "rsi_min": float(rsi_min),
        "rsi_max": float(rsi_max),
        "volume_min_ratio": float(volume_min_ratio),
        "include_ema_crossover_in_score": include_ema_crossover_in_score,
        "strict_bullish_comparisons": strict_bullish_comparisons,
        "include_volume_in_leg_score": include_volume_in_leg_score,
    }
    if spot_regime_mode:
        indicator_params["spotRegimeMode"] = spot_regime_mode
    if spot_regime_satisfied_score > 0:
        indicator_params["spotRegimeSatisfiedScore"] = int(spot_regime_satisfied_score)
    if use_short:
        indicator_params["positionIntent"] = "short_premium"
        indicator_params["scoreMaxLeg"] = int(max(3, min(10, score_max)))
        _lm = str(short_premium_leg_score_mode or "").strip().lower()
        if _lm:
            indicator_params["shortPremiumLegScoreMode"] = _lm
        indicator_params["shortPremiumRsiBelow"] = float(short_premium_rsi_below)
        if short_premium_rsi_direct_band:
            indicator_params["shortPremiumRsiDirectBand"] = True
        if short_premium_rsi_decreasing:
            indicator_params["shortPremiumRsiDecreasing"] = True
        indicator_params["shortPremiumIvrSkewMin"] = float(short_premium_ivr_skew_min)
        indicator_params["shortPremiumPcrBonusVsChain"] = bool(short_premium_pcr_bonus_vs_chain)
        indicator_params["shortPremiumPcrChainEpsilon"] = float(short_premium_pcr_chain_epsilon)
        if short_premium_pcr_min_for_sell_ce is not None:
            indicator_params["shortPremiumPcrMinForSellCe"] = short_premium_pcr_min_for_sell_ce
        if short_premium_pcr_max_for_sell_pe is not None:
            indicator_params["shortPremiumPcrMaxForSellPe"] = short_premium_pcr_max_for_sell_pe
        if float(short_premium_expansion_block_rsi or 0) > 0:
            indicator_params["shortPremiumExpansionBlockRsi"] = float(short_premium_expansion_block_rsi)
        if float(short_premium_vwap_weakness_min_pct or 0) > 0:
            indicator_params["shortPremiumVwapWeaknessMinPct"] = float(short_premium_vwap_weakness_min_pct)
        if int(short_premium_min_momentum_points or 0) > 0:
            indicator_params["shortPremiumMinMomentumPoints"] = int(short_premium_min_momentum_points)
        if float(short_premium_ghost_rsi_drop_pts or 0) > 0:
            indicator_params["shortPremiumGhostRsiDropPts"] = float(short_premium_ghost_rsi_drop_pts)
        if short_premium_rsi_zone_or_reversal:
            indicator_params["shortPremiumRsiZoneOrReversal"] = True
            indicator_params["shortPremiumRsiSoftZoneLow"] = float(short_premium_rsi_soft_zone_low)
            indicator_params["shortPremiumRsiSoftZoneHigh"] = float(short_premium_rsi_soft_zone_high)
            indicator_params["shortPremiumRsiReversalFromRsi"] = float(short_premium_rsi_reversal_from_rsi)
            _rfb = max(0, min(20, int(short_premium_rsi_reversal_falling_bars or 0)))
            if _rfb > 0:
                indicator_params["shortPremiumRsiReversalFallingBars"] = _rfb
        _vwbuf = float(short_premium_vwap_eligible_buffer_pct or 0)
        if _vwbuf > 0:
            indicator_params["shortPremiumVwapEligibleBufferPct"] = min(3.0, _vwbuf)
        if not short_premium_three_factor_require_ltp_below_vwap:
            indicator_params["shortPremiumThreeFactorRequireLtpBelowVwapForEligible"] = False
    if ema_crossover_max_candles is not None:
        indicator_params["max_candles_since_cross"] = ema_crossover_max_candles
    if adx_min_threshold is not None:
        indicator_params["adx_period"] = adx_period
        indicator_params["adx_min_threshold"] = float(adx_min_threshold)
    if require_rsi_for_eligible:
        indicator_params["requireRsiForEligible"] = True
    if long_premium_spot_align and not use_short:
        indicator_params["longPremiumSpotAlign"] = True
    eff_strike_min_volume = _effective_strike_min_volume(
        int(strike_min_volume),
        early_session_vol=min_volume_early_session,
        early_session_end_hour_ist=int(early_session_end_hour_ist or 0),
    )
    if market_provider is not None:
        chain_payload = await market_provider.option_chain(
            instrument,
            expiry_str,
            chain_half_width,
            chain_half_width,
            True,
        )
    else:
        chain_payload = await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            instrument,
            expiry_str,
            chain_half_width,
            chain_half_width,
            score_threshold,
            indicator_params if indicator_params else None,
        )
    chain = chain_payload.get("chain", [])
    spot = float(chain_payload.get("spot") or 0.0)
    if not chain or spot <= 0:
        return (
            [],
            [],
            _chain_eval_meta(
                expiry_str=expiry_str,
                expiry_date=expiry_date,
                chain_len=len(chain or []),
                reason="empty_chain_or_spot",
            ),
        )
    spot_regime = chain_payload.get("spotRegime")
    spot_bull = int(chain_payload.get("spotBullishScore") or 0)
    spot_bear = int(chain_payload.get("spotBearishScore") or 0)
    strike_regime_mode = str(spot_regime_mode or "").strip().lower()
    ce_d_lo = ce_d_hi = pe_d_lo = pe_d_hi = 0.0
    short_delta_gate_note = ""
    if use_short:
        vix_for_delta = chain_payload.get("vix")
        if vix_for_delta is None and vix_prefetch is not None:
            vix_for_delta = vix_prefetch
        ce_d_lo, ce_d_hi, pe_d_lo, pe_d_hi, short_delta_gate_note = _resolve_short_premium_delta_corners(
            strike_delta_min_abs=strike_delta_min_abs,
            strike_delta_max_abs=strike_delta_max_abs,
            short_premium_delta_vix_bands=short_premium_delta_vix_bands,
            vix=vix_for_delta,
        )
    try:
        diag_max = int(os.getenv("S004_EVALUATION_LOG_MAX_DIAGNOSTIC_LEGS", "48") or "48")
    except ValueError:
        diag_max = 48
    diag_max = max(8, min(80, diag_max))
    short_diag: list[dict[str, Any]] = []
    if use_short:
        short_diag = _build_short_leg_diagnostics(
            chain,
            spot=spot,
            strike_max_otm_steps=effective_max_otm_steps,
            short_premium_delta_only_strikes=short_premium_delta_only_strikes,
            short_premium_asymmetric_datm=short_premium_asymmetric_datm,
            short_premium_ce_datm_min=short_premium_ce_datm_min,
            short_premium_ce_datm_max=short_premium_ce_datm_max,
            short_premium_pe_datm_min=short_premium_pe_datm_min,
            short_premium_pe_datm_max=short_premium_pe_datm_max,
            strike_regime_mode=strike_regime_mode,
            spot_regime=spot_regime,
            spot_bull=spot_bull,
            spot_bear=spot_bear,
            score_threshold=score_threshold,
            ivr_min_threshold=ivr_min_threshold,
            ivr_leg_max_threshold=ivr_leg_max_threshold,
            short_premium_ivr_min_ce=short_premium_ivr_min_ce,
            short_premium_ivr_min_pe=short_premium_ivr_min_pe,
            short_ce_delta_min=ce_d_lo,
            short_ce_delta_max=ce_d_hi,
            short_pe_delta_min=pe_d_lo,
            short_pe_delta_max=pe_d_hi,
            rsi_min=rsi_min,
            rsi_max=rsi_max,
            strike_min_oi=strike_min_oi,
            strike_min_volume=strike_min_volume,
            instrument=instrument,
            expiry_str=expiry_str,
            short_premium_leg_score_mode=str(short_premium_leg_score_mode or ""),
            short_premium_rsi_below=float(short_premium_rsi_below or 50),
            short_premium_rsi_direct_band=bool(short_premium_rsi_direct_band),
            short_premium_rsi_decreasing=bool(short_premium_rsi_decreasing),
            short_premium_rsi_zone_or_reversal=bool(short_premium_rsi_zone_or_reversal),
            short_premium_rsi_reversal_falling_bars=max(
                0, min(20, int(short_premium_rsi_reversal_falling_bars or 0))
            ),
            short_premium_rsi_soft_zone_low=float(short_premium_rsi_soft_zone_low or 20),
            short_premium_rsi_soft_zone_high=float(short_premium_rsi_soft_zone_high or 45),
            short_premium_rsi_reversal_from_rsi=float(short_premium_rsi_reversal_from_rsi or 70),
            max_rows=diag_max,
            score_max=int(score_max),
        )
    step = 50
    atm = round(spot / step) * step
    conf_denom = max(1, int(score_max))
    liq_tiers: list[tuple[int, int, bool]] = [(strike_min_oi, eff_strike_min_volume, False)]
    if strike_min_oi > 0 or eff_strike_min_volume > 0:
        liq_tiers.append((0, 0, True))
    recs: list[dict] = []
    for min_oi, min_vol, relaxed_liq in liq_tiers:
        recs = []
        for row in chain:
            strike = int(float(row.get("strike", 0)))
            distance_to_atm = int((strike - atm) / step)
            skip_otm_geometry = use_short and short_premium_delta_only_strikes
            if not skip_otm_geometry:
                if not use_short or not short_premium_asymmetric_datm:
                    if abs(distance_to_atm) > effective_max_otm_steps:
                        continue
            for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
                if use_short and short_premium_asymmetric_datm and not skip_otm_geometry:
                    if not _short_premium_datm_allows_leg(
                        opt_type,
                        distance_to_atm,
                        ce_min=short_premium_ce_datm_min,
                        ce_max=short_premium_ce_datm_max,
                        pe_min=short_premium_pe_datm_min,
                        pe_max=short_premium_pe_datm_max,
                    ):
                        continue
                leg = row.get(leg_key) or {}
                oi = int(float(leg.get("oi") or 0))
                volume = int(float(leg.get("volume") or 0))
                if oi < min_oi or volume < min_vol:
                    continue
                strike_regime_ok = True
                if use_short:
                    if strike_regime_mode == "ema_cross_vwap":
                        strike_regime_ok = (
                            (opt_type == "PE" and bool(leg.get("regimeSellPe")))
                            or (opt_type == "CE" and bool(leg.get("regimeSellCe")))
                        )
                    elif spot_regime == "bullish":
                        strike_regime_ok = opt_type == "PE"
                    elif spot_regime == "bearish":
                        strike_regime_ok = opt_type == "CE"
                    else:
                        strike_regime_ok = False
                ltp = float(leg.get("ltp") or 0.0)
                delta = float(leg.get("delta") or 0.0)
                ivr = leg.get("ivr")
                ivr_ok = True
                delta_ok = True
                if use_short:
                    eff_ivr_min = float(ivr_min_threshold)
                    if opt_type == "CE" and short_premium_ivr_min_ce is not None:
                        eff_ivr_min = float(short_premium_ivr_min_ce)
                    elif opt_type == "PE" and short_premium_ivr_min_pe is not None:
                        eff_ivr_min = float(short_premium_ivr_min_pe)
                    if eff_ivr_min > 0 or ivr_leg_max_threshold > 0:
                        if ivr is None:
                            ivr_ok = False
                        else:
                            try:
                                ivf = float(ivr)
                                if eff_ivr_min > 0 and ivf < eff_ivr_min:
                                    ivr_ok = False
                                if ivr_leg_max_threshold > 0 and ivf > float(ivr_leg_max_threshold):
                                    ivr_ok = False
                            except (TypeError, ValueError):
                                ivr_ok = False
                    delta_ok = _short_premium_signed_delta_ok(
                        delta,
                        opt_type,
                        ce_lo=ce_d_lo,
                        ce_hi=ce_d_hi,
                        pe_lo=pe_d_lo,
                        pe_hi=pe_d_hi,
                    )
                    st_int = int(score_threshold)
                    leg_score = int(leg.get("score") or 0)
                    if strike_regime_mode == "ema_cross_vwap":
                        # Regime is already on leg flags; use composite leg score for ranking/auto-trade
                        # (was st_int only → score 3 with autoTrade 4 and confidence 75% blocked execution).
                        smax = max(1, int(score_max))
                        score = min(smax, max(st_int, leg_score))
                        spot_ok = True
                    else:
                        spot_score = spot_bull if opt_type == "PE" else spot_bear
                        score = int(spot_score)
                        spot_ok = score >= st_int
                    leg_ok = bool(leg.get("signalEligible"))
                    signal_eligible = (
                        spot_ok and leg_ok and strike_regime_ok and ivr_ok and delta_ok
                    )
                    ivr_note = float(ivr) if ivr is not None else None
                    parts: list[str] = []
                    if not ivr_ok:
                        if ivr is None:
                            parts.append("IVR=null")
                        else:
                            try:
                                ivf = float(ivr)
                                if eff_ivr_min > 0 and ivf < eff_ivr_min:
                                    parts.append(f"IVR<{eff_ivr_min} (got {ivf:.1f})")
                                if ivr_leg_max_threshold > 0 and ivf > float(ivr_leg_max_threshold):
                                    parts.append(f"IVR>{ivr_leg_max_threshold} (got {ivf:.1f})")
                            except (TypeError, ValueError):
                                parts.append("IVR=invalid")
                    if not delta_ok:
                        parts.append(
                            _short_premium_delta_blocker(
                                delta,
                                opt_type,
                                ce_lo=ce_d_lo,
                                ce_hi=ce_d_hi,
                                pe_lo=pe_d_lo,
                                pe_hi=pe_d_hi,
                            )
                        )
                    if not strike_regime_ok:
                        if strike_regime_mode == "ema_cross_vwap":
                            parts.append(
                                "regimeSellPe=false (need fresh EMA9<EMA21 cross + LTP<VWAP on PE leg)"
                                if opt_type == "PE"
                                else "regimeSellCe=false (need fresh EMA9<EMA21 cross + LTP<VWAP on CE leg)"
                            )
                        else:
                            parts.append(
                                f"spot_regime={spot_regime!r} requires {'PE' if spot_regime == 'bullish' else 'CE'}; "
                                f"leg={opt_type}"
                            )
                    if not spot_ok:
                        parts.append(
                            f"NIFTY spot trend score {score} < {st_int} (regime={spot_regime}, IVR={ivr_note})"
                            if strike_regime_mode != "ema_cross_vwap"
                            else "Strike regime (EMA cross + LTP<VWAP on this leg) not satisfied"
                        )
                    if not leg_ok:
                        enrich: list[str] = []
                        if leg.get("shortPremiumExpansionBlocked"):
                            enrich.append("expansion_phase_block(RSI above shortPremiumExpansionBlockRsi and LTP>VWAP)")
                        if leg.get("shortPremiumVwapDistanceBlocked"):
                            enrich.append("VWAP_weakness_distance_below_shortPremiumVwapWeaknessMinPct")
                        if leg.get("shortPremiumMomentumBlocked"):
                            enrich.append("momentum_factors_below_shortPremiumMinMomentumPoints")
                        if leg.get("shortPremiumGhostBlocked"):
                            enrich.append("ghost_timing(RSI drop from prior bar < shortPremiumGhostRsiDropPts)")
                        if enrich:
                            parts.append("; ".join(enrich))
                        else:
                            leg_detail = _failed_conditions_short_leg(
                                bool(leg.get("primaryOk")),
                                bool(leg.get("emaOk")),
                                bool(leg.get("rsiOk")),
                                rsi_min=rsi_min,
                                rsi_max=rsi_max,
                                leg_score_mode=str(short_premium_leg_score_mode or ""),
                                rsi_below_for_weak=float(short_premium_rsi_below or 50),
                                rsi_direct_band=bool(short_premium_rsi_direct_band),
                                rsi_require_decreasing=bool(short_premium_rsi_decreasing),
                                rsi_zone_or_reversal=bool(short_premium_rsi_zone_or_reversal),
                                rsi_reversal_falling_bars=int(short_premium_rsi_reversal_falling_bars or 0),
                                rsi_soft_zone_low=float(short_premium_rsi_soft_zone_low or 20),
                                rsi_soft_zone_high=float(short_premium_rsi_soft_zone_high or 45),
                                rsi_reversal_from_rsi=float(short_premium_rsi_reversal_from_rsi or 70),
                            )
                            if leg_detail != "PASS":
                                parts.append(leg_detail)
                            else:
                                parts.append(
                                    f"Option premium composite score {leg_score} < {int(score_threshold)} "
                                    "(crossover/volume vs thresholds)"
                                )
                    failed_msg = "PASS" if signal_eligible else "; ".join(parts)
                    if action_short:
                        side = "SELL"
                        target_price = round(max(0.05, ltp * 0.75), 2)
                        stop_loss_price = round(ltp * 1.35, 2)
                    else:
                        side = "BUY"
                        target_price = round(ltp * 1.08, 2)
                        stop_loss_price = round(ltp * 0.94, 2)
                    gamma_val = float(
                        compute_gamma_from_ltp(spot, float(strike), expiry_date, ltp, opt_type)
                    )
                else:
                    score = int(leg.get("score") or 0)
                    if ivr_bonus > 0 and ivr is not None:
                        try:
                            ivr_val = float(ivr)
                            if ivr_val < ivr_max_threshold:
                                score = min(score_max, score + ivr_bonus)
                        except (TypeError, ValueError):
                            pass
                    leg_chain_eligible = bool(leg.get("signalEligible"))
                    signal_eligible = leg_chain_eligible
                    if long_premium_spot_align and leg_chain_eligible:
                        sr_spot = spot_regime
                        if opt_type == "CE" and sr_spot != "bullish":
                            signal_eligible = False
                        elif opt_type == "PE" and sr_spot != "bearish":
                            signal_eligible = False
                    if action_short:
                        side = "SELL"
                        target_price = round(max(0.05, ltp * 0.92), 2)
                        stop_loss_price = round(ltp * 1.06, 2)
                    else:
                        side = "BUY"
                        target_price = round(ltp * 1.08, 2)
                        stop_loss_price = round(ltp * 0.94, 2)
                    failed_msg = _failed_conditions(
                        bool(leg.get("primaryOk")),
                        bool(leg.get("emaOk")),
                        bool(leg.get("rsiOk")),
                        rsi_min=rsi_min,
                        rsi_max=rsi_max,
                        volume_ok=bool(leg.get("volumeOk")) if include_volume_in_leg_score else None,
                        volume_min_ratio=float(volume_min_ratio) if include_volume_in_leg_score else None,
                    )
                    if long_premium_spot_align and leg_chain_eligible and not signal_eligible:
                        sr_spot = spot_regime
                        if opt_type == "CE":
                            spot_note = (
                                f"NIFTY spot not bullish for CE (spotRegime={sr_spot!r}; need bullish)"
                            )
                        else:
                            spot_note = (
                                f"NIFTY spot not bearish for PE (spotRegime={sr_spot!r}; need bearish)"
                            )
                        failed_msg = spot_note if failed_msg == "PASS" else f"{failed_msg}; {spot_note}"
                    gamma_val = 0.0
                if relaxed_liq:
                    signal_eligible = False
                    liq_note = f"OI/vol below strategy min (≥{strike_min_oi} OI, ≥{strike_min_volume} vol)"
                    failed_msg = liq_note if failed_msg == "PASS" else f"{failed_msg}; {liq_note}"
                vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
                base_conf = (score / conf_denom) * 100
                vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
                confidence = min(99.0, round(base_conf + vol_bonus, 2))
                primary_ok = bool(leg.get("primaryOk"))
                ema_ok = bool(leg.get("emaOk"))
                ema_crossover_ok = bool(leg.get("emaCrossoverOk"))
                rsi_ok = bool(leg.get("rsiOk"))
                volume_ok = bool(leg.get("volumeOk"))
                oi_chg_pct = float(leg.get("oiChgPct") or 0.0)
                target_delta = strike_delta_ce if opt_type == "CE" else strike_delta_pe
                delta_distance = abs(delta - target_delta)
                symbol = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(
                    instrument, expiry_str, strike, opt_type
                )
                leg_rsi = float(leg.get("rsi") or 0.0)
                rsi_prev_leg: float | None = None
                short_premium_rsi_drop = 0.0
                rp_raw = leg.get("rsiPrev")
                if rp_raw is not None:
                    try:
                        pv = float(rp_raw)
                        rsi_prev_leg = round(pv, 2)
                        short_premium_rsi_drop = round(max(0.0, pv - leg_rsi), 4)
                    except (TypeError, ValueError):
                        rsi_prev_leg = None
                        short_premium_rsi_drop = 0.0
                recs.append(
                    {
                        "instrument": instrument,
                        "expiry": expiry_str,
                        "symbol": symbol,
                        "side": side,
                        "entry_price": round(ltp, 2),
                        "target_price": target_price,
                        "stop_loss_price": stop_loss_price,
                        "confidence_score": confidence,
                        "vwap": float(leg.get("vwap") or 0.0),
                        "ema9": float(leg.get("ema9") or 0.0),
                        "ema21": float(leg.get("ema21") or 0.0),
                        "rsi": leg_rsi,
                        "rsi_prev": rsi_prev_leg,
                        "short_premium_rsi_drop": short_premium_rsi_drop,
                        "ivr": _leg_iv_optional(leg),
                        "volume": volume,
                        "avg_volume": float(leg.get("avgVolume") or 0.0),
                        "volume_spike_ratio": vol_ratio,
                        "score": score,
                        "primary_ok": primary_ok,
                        "ema_ok": ema_ok,
                        "ema_crossover_ok": ema_crossover_ok,
                        "rsi_ok": rsi_ok,
                        "volume_ok": volume_ok,
                        "signal_eligible": signal_eligible,
                        "failed_conditions": failed_msg,
                        "spot_price": round(spot, 2),
                        "timeframe": "3m",
                        "refresh_interval_sec": RECOMMENDATION_ENGINE_REFRESH_SEC,
                        "distance_to_atm": distance_to_atm,
                        "strike": strike,
                        "oi": oi,
                        "oi_chg_pct": oi_chg_pct,
                        "buildup": str(leg.get("buildup") or "—"),
                        "delta": delta,
                        "delta_distance": delta_distance,
                        "option_type": opt_type,
                        "gamma": gamma_val if use_short else 0.0,
                    }
                )
        if recs:
            break
    flow_meta: dict[str, Any] = {}
    if not use_short and recs and flow_ranking:
        flow_meta = _apply_long_premium_flow_ranking(
            recs, chain, chain_payload, flow_ranking, expiry_date=expiry_date
        )
    # Snapshot before long eligible cap / short gamma trim — used for evaluation JSONL.
    scanned_before_rank = list(recs)
    if not use_short and recs:
        cap_l = max(1, int(max_strike_recommendations))
        elig_l = [r for r in recs if r.get("signal_eligible")]
        if len(elig_l) > cap_l:
            elig_l.sort(key=_long_premium_rec_sort_key)
            keep_ids = {id(r) for r in elig_l[:cap_l]}
            note_tail = f"eligible_cap_per_refresh(max={cap_l})"
            for r in recs:
                if r.get("signal_eligible") and id(r) not in keep_ids:
                    r["signal_eligible"] = False
                    prev = str(r.get("failed_conditions") or "PASS")
                    r["failed_conditions"] = note_tail if prev == "PASS" else f"{prev}; {note_tail}"
    _sp_rank_rsi = bool(short_premium_rsi_decreasing)
    if use_short and select_strike_by_min_gamma and recs:
        eligible_recs = [r for r in recs if r.get("signal_eligible")]
        if eligible_recs:
            eligible_recs.sort(
                key=lambda rr: _short_premium_eligible_sort_key(
                    rr, rsi_decreasing_rank=_sp_rank_rsi
                )
            )
            cap = max(1, int(max_strike_recommendations))
            recs = eligible_recs[:cap]
        else:
            recs.sort(
                key=lambda rr: _short_premium_eligible_sort_key(
                    rr, rsi_decreasing_rank=_sp_rank_rsi
                )
            )
    elif use_short:
        recs.sort(
            key=lambda rr: _short_premium_eligible_sort_key(
                rr, rsi_decreasing_rank=_sp_rank_rsi
            )
        )
    else:
        recs.sort(key=_long_premium_rec_sort_key)
    snap = _chain_eval_meta(
        expiry_str=expiry_str,
        expiry_date=expiry_date,
        chain_len=len(chain),
        short_leg_diagnostics=short_diag if use_short else None,
    )
    if flow_meta:
        snap.update(flow_meta)
    if use_short:
        snap["short_premium_delta_abs"] = short_delta_gate_note
        snap["short_delta_ce_lo"] = ce_d_lo
        snap["short_delta_ce_hi"] = ce_d_hi
        snap["short_delta_pe_lo"] = pe_d_lo
        snap["short_delta_pe_hi"] = pe_d_hi
        snap["india_vix"] = chain_payload.get("vix")
        if snap["india_vix"] is None and vix_prefetch is not None:
            snap["india_vix"] = vix_prefetch
        snap["short_premium_strike_select"] = (
            "delta_only (VIX→delta band; maxOtmSteps/dATM off)" if short_premium_delta_only_strikes else "otm_steps"
        )
        snap["chain_strikes_each_side"] = chain_half_width
        if short_premium_asymmetric_datm and not short_premium_delta_only_strikes:
            snap["short_premium_ce_datm"] = (
                f"{short_premium_ce_datm_min}..{short_premium_ce_datm_max}"
            )
            snap["short_premium_pe_datm"] = (
                f"{short_premium_pe_datm_min}..{short_premium_pe_datm_max}"
            )
    return (recs, scanned_before_rank, snap)


async def _get_live_candidates_trendpulse_z(
    kite: KiteConnect | None,
    market_provider: Any | None,
    max_strike_distance: int,
    score_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Long CE/PE from PS_z vs VS_z cross + HTF bias; strike filters match rule-based long premium."""
    if market_provider is None and kite is None:
        return []
    tpc = score_params.get("trendpulse_config") or {}
    if not isinstance(tpc, dict):
        tpc = {}
    st_int = str(tpc.get("stInterval", "5minute"))
    htf_int = str(tpc.get("htfInterval", "15minute"))
    days = int(tpc.get("candleDaysBack", 5))
    z_window = int(tpc.get("zWindow", 50))
    slope_k = int(tpc.get("slopeLookback", 4))
    adx_period = int(tpc.get("adxPeriod", 14))
    adx_min = float(tpc.get("adxMin", 18.0))
    htf_ef = int(tpc.get("htfEmaFast", 13))
    htf_es = int(tpc.get("htfEmaSlow", 34))
    iv_rank_max = float(tpc.get("ivRankMaxPercentile", 70.0))

    if market_provider is not None:
        st = await market_provider.index_candles("NIFTY", st_int, days)
        htf = await market_provider.index_candles("NIFTY", htf_int, days)
    else:
        st = await asyncio.to_thread(fetch_index_candles_sync, kite, "NIFTY", st_int, days)
        htf = await asyncio.to_thread(fetch_index_candles_sync, kite, "NIFTY", htf_int, days)
    ev = evaluate_trendpulse_signal(
        st,
        htf,
        z_window=z_window,
        slope_lookback=slope_k,
        adx_period=adx_period,
        adx_min=adx_min,
        htf_ema_fast=htf_ef,
        htf_ema_slow=htf_es,
    )
    if not ev.ok:
        return []

    opt_type = "CE" if ev.cross == "bullish" else "PE"
    score_max = int(score_params.get("score_max", 5))
    score_threshold = int(score_params.get("score_threshold", 3))
    strike_min_oi = int(score_params.get("strike_min_oi", 10000))
    strike_min_volume = int(score_params.get("strike_min_volume", 500))
    strike_delta_ce = float(score_params.get("strike_delta_ce", 0.35))
    strike_delta_pe = float(score_params.get("strike_delta_pe", -0.35))
    strike_max_otm_steps = int(score_params.get("strike_max_otm_steps", 3))
    rsi_min = float(score_params.get("rsi_min", 50))
    rsi_max = float(score_params.get("rsi_max", 75))
    volume_min_ratio = float(score_params.get("volume_min_ratio", 1.5))
    ema_crossover_max_candles = score_params.get("ema_crossover_max_candles")
    adx_period = int(score_params.get("adx_period", 14))
    adx_min_threshold = score_params.get("adx_min_threshold")

    instrument = "NIFTY"
    min_dte_cal = int(tpc.get("minDteCalendarDays", 2))
    nifty_expiry_weekday = tpc.get("niftyWeeklyExpiryWeekday")
    delta_lo = float(tpc.get("deltaMinAbs", 0.40))
    delta_hi = float(tpc.get("deltaMaxAbs", 0.50))
    ext_min = float(tpc.get("extrinsicShareMin", 0.25))
    # minDteCalendarDays > 0: IST calendar DTE >= threshold; optional NIFTY weekly expiry weekday (default Tue).
    if market_provider is not None:
        try:
            expiries, _src = await market_provider.expiries(instrument)
        except Exception:
            expiries = []
        expiry_str = _pick_expiry_from_provider_list(
            expiries,
            min_dte_calendar_days=min_dte_cal,
            nifty_weekly_expiry_weekday=nifty_expiry_weekday,
        )
    elif min_dte_cal > 0:
        expiry_str = pick_expiry_with_min_calendar_dte(
            kite,
            instrument,
            min_dte_days=min_dte_cal,
            weekday=nifty_expiry_weekday,
        )
    else:
        expiry_str = pick_primary_expiry_str(kite, instrument)
    if not expiry_str:
        return []

    indicator_params: dict[str, Any] = {
        "rsi_min": rsi_min,
        "rsi_max": rsi_max,
        "volume_min_ratio": volume_min_ratio,
    }
    if ema_crossover_max_candles is not None:
        indicator_params["max_candles_since_cross"] = ema_crossover_max_candles
    if adx_min_threshold is not None:
        indicator_params["adx_period"] = adx_period
        indicator_params["adx_min_threshold"] = float(adx_min_threshold)

    if market_provider is not None:
        chain_payload = await market_provider.option_chain(
            instrument,
            expiry_str,
            max_strike_distance,
            max_strike_distance,
            True,
        )
    else:
        chain_payload = await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            instrument,
            expiry_str,
            max_strike_distance,
            max_strike_distance,
            score_threshold,
            indicator_params,
        )
    chain = chain_payload.get("chain", [])
    spot = float(chain_payload.get("spot") or 0.0)
    if not chain or spot <= 0:
        return []

    sc_raw = chain_payload.get("spotChgPct")
    pc_raw = chain_payload.get("pcr")
    try:
        spot_chg_f = float(sc_raw) if sc_raw is not None else None
    except (TypeError, ValueError):
        spot_chg_f = None
    try:
        pcr_f = float(pc_raw) if pc_raw is not None else None
    except (TypeError, ValueError):
        pcr_f = None
    ev = apply_trendpulse_hard_gates(
        ev,
        tpc,
        spot_chg_pct=spot_chg_f,
        pcr=pcr_f,
        now_utc=datetime.now(timezone.utc),
    )
    if not ev.ok:
        return []

    opening_blocked = trendpulse_opening_window_blocked(datetime.now(timezone.utc))
    if opening_blocked:
        return []

    expiry_date = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y").date()
    max_prem = float(tpc.get("maxOptionPremiumInr", 80.0))
    select_by_max_gamma = bool(tpc.get("selectStrikeByMaxGamma", True))
    max_strike_recs = max(1, int(tpc.get("maxStrikeRecommendations", 1)))

    step = 50
    atm = round(spot / step) * step
    conf_denom = max(1, score_max)
    tf_label = "5m" if "5" in st_int else st_int.replace("minute", "m")
    # Two-tier: strict liquidity only (no relaxed OI/vol waiver).
    recs: list[dict[str, Any]] = []
    for row in chain:
        strike = int(float(row.get("strike", 0)))
        distance_to_atm = int((strike - atm) / step)
        if abs(distance_to_atm) > strike_max_otm_steps:
            continue
        leg_key, ot = ("call", "CE") if opt_type == "CE" else ("put", "PE")
        leg = row.get(leg_key) or {}
        oi = int(float(leg.get("oi") or 0))
        volume = int(float(leg.get("volume") or 0))
        if oi < strike_min_oi or volume < strike_min_volume:
            continue
        ivr = leg.get("ivr")
        if ivr is not None:
            try:
                if float(ivr) > iv_rank_max:
                    continue
            except (TypeError, ValueError):
                pass
        ltp = float(leg.get("ltp") or 0.0)
        if max_prem > 0 and ltp >= max_prem:
            continue
        delta = float(leg.get("delta") or 0.0)
        if not delta_abs_in_band(delta, delta_lo, delta_hi):
            continue
        ext_share = option_extrinsic_share(ltp, spot, strike, opt_type)
        if ext_share is None or ext_share < ext_min:
            continue
        score = score_max
        signal_eligible = True
        vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
        base_conf = (score / conf_denom) * 100
        vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
        confidence = min(99.0, round(base_conf + vol_bonus, 2))
        target_delta = strike_delta_ce if opt_type == "CE" else strike_delta_pe
        delta_distance = abs(delta - target_delta)
        gamma_val = float(
            compute_gamma_from_ltp(spot, float(strike), expiry_date, ltp, opt_type)
        )
        symbol = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(
            instrument, expiry_str, strike, opt_type
        )
        fc = "PASS"
        tier_payload = {
            "trendpulse": {
                "htf_bias": ev.htf_bias,
                "cross": ev.cross,
                "ps_z": round(ev.ps_z, 4),
                "vs_z": round(ev.vs_z, 4),
                "adx_st": round(ev.adx_st, 2),
                "reason": ev.reason,
                "tier1": {
                    "ok": True,
                    "opening_block": False,
                    "cross": ev.cross,
                    "htf_bias": ev.htf_bias,
                    "adx": round(ev.adx_st, 2),
                },
                "tier2": {
                    "delta": round(delta, 4),
                    "delta_band": [delta_lo, delta_hi],
                    "delta_in_band": True,
                    "extrinsic_share": round(ext_share, 4),
                    "extrinsic_min": ext_min,
                    "strict_liquidity": True,
                    "expiry": expiry_str,
                    "min_dte_calendar_days": min_dte_cal,
                    "nifty_weekly_expiry_weekday": nifty_expiry_weekday,
                    "max_option_premium_inr": max_prem if max_prem > 0 else None,
                    "gamma": round(gamma_val, 8),
                    "select_strike_by_max_gamma": select_by_max_gamma,
                },
            },
        }
        recs.append(
            {
                "instrument": instrument,
                "expiry": expiry_str,
                "symbol": symbol,
                "side": "BUY",
                "entry_price": round(ltp, 2),
                "target_price": round(ltp * 1.08, 2),
                "stop_loss_price": round(ltp * 0.94, 2),
                "confidence_score": confidence,
                "vwap": float(leg.get("vwap") or 0.0),
                "ema9": float(leg.get("ema9") or 0.0),
                "ema21": float(leg.get("ema21") or 0.0),
                "rsi": float(leg.get("rsi") or 0.0),
                "ivr": _leg_iv_optional(leg),
                "volume": volume,
                "avg_volume": float(leg.get("avgVolume") or 0.0),
                "volume_spike_ratio": vol_ratio,
                "score": score,
                "primary_ok": bool(leg.get("primaryOk")),
                "ema_ok": bool(leg.get("emaOk")),
                "ema_crossover_ok": bool(leg.get("emaCrossoverOk")),
                "rsi_ok": bool(leg.get("rsiOk")),
                "volume_ok": bool(leg.get("volumeOk")),
                "signal_eligible": signal_eligible,
                "failed_conditions": fc,
                "spot_price": round(spot, 2),
                "timeframe": tf_label,
                "refresh_interval_sec": RECOMMENDATION_ENGINE_REFRESH_SEC,
                "distance_to_atm": distance_to_atm,
                "oi": oi,
                "oi_chg_pct": float(leg.get("oiChgPct") or 0.0),
                "delta": delta,
                "delta_distance": delta_distance,
                "option_type": opt_type,
                "gamma": gamma_val,
                **tier_payload,
            }
        )

    if select_by_max_gamma:
        recs.sort(
            key=lambda x: (
                -float(x.get("gamma") or 0.0),
                -x["score"],
                -x["volume_spike_ratio"],
                -x["oi_chg_pct"],
                x["delta_distance"],
                abs(x["distance_to_atm"]),
            )
        )
        recs = recs[:max_strike_recs]
    else:
        recs.sort(
            key=lambda x: (
                -x["score"],
                -x["volume_spike_ratio"],
                -x["oi_chg_pct"],
                x["delta_distance"],
                abs(x["distance_to_atm"]),
            )
        )
    return recs


async def _get_live_candidates_heuristic(
    kite: "KiteConnect | None",
    market_provider: Any | None,
    max_strike_distance: int,
    score_threshold: float = 3.0,
    score_max: float = 5.0,
    heuristics_config: dict | None = None,
    strike_min_oi: int = 10000,
    strike_min_volume: int = 500,
    strike_delta_ce: float = 0.35,
    strike_delta_pe: float = -0.35,
    strike_max_otm_steps: int = 3,
    rsi_min: float = 45,
    rsi_max: float = 75,
    heuristics_config_ce: dict | None = None,
    heuristics_config_pe: dict | None = None,
    score_threshold_ce: float | None = None,
    score_threshold_pe: float | None = None,
    enhancement_cfg: HeuristicEnhancementConfig | None = None,
) -> list[dict]:
    """Generate recommendations using multi-heuristic weighted scoring + optional strike/DTE/joint-OI enhancements."""
    instrument = "NIFTY"
    if market_provider is not None:
        try:
            expiries, _src = await market_provider.expiries(instrument)
        except Exception:
            expiries = []
        expiry_str = _pick_expiry_from_provider_list(
            expiries,
            min_dte_calendar_days=0,
            nifty_weekly_expiry_weekday=None,
        )
    else:
        expiry_str = pick_primary_expiry_str(kite, instrument)
    if not expiry_str:
        return []
    try:
        expiry_day = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y").date()
    except ValueError:
        expiry_day = date.today()
    indicator_params: dict[str, Any] = {
        "rsi_min": float(rsi_min),
        "rsi_max": float(rsi_max),
        "volume_min_ratio": 0.8,
    }
    if market_provider is not None:
        chain_payload = await market_provider.option_chain(
            instrument,
            expiry_str,
            max_strike_distance,
            max_strike_distance,
            True,
        )
    else:
        chain_payload = await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            instrument,
            expiry_str,
            max_strike_distance,
            max_strike_distance,
            2,
            indicator_params,
        )
    chain = chain_payload.get("chain", [])
    spot = float(chain_payload.get("spot") or 0.0)
    spot_chg_pct = chain_payload.get("spotChgPct")
    if spot_chg_pct is not None:
        spot_chg_pct = float(spot_chg_pct)
    if not chain or spot <= 0:
        return []
    step = 50
    atm = round(spot / step) * step
    chain_context = {
        "spot": spot,
        "pcr": chain_payload.get("pcr"),
        "atm_strike": atm,
    }
    enh = enhancement_cfg
    use_enh = enh is not None and enh.enabled

    thr_ce = float(score_threshold_ce) if score_threshold_ce is not None else float(score_threshold)
    thr_pe = float(score_threshold_pe) if score_threshold_pe is not None else float(score_threshold)

    spot_dir = (
        spot_direction(spot_chg_pct, enh.flat_spot_band_pct)
        if use_enh and enh is not None
        else "flat"
    )

    def pick_heuristics(opt: str) -> dict | None:
        if opt == "CE" and heuristics_config_ce:
            return heuristics_config_ce
        if opt == "PE" and heuristics_config_pe:
            return heuristics_config_pe
        return heuristics_config

    liq_tiers_h: list[tuple[int, int, bool]] = [(strike_min_oi, strike_min_volume, False)]
    if strike_min_oi > 0 or strike_min_volume > 0:
        liq_tiers_h.append((0, 0, True))
    recs: list[dict] = []
    for min_oi, min_vol, relaxed_liq in liq_tiers_h:
        recs = []
        for row in chain:
            strike = int(float(row.get("strike", 0)))
            distance_to_atm = int((strike - atm) / step)
            if abs(distance_to_atm) > strike_max_otm_steps:
                continue
            for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
                leg = row.get(leg_key) or {}
                oi = int(float(leg.get("oi") or 0))
                volume = int(float(leg.get("volume") or 0))
                if oi < min_oi or volume < min_vol:
                    continue
                if use_enh and enh is not None and not passes_directional_gate(opt_type, spot_chg_pct, enh):
                    continue

                hcfg = pick_heuristics(opt_type)
                ltp_strong = None
                oi_w_ltp = None
                max_pair_share = None
                if use_enh and enh is not None:
                    ltp_strong = enh.ltp_strong_pct
                    oi_w_ltp = enh.oi_weight_when_ltp_strong
                    max_pair_share = enh.max_ltp_oi_combined_weight_share

                weighted_score, reasons = heuristic_score_leg(
                    leg,
                    opt_type,
                    strike,
                    atm,
                    chain_context,
                    hcfg,
                    delta_ce=strike_delta_ce,
                    delta_pe=strike_delta_pe,
                    rsi_min=rsi_min,
                    rsi_max=rsi_max,
                    ltp_strong_pct=ltp_strong,
                    oi_weight_when_ltp_strong=oi_w_ltp,
                    max_ltp_oi_combined_weight_share=max_pair_share,
                )
                ltp = float(leg.get("ltp") or 0.0)
                vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
                oi_chg_pct = float(leg.get("oiChgPct") or 0.0)
                oi_chg_raw = leg.get("oiChgPct")

                enhanced_score = float(weighted_score)
                extra_reasons = list(reasons)

                if use_enh and enh is not None:
                    mx_pct = moneyness_pct_abs(strike, spot)
                    # Override uses raw weighted heuristic (before joint/volume multipliers)
                    if not passes_moneyness_hard_filter(mx_pct, float(weighted_score), enh):
                        continue
                    oi_dir = oi_direction(oi_chg_raw, enh.flat_oi_pct)
                    jm = joint_score_multiplier(opt_type, spot_dir, oi_dir, enh)
                    enhanced_score = round(enhanced_score * jm, 3)
                    vm = volume_oi_multiplier(vol_ratio, oi_chg_raw, enh)
                    enhanced_score = round(enhanced_score * vm, 3)
                    if jm != 1.0:
                        extra_reasons.append(f"joint×{jm:.2f}")
                    if vm != 1.0:
                        extra_reasons.append(f"volOI×{vm:.2f}")
                    capped, matrix_ok, matrix_note = apply_moneyness_dte_rules(
                        enhanced_score, float(strike), spot, expiry_day, enh
                    )
                    enhanced_score = capped
                    if matrix_note:
                        extra_reasons.append(matrix_note)
                    thr = thr_ce if opt_type == "CE" else thr_pe
                    signal_eligible = matrix_ok and enhanced_score >= thr
                else:
                    signal_eligible = enhanced_score >= score_threshold

                if relaxed_liq:
                    signal_eligible = False
                    extra_reasons.append("below catalog min OI/vol")

                base_conf = (enhanced_score / max(1.0, score_max)) * 100
                vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
                confidence = min(99.0, round(base_conf + vol_bonus, 2))
                heuristic_reasons = "; ".join(extra_reasons) if extra_reasons else "PASS"
                symbol = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(
                    instrument, expiry_str, strike, opt_type
                )
                delta = float(leg.get("delta") or 0.0)
                target_delta = strike_delta_ce if opt_type == "CE" else strike_delta_pe
                delta_distance = abs(delta - target_delta)
                recs.append(
                    {
                        "instrument": instrument,
                        "expiry": expiry_str,
                        "symbol": symbol,
                        "side": "BUY",
                        "entry_price": round(ltp, 2),
                        "target_price": round(ltp * 1.08, 2),
                        "stop_loss_price": round(ltp * 0.94, 2),
                        "confidence_score": confidence,
                        "vwap": float(leg.get("vwap") or 0.0),
                        "ema9": float(leg.get("ema9") or 0.0),
                        "ema21": float(leg.get("ema21") or 0.0),
                        "rsi": float(leg.get("rsi") or 0.0),
                        "ivr": _leg_iv_optional(leg),
                        "volume": volume,
                        "avg_volume": float(leg.get("avgVolume") or 0.0),
                        "volume_spike_ratio": vol_ratio,
                        "score": round(enhanced_score, 2),
                        "primary_ok": bool(leg.get("primaryOk")),
                        "ema_ok": bool(leg.get("emaOk")),
                        "ema_crossover_ok": bool(leg.get("emaCrossoverOk", False)),
                        "rsi_ok": bool(leg.get("rsiOk")),
                        "volume_ok": bool(leg.get("volumeOk")),
                        "signal_eligible": signal_eligible,
                        "failed_conditions": heuristic_reasons,
                        "heuristic_reasons": extra_reasons,
                        "spot_price": round(spot, 2),
                        "timeframe": "3m",
                        "refresh_interval_sec": RECOMMENDATION_ENGINE_REFRESH_SEC,
                        "distance_to_atm": distance_to_atm,
                        "oi": oi,
                        "oi_chg_pct": oi_chg_pct,
                        "delta": delta,
                        "delta_distance": delta_distance,
                        "option_type": opt_type,
                    }
                )
        if use_enh and enh is not None:
            recs = select_best_per_side(recs, enh)
        if recs:
            break

    recs.sort(
        key=lambda x: (
            -x["score"],
            -x["volume_spike_ratio"],
            -x["oi_chg_pct"],
            x["delta_distance"],
            abs(x["distance_to_atm"]),
        )
    )
    return recs


async def get_strategy_score_params(
    strategy_id: str, strategy_version: str, user_id: int | None = None
) -> dict:
    """Load strategy JSON from catalog, then merge per-user Settings on top so UI edits (e.g. IVR bands) apply."""

    def _parse_details(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                return {}
        return raw if isinstance(raw, dict) else {}

    catalog_row = await fetchrow(
        """
        SELECT strategy_details_json FROM s004_strategy_catalog
        WHERE strategy_id = $1 AND version = $2
        """,
        strategy_id,
        strategy_version,
    )
    catalog_details = _parse_details(catalog_row.get("strategy_details_json") if catalog_row else None)
    details = dict(catalog_details)
    if user_id is not None:
        user_row = await fetchrow(
            """
            SELECT strategy_details_json FROM s004_user_strategy_settings
            WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
            """,
            user_id,
            strategy_id,
            strategy_version,
        )
        if user_row:
            user_details = _parse_details(user_row.get("strategy_details_json"))
            if user_details:
                details = _deep_merge_strategy_details(details, user_details)
    indicators = details.get("indicators") or {}
    if not isinstance(indicators, dict):
        indicators = {}
    ivr_cfg = indicators.get("ivr") or {}
    if not isinstance(ivr_cfg, dict):
        ivr_cfg = {}
    ema_cross_cfg = indicators.get("emaCrossover") or {}
    if not isinstance(ema_cross_cfg, dict):
        ema_cross_cfg = {}
    adx_cfg = indicators.get("adx") or {}
    if not isinstance(adx_cfg, dict):
        adx_cfg = {}
    strike_cfg = details.get("strikeSelection") or {}
    if not isinstance(strike_cfg, dict):
        strike_cfg = {}
    rsi_cfg = indicators.get("rsi") or {}
    if not isinstance(rsi_cfg, dict):
        rsi_cfg = {}
    vol_cfg = indicators.get("volumeSpike") or {}
    if not isinstance(vol_cfg, dict):
        vol_cfg = {}

    def _num_float(v: Any, default: float) -> float:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _num_int(v: Any, default: int) -> int:
        if v is None:
            return default
        if isinstance(v, bool):
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            try:
                return int(float(str(v).strip()))
            except (TypeError, ValueError):
                return default

    strategy_type = str(details.get("strategyType", "rule-based")).strip().lower()
    heuristics_cfg = details.get("heuristics")
    if not isinstance(heuristics_cfg, dict):
        heuristics_cfg = {}
    heuristics_ce = details.get("heuristicsCE")
    if not isinstance(heuristics_ce, dict):
        heuristics_ce = None
    heuristics_pe = details.get("heuristicsPE")
    if not isinstance(heuristics_pe, dict):
        heuristics_pe = None
    heuristic_enhancements = details.get("heuristicEnhancements")
    if not isinstance(heuristic_enhancements, dict):
        heuristic_enhancements = None
    _stc = details.get("scoreThresholdCE")
    score_threshold_ce = float(_stc) if isinstance(_stc, (int, float)) else None
    _stp = details.get("scoreThresholdPE")
    score_threshold_pe = float(_stp) if isinstance(_stp, (int, float)) else None

    catalog_position_intent = str(catalog_details.get("positionIntent", "long_premium")).strip().lower()
    if catalog_position_intent not in ("long_premium", "short_premium"):
        catalog_position_intent = "long_premium"
    position_intent = catalog_position_intent
    execution_action_intent = str(
        details.get("tradeActionIntent", details.get("positionIntent", position_intent))
    ).strip().lower()
    if position_intent not in ("long_premium", "short_premium"):
        position_intent = "long_premium"
    if execution_action_intent not in ("long_premium", "short_premium"):
        execution_action_intent = position_intent
    ivr_min_threshold = (
        float(ivr_cfg["minThreshold"]) if isinstance(ivr_cfg.get("minThreshold"), (int, float)) else 0.0
    )
    _dmin = strike_cfg.get("deltaMinAbs")
    _dmax = strike_cfg.get("deltaMaxAbs")
    strike_delta_min_abs = (
        float(_dmin)
        if isinstance(_dmin, (int, float))
        else (0.29 if position_intent == "short_premium" else 0.0)
    )
    strike_delta_max_abs = (
        float(_dmax)
        if isinstance(_dmax, (int, float))
        else (0.35 if position_intent == "short_premium" else 1.0)
    )

    tpz_raw = details.get("trendPulseZ")
    if not isinstance(tpz_raw, dict):
        tpz_raw = {}
    try:
        trendpulse_config = resolve_trendpulse_z_config(tpz_raw)
    except Exception:
        _logger.exception(
            "get_strategy_score_params: trendPulseZ resolve failed strategy=%s version=%s",
            strategy_id,
            strategy_version,
        )
        trendpulse_config = resolve_trendpulse_z_config({})

    strike_max_otm_steps = _num_int(strike_cfg.get("maxOtmSteps", 3), 3)
    # Guardrail for TrendSnap Momentum: never go beyond +/-3 strikes from ATM on NIFTY.
    if strategy_id == "strat-trendsnap-momentum":
        strike_max_otm_steps = min(strike_max_otm_steps, 3)

    _mdc_raw = strike_cfg.get("minDteCalendarDays")
    if _mdc_raw is None:
        min_dte_calendar_days = 3 if position_intent == "short_premium" else 0
    else:
        try:
            min_dte_calendar_days = int(_mdc_raw)
        except (TypeError, ValueError):
            min_dte_calendar_days = 3 if position_intent == "short_premium" else 0
    nifty_weekly_expiry_weekday = (
        parse_nifty_weekly_expiry_weekday(strike_cfg.get("niftyWeeklyExpiryWeekday"))
        if position_intent == "short_premium"
        else None
    )
    _ssmg = strike_cfg.get("selectStrikeByMinGamma")
    if _ssmg is None:
        select_strike_by_min_gamma = position_intent == "short_premium"
    else:
        select_strike_by_min_gamma = bool(_ssmg)
    _msr = strike_cfg.get("maxStrikeRecommendations")
    _msr_i = _num_int(_msr, 1)
    max_strike_recommendations = _msr_i if _msr_i >= 1 else 1

    score_max_val = _num_int(details.get("scoreMax", 6), 6)
    auto_trade_raw = _num_float(details.get("autoTradeScoreThreshold", 4), 4.0)
    auto_trade_clamped = min(auto_trade_raw, float(score_max_val)) if score_max_val > 0 else auto_trade_raw
    if auto_trade_clamped < auto_trade_raw:
        _logger.warning(
            "autoTradeScoreThreshold %s exceeds scoreMax %s; using %s (strategy=%s version=%s)",
            auto_trade_raw,
            score_max_val,
            auto_trade_clamped,
            strategy_id,
            strategy_version,
        )

    result: dict[str, Any] = {
        "strategy_type": strategy_type,
        "position_intent": position_intent,
        "execution_action_intent": execution_action_intent,
        "ivr_min_threshold": ivr_min_threshold,
        "ivr_leg_max_threshold": _num_float(ivr_cfg.get("maxLegThreshold"), 0.0),
        "strike_delta_min_abs": strike_delta_min_abs,
        "strike_delta_max_abs": strike_delta_max_abs,
        "heuristics": heuristics_cfg,
        "heuristics_ce": heuristics_ce,
        "heuristics_pe": heuristics_pe,
        "heuristic_enhancements": heuristic_enhancements,
        "score_threshold_ce": score_threshold_ce,
        "score_threshold_pe": score_threshold_pe,
        "score_threshold": _num_float(details.get("scoreThreshold", 3), 3.0),
        "score_max": score_max_val,
        "auto_trade_score_threshold": auto_trade_clamped,
        "ivr_max_threshold": _num_float(ivr_cfg.get("maxThreshold", 20), 20.0),
        "ivr_bonus": _num_int(ivr_cfg.get("bonus", 0), 0),
        "ema_crossover_max_candles": ema_cross_cfg.get("maxCandlesSinceCross"),
        "adx_period": _num_int(adx_cfg.get("period", 14), 14),
        "adx_min_threshold": adx_cfg.get("minThreshold"),
        "rsi_min": _num_float(rsi_cfg.get("min", 50), 50.0),
        "rsi_max": _num_float(rsi_cfg.get("max", 75), 75.0),
        "volume_min_ratio": _num_float(vol_cfg.get("minRatio", 1.5), 1.5),
        "strike_min_oi": _num_int(strike_cfg.get("minOi", 10000), 10000),
        "strike_min_volume": _num_int(strike_cfg.get("minVolume", 500), 500),
        "strike_delta_ce": _num_float(strike_cfg.get("deltaPreferredCE", 0.35), 0.35),
        "strike_delta_pe": _num_float(strike_cfg.get("deltaPreferredPE", -0.35), -0.35),
        "strike_max_otm_steps": strike_max_otm_steps,
        "trendpulse_config": trendpulse_config,
        "min_dte_calendar_days": min_dte_calendar_days,
        "nifty_weekly_expiry_weekday": nifty_weekly_expiry_weekday,
        "select_strike_by_min_gamma": select_strike_by_min_gamma,
        "max_strike_recommendations": max_strike_recommendations,
        "include_ema_crossover_in_score": bool(details.get("includeEmaCrossoverInScore", True)),
        "strict_bullish_comparisons": bool(details.get("strictBullishComparisons", False)),
        "spot_regime_mode": str(details.get("spotRegimeMode", "")).strip().lower(),
        "include_volume_in_leg_score": bool(details.get("includeVolumeInLegScore", True)),
        "spot_regime_satisfied_score": _num_int(details.get("spotRegimeSatisfiedScore", 5), 5),
    }
    if position_intent == "short_premium":
        _asym_raw = strike_cfg.get("shortPremiumAsymmetricDatm")
        result["short_premium_asymmetric_datm"] = (
            bool(_asym_raw) if isinstance(_asym_raw, bool) else str(_asym_raw).strip().lower() in {"1", "true", "yes"}
        )
        _sce_lo = _num_int(strike_cfg.get("shortPremiumCeMinSteps"), 2)
        _sce_hi = _num_int(strike_cfg.get("shortPremiumCeMaxSteps"), 4)
        _spe_lo = _num_int(strike_cfg.get("shortPremiumPeMinSteps"), -4)
        _spe_hi = _num_int(strike_cfg.get("shortPremiumPeMaxSteps"), 2)
        if _sce_lo > _sce_hi:
            _sce_lo, _sce_hi = _sce_hi, _sce_lo
        if _spe_lo > _spe_hi:
            _spe_lo, _spe_hi = _spe_hi, _spe_lo
        result["short_premium_ce_datm_min"] = _sce_lo
        result["short_premium_ce_datm_max"] = _sce_hi
        result["short_premium_pe_datm_min"] = _spe_lo
        result["short_premium_pe_datm_max"] = _spe_hi
        bands = _normalize_short_premium_delta_vix_bands(strike_cfg)
        result["short_premium_delta_vix_bands"] = bands
        _pdo = strike_cfg.get("shortPremiumDeltaOnlyStrikes")
        # VIX delta bands imply strike selection by delta; otm_steps + narrow ±strikes often yields zero in-band legs.
        if bands is not None:
            result["short_premium_delta_only_strikes"] = True
        elif isinstance(_pdo, bool):
            result["short_premium_delta_only_strikes"] = _pdo
        else:
            result["short_premium_delta_only_strikes"] = False
        _slm = strike_cfg.get("shortPremiumLegScoreMode")
        result["short_premium_leg_score_mode"] = (
            str(_slm).strip().lower() if _slm is not None and str(_slm).strip() else ""
        )
        result["short_premium_rsi_below"] = _num_float(strike_cfg.get("shortPremiumRsiBelow"), 50.0)
        _srdb = strike_cfg.get("shortPremiumRsiDirectBand")
        result["short_premium_rsi_direct_band"] = (
            bool(_srdb) if isinstance(_srdb, bool) else str(_srdb or "").strip().lower() in {"1", "true", "yes"}
        )
        _srdc = strike_cfg.get("shortPremiumRsiDecreasing")
        result["short_premium_rsi_decreasing"] = (
            bool(_srdc) if isinstance(_srdc, bool) else str(_srdc or "").strip().lower() in {"1", "true", "yes"}
        )
        result["short_premium_ivr_skew_min"] = _num_float(strike_cfg.get("shortPremiumIvrSkewMin"), 5.0)
        _pvc = strike_cfg.get("shortPremiumPcrBonusVsChain", True)
        if isinstance(_pvc, str):
            result["short_premium_pcr_bonus_vs_chain"] = _pvc.strip().lower() in {"1", "true", "yes"}
        else:
            result["short_premium_pcr_bonus_vs_chain"] = bool(_pvc)
        result["short_premium_pcr_chain_epsilon"] = _num_float(strike_cfg.get("shortPremiumPcrChainEpsilon"), 0.0)
        result["short_premium_pcr_min_for_sell_ce"] = strike_cfg.get("shortPremiumPcrMinForSellCe")
        result["short_premium_pcr_max_for_sell_pe"] = strike_cfg.get("shortPremiumPcrMaxForSellPe")
        result["short_premium_expansion_block_rsi"] = _num_float(
            strike_cfg.get("shortPremiumExpansionBlockRsi"), 0.0
        )
        result["short_premium_vwap_weakness_min_pct"] = _num_float(
            strike_cfg.get("shortPremiumVwapWeaknessMinPct"), 0.0
        )
        result["short_premium_min_momentum_points"] = _num_int(
            strike_cfg.get("shortPremiumMinMomentumPoints"), 0
        )
        result["short_premium_ghost_rsi_drop_pts"] = _num_float(
            strike_cfg.get("shortPremiumGhostRsiDropPts"), 0.0
        )
        _szor = strike_cfg.get("shortPremiumRsiZoneOrReversal")
        result["short_premium_rsi_zone_or_reversal"] = (
            bool(_szor) if isinstance(_szor, bool) else str(_szor or "").strip().lower() in {"1", "true", "yes"}
        )
        result["short_premium_rsi_soft_zone_low"] = _num_float(
            strike_cfg.get("shortPremiumRsiSoftZoneLow"), 20.0
        )
        result["short_premium_rsi_soft_zone_high"] = _num_float(
            strike_cfg.get("shortPremiumRsiSoftZoneHigh"), 45.0
        )
        result["short_premium_rsi_reversal_from_rsi"] = _num_float(
            strike_cfg.get("shortPremiumRsiReversalFromRsi"), 70.0
        )
        result["short_premium_rsi_reversal_falling_bars"] = max(
            0, min(20, _num_int(strike_cfg.get("shortPremiumRsiReversalFallingBars"), 0))
        )
        result["short_premium_vwap_eligible_buffer_pct"] = max(
            0.0, min(3.0, _num_float(strike_cfg.get("shortPremiumVwapEligibleBufferPct"), 0.0))
        )
        _tfvw = strike_cfg.get("shortPremiumThreeFactorRequireLtpBelowVwapForEligible")
        if _tfvw is None:
            result["short_premium_three_factor_require_ltp_below_vwap"] = True
        else:
            result["short_premium_three_factor_require_ltp_below_vwap"] = (
                bool(_tfvw) if isinstance(_tfvw, bool) else str(_tfvw).strip().lower() in {"1", "true", "yes"}
            )
        if strike_cfg.get("shortPremiumIvrMinCe") is not None:
            result["short_premium_ivr_min_ce"] = _num_float(strike_cfg.get("shortPremiumIvrMinCe"), 0.0)
        else:
            result["short_premium_ivr_min_ce"] = None
        if strike_cfg.get("shortPremiumIvrMinPe") is not None:
            result["short_premium_ivr_min_pe"] = _num_float(strike_cfg.get("shortPremiumIvrMinPe"), 0.0)
        else:
            result["short_premium_ivr_min_pe"] = None
    result["require_rsi_for_eligible"] = bool(details.get("requireRsiForEligible", False))
    result["long_premium_spot_align"] = bool(details.get("longPremiumSpotAlign", False))
    _mve = strike_cfg.get("minVolumeEarlySession")
    try:
        result["min_volume_early_session"] = (
            int(_mve) if _mve is not None and not isinstance(_mve, bool) else None
        )
    except (TypeError, ValueError):
        result["min_volume_early_session"] = None
    result["early_session_end_hour_ist"] = _num_int(strike_cfg.get("earlySessionEndHourIST"), 0)
    result["flow_ranking"] = _parse_flow_ranking_cfg(strike_cfg.get("flowRanking"))
    return result


async def get_score_params_for_active_subscription(user_id: int) -> dict[str, Any]:
    """Catalog + merged user settings for the user's ACTIVE subscription (one helper for list/GET paths)."""
    sid, ver = await _get_user_strategy(user_id)
    return await get_strategy_score_params(sid, ver, user_id)


async def _get_user_strategy(user_id: int) -> tuple[str, str]:
    """Get user's active strategy (strategy_id, version) from settings with ACTIVE subscription."""
    row = await fetchrow(
        """
        SELECT s.strategy_id, s.strategy_version
        FROM s004_user_strategy_settings s
        JOIN s004_strategy_subscriptions sub
            ON sub.user_id = s.user_id AND sub.strategy_id = s.strategy_id AND sub.strategy_version = s.strategy_version
        WHERE s.user_id = $1 AND sub.status = 'ACTIVE'
        ORDER BY s.updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if row:
        return str(row["strategy_id"]), str(row["strategy_version"])
    # Do not fall back to "latest settings" without an ACTIVE subscription — avoids running an old
    # strategy after the user subscribed elsewhere only.
    # User has no settings row matching an ACTIVE sub — check ACTIVE subscription (Marketplace-only users).
    # Ensure settings exist so future requests use the preferred path.
    row = await fetchrow(
        """
        SELECT strategy_id, strategy_version FROM s004_strategy_subscriptions
        WHERE user_id = $1 AND status = 'ACTIVE'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if row:
        sid, ver = str(row["strategy_id"]), str(row["strategy_version"])
        from app.services.marketplace_service import ensure_user_strategy_settings
        await ensure_user_strategy_settings(user_id, sid, ver)
        return sid, ver
    return "strat-trendsnap-momentum", "1.0.0"


async def _get_user_max_strike_distance(user_id: int) -> int:
    row = await fetchrow(
        """
        SELECT max_strike_distance_atm
        FROM s004_user_strategy_settings
        WHERE user_id = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    try:
        dist = int((row["max_strike_distance_atm"] if row else 5) or 5)
    except (TypeError, ValueError):
        dist = 5
    return max(1, min(20, dist))


async def _get_subscribed_user_ids(strategy_id: str, strategy_version: str) -> list[int]:
    """Return all user_ids with ACTIVE subscription to this strategy."""
    rows = await fetch(
        """
        SELECT user_id FROM s004_strategy_subscriptions
        WHERE strategy_id = $1 AND strategy_version = $2 AND status = 'ACTIVE'
        """,
        strategy_id,
        strategy_version,
    )
    return [int(r["user_id"]) for r in rows or []]


async def _is_admin(user_id: int) -> bool:
    """Return True if user has ADMIN role."""
    row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    return row is not None and str(row.get("role", "")).upper() == "ADMIN"


async def _get_all_active_strategies() -> list[tuple[str, str]]:
    """Return all (strategy_id, strategy_version) with at least one ACTIVE subscriber."""
    rows = await fetch(
        """
        SELECT DISTINCT strategy_id, strategy_version
        FROM s004_strategy_subscriptions
        WHERE status = 'ACTIVE'
        ORDER BY strategy_id, strategy_version
        """
    )
    return [(str(r["strategy_id"]), str(r["strategy_version"])) for r in rows or []]


async def ensure_recommendations(user_id: int, kite: KiteConnect | None = None) -> bool:
    """Generate recommendations for all users subscribed to the strategy. Uses fallback Kite if user has none.
    Admin: generates for ALL active strategies so Trades screen shows recommendations from every strategy.

    Returns True if a full run executed (option chain + evaluation), False if skipped due to throttle."""
    # Fast path: do not wait on the per-user lock when a refresh just ran (avoids piling HTTP polls behind a slow chain).
    now_ts = time.time()
    last = _REC_CACHE_TS.get(user_id)
    if last is not None and (now_ts - last) < RECOMMENDATION_ENGINE_REFRESH_SEC:
        return False

    lock = _ensure_lock_for(user_id)
    async with lock:
        now_ts = time.time()
        last = _REC_CACHE_TS.get(user_id)
        if last is not None and (now_ts - last) < RECOMMENDATION_ENGINE_REFRESH_SEC:
            return False

        await _ensure_recommendations_locked(user_id, kite)
        return True


async def _ensure_recommendations_locked(user_id: int, kite: KiteConnect | None) -> None:
    from app.services.broker_runtime import ZerodhaProvider, resolve_broker_context

    is_admin = await _is_admin(user_id)
    if is_admin:
        strategy_list = await _get_all_active_strategies()
        if not strategy_list:
            strategy_list = [await _get_user_strategy(user_id)]
    else:
        sid, ver = await _get_user_strategy(user_id)
        strategy_list = [(sid, ver)]

    broker_ctx = await resolve_broker_context(user_id, mode="PAPER")
    market_provider = broker_ctx.market_data
    using_zerodha_path = market_provider is None or isinstance(market_provider, ZerodhaProvider)
    if using_zerodha_path:
        if kite is None and isinstance(market_provider, ZerodhaProvider):
            kite = market_provider.kite
        kite = kite or await _get_kite_for_any_user()
    else:
        # Broker-agnostic mode: when an active non-Zerodha market provider exists,
        # do not inject a Zerodha/Kite fallback dependency.
        kite = None
    kite_live_ok = False
    if kite is not None:
        try:
            kite_live_ok = bool(await asyncio.to_thread(verify_kite_session_sync, kite))
        except Exception:
            kite_live_ok = False
    if not kite_live_ok:
        kite = None
    if market_provider is None and kite is None:
        _logger.info(
            "ensure_recommendations: skip refresh user_id=%s — no live market-data session (source=%s broker=%s)",
            user_id,
            broker_ctx.source,
            broker_ctx.broker_code,
        )
        _REC_CACHE_TS[user_id] = time.time()
        return
    max_strike_distance = await _get_user_max_strike_distance(user_id)

    for strategy_id, strategy_version in strategy_list:
        if is_admin:
            from app.services.marketplace_service import ensure_user_strategy_settings
            await ensure_user_strategy_settings(user_id, strategy_id, strategy_version)
            subscribed_users = [user_id]
        else:
            subscribed_users = await _get_subscribed_user_ids(strategy_id, strategy_version)
            if not subscribed_users:
                subscribed_users = [user_id]

        try:
            score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
        except Exception:
            _logger.exception(
                "ensure_recommendations: get_strategy_score_params failed strategy=%s version=%s",
                strategy_id,
                strategy_version,
            )
            for uid in subscribed_users:
                _REC_CACHE_TS[uid] = time.time()
            continue

        strategy_type = score_params.get("strategy_type", "rule-based")
        score_threshold = score_params["score_threshold"]
        score_max = score_params["score_max"]
        ivr_max_threshold = score_params.get("ivr_max_threshold", 20.0)
        ivr_bonus = score_params.get("ivr_bonus", 0)
        position_intent = str(score_params.get("position_intent", "long_premium"))
        execution_action_intent = str(
            score_params.get("execution_action_intent", position_intent)
        )
        ivr_min_threshold = float(score_params.get("ivr_min_threshold", 0.0))
        ivr_leg_max_threshold = float(score_params.get("ivr_leg_max_threshold", 0.0))
        strike_delta_min_abs = float(score_params.get("strike_delta_min_abs", 0.29))
        strike_delta_max_abs = float(score_params.get("strike_delta_max_abs", 0.35))
        if position_intent == "short_premium":
            ivr_bonus = 0
        ema_crossover_max_candles = score_params.get("ema_crossover_max_candles")
        adx_period = score_params.get("adx_period", 14)
        adx_min_threshold = score_params.get("adx_min_threshold")
        strike_min_oi = score_params.get("strike_min_oi", 10000)
        strike_min_volume = score_params.get("strike_min_volume", 500)
        strike_delta_ce = score_params.get("strike_delta_ce", 0.35)
        strike_delta_pe = score_params.get("strike_delta_pe", -0.35)
        strike_max_otm_steps = score_params.get("strike_max_otm_steps", 3)
        rsi_min = score_params.get("rsi_min", 50)
        rsi_max = score_params.get("rsi_max", 75)
        volume_min_ratio = score_params.get("volume_min_ratio", 1.5)
        min_dte_calendar_days = int(score_params.get("min_dte_calendar_days", 0))
        nifty_weekly_expiry_weekday = score_params.get("nifty_weekly_expiry_weekday")
        select_strike_by_min_gamma = bool(score_params.get("select_strike_by_min_gamma", False))
        max_strike_recommendations = int(score_params.get("max_strike_recommendations", 1))
        include_ema_crossover_in_score = bool(score_params.get("include_ema_crossover_in_score", True))
        strict_bullish_comparisons = bool(score_params.get("strict_bullish_comparisons", False))
        spot_regime_mode = str(score_params.get("spot_regime_mode", "")).strip()
        include_volume_in_leg_score = bool(score_params.get("include_volume_in_leg_score", True))
        spot_regime_satisfied_score = int(score_params.get("spot_regime_satisfied_score", 5))
        require_rsi_for_eligible = bool(score_params.get("require_rsi_for_eligible", False))
        long_premium_spot_align = bool(score_params.get("long_premium_spot_align", False))
        min_volume_early_session = score_params.get("min_volume_early_session")
        early_session_end_hour_ist = int(score_params.get("early_session_end_hour_ist", 0) or 0)
        flow_ranking = score_params.get("flow_ranking")

        generated_rows: list[dict] = []
        scanned_for_log: list[dict] | None = None
        chain_meta: dict[str, Any] = {}
        fetch_failed = False
        fetch_error: str | None = None
        try:
            if strategy_type == "trendpulse-z":
                if market_provider is None and kite is None:
                    fetch_failed = True
                    generated_rows = []
                    fetch_error = "index-candle capable market-data session unavailable"
                    _logger.warning(
                        "ensure_recommendations: skip refresh for %s %s — TrendPulse Z currently needs "
                        "a broker market-data session with index candles.",
                        strategy_id,
                        strategy_version,
                    )
                else:
                    generated_rows = await _get_live_candidates_trendpulse_z(
                        kite,
                        market_provider,
                        max_strike_distance,
                        score_params,
                    )
            elif strategy_type == "heuristic-voting":
                heuristics_cfg = score_params.get("heuristics") or {}
                raw_enh = score_params.get("heuristic_enhancements")
                # Missing/empty → DEFAULT_HEURISTIC_ENHANCEMENTS (enabled, loss-reduction filters on).
                if not isinstance(raw_enh, dict) or len(raw_enh) == 0:
                    enhancement_cfg = HeuristicEnhancementConfig.from_dict(DEFAULT_HEURISTIC_ENHANCEMENTS)
                else:
                    enhancement_cfg = HeuristicEnhancementConfig.from_dict(raw_enh)
                generated_rows = await _get_live_candidates_heuristic(
                    kite,
                    market_provider,
                    max_strike_distance,
                    score_threshold=float(score_threshold),
                    score_max=float(score_max),
                    heuristics_config=heuristics_cfg,
                    strike_min_oi=strike_min_oi,
                    strike_min_volume=strike_min_volume,
                    strike_delta_ce=strike_delta_ce,
                    strike_delta_pe=strike_delta_pe,
                    strike_max_otm_steps=strike_max_otm_steps,
                    rsi_min=rsi_min,
                    rsi_max=rsi_max,
                    heuristics_config_ce=score_params.get("heuristics_ce"),
                    heuristics_config_pe=score_params.get("heuristics_pe"),
                    score_threshold_ce=score_params.get("score_threshold_ce"),
                    score_threshold_pe=score_params.get("score_threshold_pe"),
                    enhancement_cfg=enhancement_cfg,
                )
            else:
                generated_rows, scanned_for_log, chain_meta = await _get_live_candidates(
                    kite,
                    market_provider,
                    max_strike_distance,
                    score_threshold=int(score_threshold),
                    score_max=score_max,
                    ivr_max_threshold=ivr_max_threshold,
                    ivr_bonus=ivr_bonus,
                    ema_crossover_max_candles=ema_crossover_max_candles,
                    adx_period=adx_period,
                    adx_min_threshold=adx_min_threshold,
                    strike_min_oi=strike_min_oi,
                    strike_min_volume=strike_min_volume,
                    strike_delta_ce=strike_delta_ce,
                    strike_delta_pe=strike_delta_pe,
                    strike_max_otm_steps=strike_max_otm_steps,
                    rsi_min=rsi_min,
                    rsi_max=rsi_max,
                    volume_min_ratio=volume_min_ratio,
                    position_intent=position_intent,
                    execution_action_intent=execution_action_intent,
                    ivr_min_threshold=ivr_min_threshold,
                    ivr_leg_max_threshold=ivr_leg_max_threshold,
                    strike_delta_min_abs=strike_delta_min_abs,
                    strike_delta_max_abs=strike_delta_max_abs,
                    min_dte_calendar_days=min_dte_calendar_days,
                    nifty_weekly_expiry_weekday=nifty_weekly_expiry_weekday,
                    select_strike_by_min_gamma=select_strike_by_min_gamma,
                    max_strike_recommendations=max_strike_recommendations,
                    include_ema_crossover_in_score=include_ema_crossover_in_score,
                    strict_bullish_comparisons=strict_bullish_comparisons,
                    spot_regime_mode=spot_regime_mode,
                    include_volume_in_leg_score=include_volume_in_leg_score,
                    spot_regime_satisfied_score=spot_regime_satisfied_score,
                    short_premium_asymmetric_datm=bool(
                        score_params.get("short_premium_asymmetric_datm", False)
                    ),
                    short_premium_ce_datm_min=int(
                        score_params.get("short_premium_ce_datm_min", 2)
                    ),
                    short_premium_ce_datm_max=int(
                        score_params.get("short_premium_ce_datm_max", 4)
                    ),
                    short_premium_pe_datm_min=int(
                        score_params.get("short_premium_pe_datm_min", -4)
                    ),
                    short_premium_pe_datm_max=int(
                        score_params.get("short_premium_pe_datm_max", 2)
                    ),
                    short_premium_delta_vix_bands=score_params.get("short_premium_delta_vix_bands"),
                    short_premium_delta_only_strikes=score_params.get("short_premium_delta_only_strikes"),
                    short_premium_leg_score_mode=str(
                        score_params.get("short_premium_leg_score_mode") or ""
                    ),
                    short_premium_rsi_below=float(score_params.get("short_premium_rsi_below", 50)),
                    short_premium_rsi_direct_band=bool(
                        score_params.get("short_premium_rsi_direct_band", False)
                    ),
                    short_premium_rsi_decreasing=bool(
                        score_params.get("short_premium_rsi_decreasing", False)
                    ),
                    short_premium_ivr_skew_min=float(score_params.get("short_premium_ivr_skew_min", 5)),
                    short_premium_pcr_bonus_vs_chain=bool(
                        score_params.get("short_premium_pcr_bonus_vs_chain", True)
                    ),
                    short_premium_pcr_chain_epsilon=float(
                        score_params.get("short_premium_pcr_chain_epsilon", 0)
                    ),
                    short_premium_pcr_min_for_sell_ce=score_params.get(
                        "short_premium_pcr_min_for_sell_ce"
                    ),
                    short_premium_pcr_max_for_sell_pe=score_params.get(
                        "short_premium_pcr_max_for_sell_pe"
                    ),
                    short_premium_expansion_block_rsi=float(
                        score_params.get("short_premium_expansion_block_rsi") or 0
                    ),
                    short_premium_vwap_weakness_min_pct=float(
                        score_params.get("short_premium_vwap_weakness_min_pct") or 0
                    ),
                    short_premium_min_momentum_points=int(
                        score_params.get("short_premium_min_momentum_points") or 0
                    ),
                    short_premium_ghost_rsi_drop_pts=float(
                        score_params.get("short_premium_ghost_rsi_drop_pts") or 0
                    ),
                    short_premium_rsi_zone_or_reversal=bool(
                        score_params.get("short_premium_rsi_zone_or_reversal", False)
                    ),
                    short_premium_rsi_soft_zone_low=float(
                        score_params.get("short_premium_rsi_soft_zone_low", 20) or 20
                    ),
                    short_premium_rsi_soft_zone_high=float(
                        score_params.get("short_premium_rsi_soft_zone_high", 45) or 45
                    ),
                    short_premium_rsi_reversal_from_rsi=float(
                        score_params.get("short_premium_rsi_reversal_from_rsi", 70) or 70
                    ),
                    short_premium_rsi_reversal_falling_bars=max(
                        0,
                        min(20, int(score_params.get("short_premium_rsi_reversal_falling_bars", 0) or 0)),
                    ),
                    short_premium_vwap_eligible_buffer_pct=float(
                        score_params.get("short_premium_vwap_eligible_buffer_pct", 0) or 0
                    ),
                    short_premium_three_factor_require_ltp_below_vwap=bool(
                        score_params.get("short_premium_three_factor_require_ltp_below_vwap", True)
                    ),
                    short_premium_ivr_min_ce=score_params.get("short_premium_ivr_min_ce"),
                    short_premium_ivr_min_pe=score_params.get("short_premium_ivr_min_pe"),
                    require_rsi_for_eligible=require_rsi_for_eligible,
                    long_premium_spot_align=long_premium_spot_align,
                    min_volume_early_session=min_volume_early_session
                    if type(min_volume_early_session) is int
                    else None,
                    early_session_end_hour_ist=early_session_end_hour_ist,
                    flow_ranking=flow_ranking
                    if isinstance(flow_ranking, dict)
                    else None,
                )
        except Exception as exc:
            fetch_failed = True
            generated_rows = []
            scanned_for_log = None
            chain_meta = {}
            fetch_error = str(exc)
            err_l = fetch_error.lower()
            expected_market_data_gap = (
                "live zerodha session required" in err_l
                or "live zerodha connection is required" in err_l
                or "broker market-data session" in err_l
            )
            log_fn = _logger.info if expected_market_data_gap else _logger.warning
            log_fn(
                "ensure_recommendations failed strategy=%s version=%s: %s",
                strategy_id,
                strategy_version,
                exc,
            )

        _emit_evaluation_snapshot(
            trigger_user_id=user_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_type=str(strategy_type),
            subscribed_user_ids=list(subscribed_users),
            score_params=score_params,
            fetch_failed=fetch_failed,
            error=fetch_error,
            generated_rows=generated_rows,
            scanned_candidates=scanned_for_log,
            chain_snapshot=chain_meta,
        )

        if fetch_failed:
            for uid in subscribed_users:
                _REC_CACHE_TS[uid] = time.time()
            continue

        if not generated_rows:
            _logger.debug(
                "ensure_recommendations: zero candidates strategy=%s version=%s — clearing stale GENERATED rows",
                strategy_id,
                strategy_version,
            )
            for uid in subscribed_users:
                await execute(
                    """
                    DELETE FROM s004_trade_recommendations
                    WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3 AND status = 'GENERATED'
                    """,
                    uid,
                    strategy_id,
                    strategy_version,
                )
                invalidate_recommendation_cache(uid)
                _REC_CACHE_TS[uid] = time.time()
            continue

        for uid in subscribed_users:
            new_ids = list(
                dict.fromkeys(
                    _stable_recommendation_id(
                        int(uid),
                        str(strategy_id),
                        str(strategy_version),
                        str(rec["symbol"]),
                        str(rec["side"]),
                    )
                    for rec in generated_rows
                )
            )
            await execute(
                """
                DELETE FROM s004_trade_recommendations
                WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3 AND status = 'GENERATED'
                  AND NOT (recommendation_id = ANY($4::text[]))
                """,
                uid,
                strategy_id,
                strategy_version,
                new_ids,
            )
            invalidate_recommendation_cache(int(uid))

        for rank_idx, rec in enumerate(generated_rows, start=1):
            rec_details = {
                "vwap": rec["vwap"],
                "ema9": rec["ema9"],
                "ema21": rec["ema21"],
                "rsi": rec["rsi"],
                "rsi_prev": rec.get("rsi_prev"),
                "short_premium_rsi_drop": rec.get("short_premium_rsi_drop"),
                "ivr": rec.get("ivr"),
                "volume": rec["volume"],
                "avg_volume": rec["avg_volume"],
                "volume_spike_ratio": rec["volume_spike_ratio"],
                "score": rec["score"],
                "primary_ok": rec["primary_ok"],
                "ema_ok": rec["ema_ok"],
                "ema_crossover_ok": rec.get("ema_crossover_ok", False),
                "rsi_ok": rec["rsi_ok"],
                "volume_ok": rec["volume_ok"],
                "signal_eligible": rec["signal_eligible"],
                "failed_conditions": rec["failed_conditions"],
                "heuristic_reasons": rec.get("heuristic_reasons"),
                "spot_price": rec["spot_price"],
                "timeframe": rec["timeframe"],
                "refresh_interval_sec": rec["refresh_interval_sec"],
                "atm_distance": rec["distance_to_atm"],
                "trendpulse": rec.get("trendpulse"),
                "delta": rec.get("delta"),
                "gamma": rec.get("gamma"),
                "oi": rec.get("oi"),
                "option_type": rec.get("option_type"),
                "oi_chg_pct": rec.get("oi_chg_pct"),
                "buildup": rec.get("buildup"),
                "flow_rank_score": rec.get("flow_rank_score"),
                "flow_pin_penalized": rec.get("flow_pin_penalized"),
            }
            for uid in subscribed_users:
                rec_id = _stable_recommendation_id(
                    int(uid),
                    str(strategy_id),
                    str(strategy_version),
                    str(rec["symbol"]),
                    str(rec["side"]),
                )
                await execute(
                    """
                    INSERT INTO s004_trade_recommendations (
                        recommendation_id, strategy_id, strategy_version, user_id, instrument, expiry, symbol, side,
                        entry_price, target_price, stop_loss_price, confidence_score, rank_value, score, reason_code, status, created_at,
                        details_json
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'TREND_SNAP','GENERATED',NOW(),$15::jsonb)
                    ON CONFLICT (recommendation_id) DO UPDATE SET
                        strategy_id = EXCLUDED.strategy_id,
                        strategy_version = EXCLUDED.strategy_version,
                        user_id = EXCLUDED.user_id,
                        instrument = EXCLUDED.instrument,
                        expiry = EXCLUDED.expiry,
                        symbol = EXCLUDED.symbol,
                        side = EXCLUDED.side,
                        entry_price = EXCLUDED.entry_price,
                        target_price = EXCLUDED.target_price,
                        stop_loss_price = EXCLUDED.stop_loss_price,
                        confidence_score = EXCLUDED.confidence_score,
                        rank_value = EXCLUDED.rank_value,
                        score = EXCLUDED.score,
                        details_json = EXCLUDED.details_json,
                        updated_at = NOW()
                    WHERE s004_trade_recommendations.status = 'GENERATED'
                    """,
                    rec_id,
                    strategy_id,
                    strategy_version,
                    uid,
                    rec["instrument"],
                    rec["expiry"],
                    rec["symbol"],
                    rec["side"],
                    rec["entry_price"],
                    rec["target_price"],
                    rec["stop_loss_price"],
                    rec["confidence_score"],
                    rank_idx,
                    rec.get("score"),
                    rec_details,
                )
                if uid not in _REC_DETAILS_CACHE:
                    _REC_DETAILS_CACHE[uid] = {}
                _REC_DETAILS_CACHE[uid][rec_id] = rec_details

        for uid in subscribed_users:
            _REC_CACHE_TS[uid] = time.time()


async def _get_user_strategy_params(user_id: int) -> dict:
    row = await fetchrow(
        """
        SELECT lot_size, sl_points, target_points, breakeven_trigger_pct, trailing_sl_points
        FROM s004_user_strategy_settings
        WHERE user_id = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if not row:
        return {
            "lot_size": 65,
            "sl_points": 15.0,
            "target_points": 10.0,
            "breakeven_trigger_pct": 50.0,
            "trailing_sl_points": 20.0,
        }
    return {
        "lot_size": max(1, int(row.get("lot_size") or 65)),
        "sl_points": float(row.get("sl_points") or 15),
        "target_points": float(row.get("target_points") or 10),
        "breakeven_trigger_pct": float(row.get("breakeven_trigger_pct") or 50),
        "trailing_sl_points": float(row.get("trailing_sl_points") or 20),
    }


async def _check_mode_approval(user_id: int, mode: str) -> None:
    """Raise ValueError if user is not approved for the given trade mode."""
    mode = (mode or "PAPER").upper()
    if mode not in ("PAPER", "LIVE"):
        return
    row = await fetchrow(
        "SELECT approved_paper, approved_live, role FROM s004_users WHERE id = $1",
        user_id,
    )
    if not row:
        raise ValueError("User not found.")
    if str(row.get("role", "")).upper() == "ADMIN":
        return
    if mode == "PAPER" and not row.get("approved_paper"):
        raise ValueError("Not approved for Paper trading. Contact admin.")
    if mode == "LIVE" and not row.get("approved_live"):
        raise ValueError("Not approved for Live trading. Contact admin.")


async def execute_recommendation(
    user_id: int,
    recommendation_id: str,
    mode: str,
    quantity: int = 1,
    manual: bool = False,
    market_snapshot: dict[str, Any] | None = None,
) -> dict:
    """Execute a recommendation and create trade. Returns {trade_ref, order_ref}."""
    await _check_mode_approval(user_id, mode)
    risk_ok, _, risk_msg = await evaluate_trade_entry_allowed(user_id)
    if not risk_ok:
        raise ValueError(risk_msg)
    rec = await fetchrow(
        """
        SELECT * FROM s004_trade_recommendations
        WHERE recommendation_id = $1 AND user_id = $2
        """,
        recommendation_id,
        user_id,
    )
    if not rec:
        raise ValueError(
            "Recommendation not found. Refresh the Trades or Dashboard list — ids rotate after each chain refresh."
        )
    if rec["status"] != "GENERATED":
        raise ValueError("Recommendation already processed.")

    mode = mode.upper()
    if mode not in ("PAPER", "LIVE"):
        raise ValueError("Invalid mode.")

    existing = await fetchrow(
        """
        SELECT mode FROM s004_live_trades
        WHERE user_id = $1 AND symbol = $2 AND current_state <> 'EXIT'
        LIMIT 1
        """,
        user_id,
        rec["symbol"],
    )
    if existing:
        other = str(existing.get("mode") or "").upper()
        raise ValueError(
            f"You already have an open {other} position for {rec['symbol']}. "
            f"Close it before opening another (paper and live share the same symbol cap)."
        )

    params = await _get_user_strategy_params(user_id)
    broker_order_id: str | None = None
    if mode == "LIVE":
        from app.services.execution_service import place_entry_order

        contracts = quantity * params["lot_size"]
        result = await place_entry_order(
            user_id=user_id,
            symbol=rec["symbol"],
            side=str(rec.get("side") or "BUY"),
            quantity=contracts,
            expected_price=float(rec["entry_price"]),
        )
        if not result.success:
            if result.error_code == "TOKEN_EXPIRED":
                raise ValueError("Kite session expired. Reconnect Zerodha in Settings.")
            if result.error_code == "NO_CREDENTIALS":
                raise ValueError("Valid broker connection required for Live trading. Connect Zerodha in Settings.")
            raise ValueError(result.error_message or "Order placement failed.")
        broker_order_id = result.order_id
    entry = float(rec["entry_price"])
    sl_pts = params["sl_points"]
    tgt_pts = params["target_points"]
    side = str(rec.get("side") or "BUY").upper()
    if side == "BUY":
        target_price = round(entry + tgt_pts, 2)
        stop_loss_price = round(entry - sl_pts, 2)
    else:
        target_price = round(entry - tgt_pts, 2)
        stop_loss_price = round(entry + sl_pts, 2)

    order_ref = f"ord-{uuid4().hex[:10]}"
    trade_ref = f"trd-{uuid4().hex[:10]}"
    entry_snap = entry_snapshot_from_rec_and_market(dict(rec), market_snapshot)

    await execute(
        """
        INSERT INTO s004_execution_orders (
            order_ref, recommendation_id, user_id, requested_mode, side, quantity, requested_price,
            manual_execute, order_status, broker_order_id, created_at, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'FILLED',$9,NOW(),NOW())
        """,
        order_ref,
        recommendation_id,
        user_id,
        mode,
        rec["side"],
        quantity * params["lot_size"],
        rec["entry_price"],
        manual,
        broker_order_id,
    )

    live_trade_params = (
        trade_ref,
        order_ref,
        recommendation_id,
        user_id,
        rec["strategy_id"],
        rec["strategy_version"],
        rec["symbol"],
        mode,
        rec["side"],
        quantity,
        rec["entry_price"],
        target_price,
        stop_loss_price,
        broker_order_id,
    )
    try:
        await execute(
            """
            INSERT INTO s004_live_trades (
                trade_ref, order_ref, recommendation_id, user_id, strategy_id, strategy_version, symbol, mode, side, quantity,
                entry_price, current_price, target_price, stop_loss_price, current_state,
                realized_pnl, unrealized_pnl, broker_order_id, entry_market_snapshot, opened_at, created_at, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11,$12,$13,'ACTIVE',0,0,$14,$15::jsonb,NOW(),NOW(),NOW())
            """,
            *live_trade_params,
            json.dumps(entry_snap),
        )
    except Exception:
        await execute(
            """
            INSERT INTO s004_live_trades (
                trade_ref, order_ref, recommendation_id, user_id, strategy_id, strategy_version, symbol, mode, side, quantity,
                entry_price, current_price, target_price, stop_loss_price, current_state,
                realized_pnl, unrealized_pnl, broker_order_id, opened_at, created_at, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11,$12,$13,'ACTIVE',0,0,$14,NOW(),NOW(),NOW())
            """,
            *live_trade_params,
        )

    await execute(
        """
        INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
        VALUES ($1,$2,'ENTRY','ACTIVE',$3,$4::jsonb,NOW())
        """,
        trade_ref,
        "MANUAL_EXECUTE" if manual else "AUTO_EXECUTE",
        "USER_ACCEPTED" if manual else "AUTO_ACCEPTED",
        json.dumps({"recommendation_id": recommendation_id, "order_ref": order_ref}),
    )

    await execute(
        """
        UPDATE s004_trade_recommendations
        SET status = 'ACCEPTED', updated_at = NOW()
        WHERE recommendation_id = $1
        """,
        recommendation_id,
    )

    from app.services.trade_chain_snapshot_service import schedule_entry_chain_snapshot

    schedule_entry_chain_snapshot(
        trade_ref=trade_ref,
        user_id=user_id,
        recommendation_id=recommendation_id,
        strategy_id=str(rec["strategy_id"]),
        strategy_version=str(rec["strategy_version"]),
        mode=mode,
        symbol=str(rec["symbol"]),
        instrument=str(rec["instrument"]),
        expiry=str(rec["expiry"]),
    )

    return {"trade_ref": trade_ref, "order_ref": order_ref}


async def _get_trade_window(user_id: int):
    """Get trade_start and trade_end from user's strategy settings. Uses IST for market hours. Defaults 09:15–15:00 (NSE F&O open)."""
    from datetime import time

    strategy_id, strategy_version = await _get_user_strategy(user_id)
    row = await fetchrow(
        """
        SELECT trade_start, trade_end FROM s004_user_strategy_settings
        WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
        """,
        user_id,
        strategy_id,
        strategy_version,
    )
    if not row:
        return time(9, 15), time(15, 0)
    start = row.get("trade_start")
    end = row.get("trade_end")
    if start is None or end is None:
        return time(9, 15), time(15, 0)

    def _parse_time(v: object):
        if v is None:
            return None
        if hasattr(v, "hour"):  # datetime.time
            return v
        if isinstance(v, str):
            try:
                parts = v.strip().split(":")
                return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0, int(parts[2]) if len(parts) > 2 else 0)
            except (ValueError, IndexError):
                pass
        return None

    start_t = _parse_time(start) or time(9, 15)
    end_t = _parse_time(end) or time(15, 0)
    return start_t, end_t


def _is_within_trade_window(trade_start: datetime.time, trade_end: datetime.time) -> bool:
    """True if current IST time is within [trade_start, trade_end] (inclusive)."""
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
    if trade_start <= trade_end:
        return trade_start <= now_ist <= trade_end
    # Window spans midnight (e.g. 22:00–02:00) – treat as outside for typical market hours
    return trade_start <= now_ist or now_ist <= trade_end


def _ist_day_utc_naive_bounds_today() -> tuple[datetime, datetime]:
    ist = ZoneInfo("Asia/Kolkata")
    d = datetime.now(ist).date()
    start_ist = datetime.combine(d, datetime.min.time(), tzinfo=ist)
    end_ist = start_ist + timedelta(days=1)
    return (
        start_ist.astimezone(timezone.utc).replace(tzinfo=None),
        end_ist.astimezone(timezone.utc).replace(tzinfo=None),
    )


_DECISION_LOG_LAST: dict[int, float] = {}
_DECISION_LOG_MIN_SEC = 50.0


async def _maybe_insert_auto_execute_decision_log(
    *,
    user_id: int,
    mode: str | None,
    strategy_id: str | None,
    strategy_version: str | None,
    gate_blocked: bool,
    gate_reason: str | None,
    cycle_summary: str,
    auto_trade_threshold: float | None,
    score_display_threshold: float | None,
    min_confidence_threshold: float,
    open_trades: int | None,
    trades_today: int | None,
    max_parallel: int | None,
    max_trades_day: int | None,
    within_trade_window: bool | None,
    has_kite_live: bool | None,
    daily_pnl_ok: bool | None,
    market_context: dict[str, Any],
    evaluations: list[dict[str, Any]],
    executed_ids: list[str],
) -> None:
    """Throttle per user to limit row volume; failures are silent if table missing."""
    now = time.monotonic()
    if now - _DECISION_LOG_LAST.get(user_id, 0.0) < _DECISION_LOG_MIN_SEC:
        return
    _DECISION_LOG_LAST[user_id] = now
    try:
        await execute(
            """
            INSERT INTO s004_auto_execute_decision_log (
                user_id, mode, strategy_id, strategy_version,
                gate_blocked, gate_reason, cycle_summary,
                auto_trade_threshold, score_display_threshold, min_confidence_threshold,
                open_trades, trades_today, max_parallel, max_trades_day,
                within_trade_window, has_kite_live, daily_pnl_ok,
                market_context, evaluations, executed_recommendation_ids
            )
            VALUES (
                $1, $2, $3, $4,
                $5, $6, $7,
                $8, $9, $10,
                $11, $12, $13, $14,
                $15, $16, $17,
                $18::jsonb, $19::jsonb, $20::jsonb
            )
            """,
            user_id,
            (mode or "")[:10] or None,
            strategy_id,
            strategy_version,
            gate_blocked,
            (gate_reason or None)[:96] if gate_reason else None,
            (cycle_summary or "")[:48],
            auto_trade_threshold,
            score_display_threshold,
            min_confidence_threshold,
            open_trades,
            trades_today,
            max_parallel,
            max_trades_day,
            within_trade_window,
            has_kite_live,
            daily_pnl_ok,
            json.dumps(market_context or {}),
            json.dumps(evaluations or []),
            json.dumps(executed_ids or []),
        )
    except Exception:
        pass


async def _audit_generated_evaluations(
    user_id: int,
    mode: str,
    min_confidence_line: float = 80.0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Factual per-recommendation eligibility vs auto-execute rules (for decision log)."""
    strategy_id, strategy_version = await _get_user_strategy(user_id)
    score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
    auto_thresh = float(score_params["auto_trade_score_threshold"])
    score_threshold = float(score_params.get("score_threshold", 3))
    rows = await fetch(
        """
        SELECT t.recommendation_id, t.symbol, t.confidence_score, t.score, t.rank_value, t.reason_code, t.created_at,
               t.details_json
        FROM (
            SELECT recommendation_id, symbol, side, confidence_score, score, rank_value, reason_code, created_at, details_json,
                   ROW_NUMBER() OVER (PARTITION BY symbol, side ORDER BY created_at DESC) AS rn
            FROM s004_trade_recommendations
            WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3 AND status = 'GENERATED'
        ) t
        WHERE t.rn = 1
        ORDER BY t.rank_value ASC NULLS LAST, t.created_at DESC
        LIMIT 25
        """,
        user_id,
        strategy_id,
        strategy_version,
    )
    evaluations: list[dict[str, Any]] = []
    for r in rows or []:
        item = _enrich_recommendation_item_from_storage(dict(r), user_id)
        rec_id = str(item.get("recommendation_id") or "")
        conf = float(item.get("confidence_score") or 0)
        score_raw = item.get("score")
        score_val: float | None
        try:
            score_val = float(score_raw) if score_raw is not None else None
        except (TypeError, ValueError):
            score_val = None
        signal_eligible = item.get("signal_eligible")
        if signal_eligible is None and score_val is not None:
            signal_eligible = score_val >= score_threshold
        reasons: list[str] = []
        eligible = row_meets_auto_execute_score_bar(
            item,
            min_score=auto_thresh,
            score_threshold=score_threshold,
            min_confidence=min_confidence_line,
        )
        if not eligible:
            if conf < min_confidence_line:
                reasons.append(f"confidence_{round(conf, 2)}_below_{min_confidence_line}")
            if score_val is None:
                reasons.append("score_missing")
            elif score_val < auto_thresh:
                reasons.append("below_auto_trade_score_threshold")
            if signal_eligible is not True:
                reasons.append("signal_not_eligible_vs_display_threshold")
        evaluations.append(
            {
                "recommendation_id": rec_id,
                "symbol": str(r.get("symbol") or ""),
                "confidence": round(conf, 4),
                "score": score_val,
                "rank_value": int(r.get("rank_value") or 0),
                "reason_code": str(r.get("reason_code") or ""),
                "auto_trade_score_threshold": auto_thresh,
                "score_display_threshold": score_threshold,
                "min_confidence_for_auto": min_confidence_line,
                "signal_eligible": bool(signal_eligible) if signal_eligible is not None else None,
                "eligible_for_auto_execute": eligible,
                "block_reasons": reasons if not eligible else [],
            }
        )
    meta = {
        "strategy_id": strategy_id,
        "strategy_version": strategy_version,
        "auto_trade_score_threshold": auto_thresh,
        "score_display_threshold": score_threshold,
        "min_confidence_for_auto": min_confidence_line,
    }
    return meta, evaluations


async def run_auto_execute_cycle() -> None:
    """Run auto-execute for all users with engine_running=true. Respects Trade Start/End from Settings. Picks trades with score >= threshold, Eligible=Yes, confidence>=80."""
    async def _chain_eod_maint() -> None:
        try:
            from app.services.strategy_eod_report_service import maybe_run_strategy_eod_reports
            from app.services.trade_chain_snapshot_service import maybe_purge_chain_snapshots

            await maybe_purge_chain_snapshots()
            await maybe_run_strategy_eod_reports()
        except Exception:
            _logger.warning("chain snapshot / EOD maintenance failed", exc_info=True)

    try:
        asyncio.create_task(_chain_eod_maint())
    except RuntimeError:
        try:
            asyncio.get_event_loop().create_task(_chain_eod_maint())
        except Exception:
            _logger.debug("could not schedule chain/EOD maintenance", exc_info=True)

    if (await get_platform_trading_paused())[0]:
        try:
            from app.services.trade_chain_snapshot_service import schedule_chain_snapshot_sample_cycle

            schedule_chain_snapshot_sample_cycle()
        except Exception:
            _logger.debug("chain snapshot sample cycle skipped (paused)", exc_info=True)
        return
    kite_shared = await _get_kite_for_any_user()
    market_ctx: dict[str, Any] = {}
    if kite_shared:
        try:
            market_ctx = await build_market_context_for_log(kite_shared)
        except Exception:
            market_ctx = {}
    day_start, day_end = _ist_day_utc_naive_bounds_today()
    rows = await fetch(
        """
        SELECT m.user_id, m.mode, m.max_parallel_trades, m.max_trades_day
        FROM s004_user_master_settings m
        WHERE m.engine_running = TRUE
        """,
    )
    for r in rows:
        user_id = int(r["user_id"])
        mode = str(r.get("mode") or "PAPER").upper()
        if mode not in ("PAPER", "LIVE"):
            continue
        try:
            strategy_id, strategy_version = await _get_user_strategy(user_id)
            score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
            auto_t = float(score_params["auto_trade_score_threshold"])
            disp_t = float(score_params.get("score_threshold", 3))
        except Exception:
            strategy_id, strategy_version = "", ""
            auto_t, disp_t = None, None

        trade_start, trade_end = await _get_trade_window(user_id)
        within = _is_within_trade_window(trade_start, trade_end)
        if not within:
            await _maybe_insert_auto_execute_decision_log(
                user_id=user_id,
                mode=mode,
                strategy_id=strategy_id or None,
                strategy_version=strategy_version or None,
                gate_blocked=True,
                gate_reason="outside_trade_window",
                cycle_summary="SKIPPED_GATE",
                auto_trade_threshold=auto_t,
                score_display_threshold=disp_t,
                min_confidence_threshold=80.0,
                open_trades=None,
                trades_today=None,
                max_parallel=int(r.get("max_parallel_trades") or 3),
                max_trades_day=int(r.get("max_trades_day") or 4),
                within_trade_window=False,
                has_kite_live=None,
                daily_pnl_ok=None,
                market_context=market_ctx,
                evaluations=[],
                executed_ids=[],
            )
            continue
        if mode == "LIVE":
            from app.services.broker_runtime import resolve_broker_context

            live_ctx = await resolve_broker_context(user_id, mode="LIVE")
            if not live_ctx.execution:
                await _maybe_insert_auto_execute_decision_log(
                    user_id=user_id,
                    mode=mode,
                    strategy_id=strategy_id or None,
                    strategy_version=strategy_version or None,
                    gate_blocked=True,
                    gate_reason="live_no_broker_session",
                    cycle_summary="SKIPPED_GATE",
                    auto_trade_threshold=auto_t,
                    score_display_threshold=disp_t,
                    min_confidence_threshold=80.0,
                    open_trades=None,
                    trades_today=None,
                    max_parallel=int(r.get("max_parallel_trades") or 3),
                    max_trades_day=int(r.get("max_trades_day") or 4),
                    within_trade_window=True,
                    has_kite_live=False,
                    daily_pnl_ok=None,
                    market_context=market_ctx,
                    evaluations=[],
                    executed_ids=[],
                )
                continue
        daily_ok, _, _ = await evaluate_user_daily_pnl_limits(user_id)
        if not daily_ok:
            await _maybe_insert_auto_execute_decision_log(
                user_id=user_id,
                mode=mode,
                strategy_id=strategy_id or None,
                strategy_version=strategy_version or None,
                gate_blocked=True,
                gate_reason="daily_pnl_limit_blocked",
                cycle_summary="SKIPPED_GATE",
                auto_trade_threshold=auto_t,
                score_display_threshold=disp_t,
                min_confidence_threshold=80.0,
                open_trades=None,
                trades_today=None,
                max_parallel=int(r.get("max_parallel_trades") or 3),
                max_trades_day=int(r.get("max_trades_day") or 4),
                within_trade_window=True,
                has_kite_live=True,
                daily_pnl_ok=False,
                market_context=market_ctx,
                evaluations=[],
                executed_ids=[],
            )
            continue
        try:
            open_count = await fetchrow(
                """
                SELECT COUNT(*) AS n FROM s004_live_trades
                WHERE user_id = $1 AND current_state <> 'EXIT'
                """,
                user_id,
            )
            open_trades = int(open_count["n"] or 0) if open_count else 0
            max_parallel = int(r.get("max_parallel_trades") or 3)
            if open_trades >= max_parallel:
                await _maybe_insert_auto_execute_decision_log(
                    user_id=user_id,
                    mode=mode,
                    strategy_id=strategy_id or None,
                    strategy_version=strategy_version or None,
                    gate_blocked=True,
                    gate_reason="max_parallel_trades_reached",
                    cycle_summary="SKIPPED_GATE",
                    auto_trade_threshold=auto_t,
                    score_display_threshold=disp_t,
                    min_confidence_threshold=80.0,
                    open_trades=open_trades,
                    trades_today=None,
                    max_parallel=max_parallel,
                    max_trades_day=int(r.get("max_trades_day") or 4),
                    within_trade_window=True,
                    has_kite_live=True,
                    daily_pnl_ok=True,
                    market_context=market_ctx,
                    evaluations=[],
                    executed_ids=[],
                )
                continue

            trades_today_row = await fetchrow(
                """
                SELECT COUNT(*) AS n FROM s004_live_trades
                WHERE user_id = $1 AND opened_at >= $2 AND opened_at < $3
                """,
                user_id,
                day_start,
                day_end,
            )
            trades_today = int(trades_today_row["n"] or 0) if trades_today_row else 0
            max_per_day = int(r.get("max_trades_day") or 4)
            if trades_today >= max_per_day:
                await _maybe_insert_auto_execute_decision_log(
                    user_id=user_id,
                    mode=mode,
                    strategy_id=strategy_id or None,
                    strategy_version=strategy_version or None,
                    gate_blocked=True,
                    gate_reason="max_trades_per_day_reached",
                    cycle_summary="SKIPPED_GATE",
                    auto_trade_threshold=auto_t,
                    score_display_threshold=disp_t,
                    min_confidence_threshold=80.0,
                    open_trades=open_trades,
                    trades_today=trades_today,
                    max_parallel=max_parallel,
                    max_trades_day=max_per_day,
                    within_trade_window=True,
                    has_kite_live=True,
                    daily_pnl_ok=True,
                    market_context=market_ctx,
                    evaluations=[],
                    executed_ids=[],
                )
                continue

            kite = kite_shared
            await ensure_recommendations(user_id, kite)
            meta, evaluations = await _audit_generated_evaluations(user_id, mode, min_confidence_line=80.0)
            auto_t = float(meta.get("auto_trade_score_threshold") or 0)
            disp_t = float(meta.get("score_display_threshold") or 0)
            eligible = await get_auto_execute_eligible_recommendations(user_id, mode, min_confidence=80.0)
            executed_ids: list[str] = []
            for rec in eligible[: max_parallel - open_trades]:
                try:
                    await execute_recommendation(
                        user_id,
                        rec["recommendation_id"],
                        mode,
                        quantity=1,
                        manual=False,
                        market_snapshot=market_ctx,
                    )
                    executed_ids.append(str(rec["recommendation_id"]))
                except ValueError as ex:
                    if "already have an open" in str(ex):
                        _logger.debug(
                            "auto_execute: skip recommendation_id=%s symbol=%s (open position exists)",
                            rec.get("recommendation_id"),
                            rec.get("symbol"),
                        )
                    else:
                        _logger.warning(
                            "auto_execute: execute_recommendation failed user_id=%s mode=%s recommendation_id=%s symbol=%s",
                            user_id,
                            mode,
                            rec.get("recommendation_id"),
                            rec.get("symbol"),
                            exc_info=True,
                        )
                except Exception:
                    _logger.warning(
                        "auto_execute: execute_recommendation failed user_id=%s mode=%s recommendation_id=%s symbol=%s",
                        user_id,
                        mode,
                        rec.get("recommendation_id"),
                        rec.get("symbol"),
                        exc_info=True,
                    )
            summary = "EXECUTED" if executed_ids else ("NO_ELIGIBLE" if not any(e.get("eligible_for_auto_execute") for e in evaluations) else "ELIGIBLE_NONE_EXECUTED")
            await _maybe_insert_auto_execute_decision_log(
                user_id=user_id,
                mode=mode,
                strategy_id=str(meta.get("strategy_id") or strategy_id) or None,
                strategy_version=str(meta.get("strategy_version") or strategy_version) or None,
                gate_blocked=False,
                gate_reason=None,
                cycle_summary=summary[:48],
                auto_trade_threshold=auto_t,
                score_display_threshold=disp_t,
                min_confidence_threshold=80.0,
                open_trades=open_trades,
                trades_today=trades_today,
                max_parallel=max_parallel,
                max_trades_day=max_per_day,
                within_trade_window=True,
                has_kite_live=True,
                daily_pnl_ok=True,
                market_context=market_ctx,
                evaluations=evaluations,
                executed_ids=executed_ids,
            )
        except Exception:
            _logger.exception(
                "auto_execute_cycle: unhandled error user_id=%s mode=%s",
                user_id,
                mode,
            )

    try:
        from app.services.trade_chain_snapshot_service import schedule_chain_snapshot_sample_cycle

        schedule_chain_snapshot_sample_cycle()
    except Exception:
        _logger.debug("chain snapshot sample cycle schedule skipped", exc_info=True)


def row_meets_auto_execute_score_bar(
    r: dict[str, Any],
    *,
    min_score: float,
    score_threshold: float,
    min_confidence: float,
) -> bool:
    """Score / signal / confidence gates shared by auto-execute and GET /recommendations?eligible_only (SIGNALS)."""
    sc = r.get("score")
    if sc is None:
        return False
    try:
        sv = float(sc)
    except (TypeError, ValueError):
        return False
    try:
        conf = float(r.get("confidence_score") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < float(min_confidence):
        return False
    sig = r.get("signal_eligible")
    if sig is None:
        sig = sv >= float(score_threshold)
    if sig is not True:
        return False
    return sv >= float(min_score)


async def filter_rows_auto_execute_aligned(
    user_id: int,
    rows: list[dict[str, Any]],
    *,
    all_strategies: bool,
    min_confidence: float = 80.0,
    strategy_score_params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep rows that pass the same score/signal/confidence bar as auto-execute (per-row strategy params when all_strategies).

    When ``strategy_score_params`` is set and ``all_strategies`` is False, reuse it and skip an extra DB round-trip.
    """
    if not rows:
        return []
    params_cache: dict[tuple[str, str], tuple[float, float]] = {}

    async def _thresh(sid: str, ver: str) -> tuple[float, float]:
        k = (sid, ver)
        if k not in params_cache:
            try:
                p = await get_strategy_score_params(sid, ver, user_id)
                params_cache[k] = (
                    float(p["auto_trade_score_threshold"]),
                    float(p.get("score_threshold", 3)),
                )
            except Exception:
                params_cache[k] = (4.0, 3.0)
        return params_cache[k]

    out: list[dict[str, Any]] = []
    if not all_strategies:
        if strategy_score_params is not None:
            auto_t = float(strategy_score_params["auto_trade_score_threshold"])
            disp_t = float(strategy_score_params.get("score_threshold", 3))
        else:
            sid_u, ver_u = await _get_user_strategy(user_id)
            auto_t, disp_t = await _thresh(sid_u, ver_u)
        for r in rows:
            if row_meets_auto_execute_score_bar(
                r,
                min_score=auto_t,
                score_threshold=disp_t,
                min_confidence=min_confidence,
            ):
                out.append(r)
        return out

    for r in rows:
        sid = str(r.get("strategy_id") or "").strip()
        ver = str(r.get("strategy_version") or "").strip()
        if not sid or not ver:
            continue
        auto_t, disp_t = await _thresh(sid, ver)
        if row_meets_auto_execute_score_bar(
            r,
            min_score=auto_t,
            score_threshold=disp_t,
            min_confidence=min_confidence,
        ):
            out.append(r)
    return out


async def get_auto_execute_eligible_recommendations(
    user_id: int,
    mode: str,
    min_confidence: float = 80.0,
    min_score: int | None = None,
) -> list[dict]:
    """Return recommendations that meet auto-execute criteria: score >= autoTradeScoreThreshold, Eligible=Yes (or inferred from score>=threshold), confidence>=80.
    Skips symbols that already have an open trade (same rule as execute_recommendation)."""
    strategy_id, strategy_version = await _get_user_strategy(user_id)
    score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
    auto_thresh = float(score_params["auto_trade_score_threshold"])
    score_threshold = float(score_params.get("score_threshold", 3))
    if min_score is None:
        min_score = auto_thresh
    min_score_val = float(min_score)
    open_rows = await fetch(
        """
        SELECT symbol FROM s004_live_trades
        WHERE user_id = $1 AND current_state <> 'EXIT'
        """,
        user_id,
    )
    symbols_with_open = {str(row["symbol"]) for row in open_rows if row.get("symbol")}
    rows = await list_recommendations_for_user(
        user_id=user_id,
        status="GENERATED",
        min_confidence=min_confidence,
        sort_by="rank",
        sort_dir="asc",
        limit=50,
        offset=0,
    )
    kite = await get_kite_for_quotes(user_id)
    rows = await filter_recommendations_short_delta_band_only(user_id, kite, rows)
    eligible: list[dict] = []
    for r in rows:
        sym = r.get("symbol")
        if sym is not None and str(sym) in symbols_with_open:
            continue
        if row_meets_auto_execute_score_bar(
            r,
            min_score=min_score_val,
            score_threshold=score_threshold,
            min_confidence=min_confidence,
        ):
            r["mode"] = mode
            eligible.append(r)
    return eligible


def _infer_option_type_recommendation_row(r: dict[str, Any]) -> str:
    ot = str(r.get("option_type") or "").strip().upper()
    if ot in ("CE", "PE"):
        return ot
    sym = str(r.get("symbol") or "").strip().upper()
    if sym.endswith("PE"):
        return "PE"
    if sym.endswith("CE"):
        return "CE"
    return ""


async def filter_recommendations_short_delta_band_only(
    user_id: int,
    kite: KiteConnect | None,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Keep only short_premium rows whose persisted delta sits in the active CE/PE band
    (VIX-resolved shortPremiumDeltaVixBands, else deltaMinAbs/deltaMaxAbs). Other rows unchanged.
    """
    if not rows:
        return rows
    keys_ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        sid = str(r.get("strategy_id") or "").strip()
        ver = str(r.get("strategy_version") or "").strip()
        if sid and ver:
            k = (sid, ver)
            if k not in seen:
                seen.add(k)
                keys_ordered.append(k)

    async def _load_params(k: tuple[str, str]) -> tuple[tuple[str, str], dict[str, Any]]:
        try:
            return k, await get_strategy_score_params(k[0], k[1], user_id)
        except Exception:
            _logger.exception(
                "filter_recommendations_short_delta_band_only: get_strategy_score_params failed sid=%s ver=%s",
                k[0],
                k[1],
            )
            return k, {}

    params_cache: dict[tuple[str, str], dict[str, Any]] = {}
    if keys_ordered:
        loaded = await asyncio.gather(*[_load_params(k) for k in keys_ordered])
        for k, sp in loaded:
            params_cache[k] = sp

    any_short = any(
        str(sp.get("position_intent", "")).strip().lower() == "short_premium" for sp in params_cache.values()
    )
    if not any_short:
        return rows

    _vix_unset = object()
    vix: Any = _vix_unset
    out: list[dict[str, Any]] = []
    for r in rows:
        sid = str(r.get("strategy_id") or "").strip()
        ver = str(r.get("strategy_version") or "").strip()
        if not sid or not ver:
            out.append(r)
            continue
        key = (sid, ver)
        sp = params_cache.get(key, {})
        if str(sp.get("position_intent", "")).strip().lower() != "short_premium":
            out.append(r)
            continue
        if vix is _vix_unset:
            if kite is not None:
                try:
                    vix_timeout = max(
                        0.3,
                        min(
                            3.0,
                            float(
                                os.getenv(
                                    "S004_RECOMMENDATIONS_VIX_TIMEOUT_SEC",
                                    "1.2",
                                )
                            ),
                        ),
                    )
                    vix = await asyncio.wait_for(
                        asyncio.to_thread(_vix_from_quote, kite),
                        timeout=vix_timeout,
                    )
                except Exception:
                    vix = None
            else:
                vix = None
        d_lo_abs = float(sp.get("strike_delta_min_abs", 0.29))
        d_hi_abs = float(sp.get("strike_delta_max_abs", 0.35))
        bands = sp.get("short_premium_delta_vix_bands")
        ce_lo, ce_hi, pe_lo, pe_hi, _note = _resolve_short_premium_delta_corners(
            strike_delta_min_abs=d_lo_abs,
            strike_delta_max_abs=d_hi_abs,
            short_premium_delta_vix_bands=bands if isinstance(bands, dict) and bands else None,
            vix=vix,
        )
        opt = _infer_option_type_recommendation_row(r)
        if not opt:
            continue
        delta_raw = r.get("delta")
        try:
            delta = float(delta_raw) if delta_raw is not None else float("nan")
        except (TypeError, ValueError):
            delta = float("nan")
        if delta != delta or not _short_premium_signed_delta_ok(
            delta,
            opt,
            ce_lo=ce_lo,
            ce_hi=ce_hi,
            pe_lo=pe_lo,
            pe_hi=pe_hi,
        ):
            continue
        out.append(r)
    return out


async def list_recommendations_for_user(
    user_id: int,
    status: str,
    min_confidence: float,
    sort_by: str,
    sort_dir: str,
    limit: int,
    offset: int,
    strategy_id: str | None = None,
    strategy_version: str | None = None,
    all_strategies: bool = False,
) -> list[dict]:
    """List recommendations. When all_strategies=True (Admin), return from all strategies."""
    if not all_strategies and (strategy_id is None or strategy_version is None):
        strategy_id, strategy_version = await _get_user_strategy(user_id)
    sort_map = {
        "rank": "rank_value",
        "confidence": "confidence_score",
        "created_at": "created_at",
    }
    order_col = sort_map.get(sort_by, "rank_value")
    order_dir = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    if all_strategies:
        rows = await fetch(
            f"""
            SELECT t.recommendation_id, t.symbol, t.instrument, t.expiry, t.side, t.entry_price, t.target_price, t.stop_loss_price,
                   t.confidence_score, t.rank_value, t.score, t.status, t.created_at, t.strategy_id, t.strategy_version,
                   t.details_json,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name
            FROM (
                SELECT recommendation_id, symbol, instrument, expiry, side, entry_price, target_price, stop_loss_price,
                       confidence_score, rank_value, score, status, created_at, strategy_id, strategy_version,
                       details_json,
                       ROW_NUMBER() OVER (PARTITION BY symbol, side, strategy_id, strategy_version ORDER BY created_at DESC) AS rn
                FROM s004_trade_recommendations
                WHERE user_id = $1
                  AND status = $2
                  AND confidence_score >= $3
            ) t
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.rn = 1
            ORDER BY {order_col} {order_dir}, t.created_at DESC
            LIMIT $4 OFFSET $5
            """,
            user_id,
            status,
            min_confidence,
            limit,
            offset,
        )
    else:
        rows = await fetch(
            f"""
            SELECT t.recommendation_id, t.symbol, t.instrument, t.expiry, t.side, t.entry_price, t.target_price, t.stop_loss_price,
                   t.confidence_score, t.rank_value, t.score, t.status, t.created_at, t.strategy_id, t.strategy_version,
                   t.details_json,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name
            FROM (
                SELECT recommendation_id, symbol, instrument, expiry, side, entry_price, target_price, stop_loss_price,
                       confidence_score, rank_value, score, status, created_at, strategy_id, strategy_version,
                       details_json,
                       ROW_NUMBER() OVER (PARTITION BY symbol, side ORDER BY created_at DESC) AS rn
                FROM s004_trade_recommendations
                WHERE user_id = $1
                  AND strategy_id = $2
                  AND strategy_version = $3
                  AND status = $4
                  AND confidence_score >= $5
            ) t
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.rn = 1
            ORDER BY {order_col} {order_dir}, t.created_at DESC
            LIMIT $6 OFFSET $7
            """,
            user_id,
            strategy_id,
            strategy_version,
            status,
            min_confidence,
            limit,
            offset,
        )
    enriched: list[dict] = []
    for r in rows:
        try:
            enriched.append(_enrich_recommendation_item_from_storage(dict(r), user_id))
        except Exception:
            _logger.warning(
                "list_recommendations: skip row recommendation_id=%s (enrich failed)",
                (dict(r) if r else {}).get("recommendation_id"),
                exc_info=True,
            )
    return enriched
