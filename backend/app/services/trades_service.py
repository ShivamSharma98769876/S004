from __future__ import annotations

import asyncio
import json
import logging
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
    fetch_index_candles_sync,
    fetch_option_chain_sync,
    pick_expiry_with_min_calendar_dte,
    pick_primary_expiry_str,
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

_logger = logging.getLogger(__name__)
_EVAL_IST = ZoneInfo("Asia/Kolkata")


def _slim_candidate_for_evaluation_log(r: dict[str, Any]) -> dict[str, Any]:
    """Compact row for evaluation log (full chain scan before min-gamma cap, or persist list)."""
    return {
        "symbol": r.get("symbol"),
        "instrument": r.get("instrument"),
        "expiry": r.get("expiry"),
        "side": r.get("side"),
        "option_type": r.get("option_type"),
        "distance_to_atm": r.get("distance_to_atm"),
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
    }
    uids: list[int] = sorted({int(trigger_user_id), *[int(x) for x in subscribed_user_ids]})
    append_evaluation_event(event, user_ids=uids)


async def _get_kite_for_user(user_id: int) -> KiteConnect | None:
    row = await fetchrow(
        "SELECT credentials_json FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    if not row:
        return None
    cred = row.get("credentials_json")
    if isinstance(cred, str):
        try:
            cred = json.loads(cred)
        except json.JSONDecodeError:
            return None
    if not isinstance(cred, dict):
        return None
    api_key = str(cred.get("apiKey", "")).strip()
    access_token = str(cred.get("accessToken", "")).strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


async def _get_kite_for_any_user() -> KiteConnect | None:
    """Get Kite from any user who has credentials (prefer ADMIN). Used to generate shared recommendations."""
    rows = await fetch(
        """
        SELECT m.user_id FROM s004_user_master_settings m
        JOIN s004_users u ON u.id = m.user_id
        WHERE m.credentials_json IS NOT NULL
        ORDER BY CASE WHEN u.role = 'ADMIN' THEN 0 ELSE 1 END, m.user_id
        LIMIT 5
        """
    )
    for r in rows or []:
        kite = await _get_kite_for_user(int(r["user_id"]))
        if kite:
            return kite
    return None


async def get_kite_for_quotes(user_id: int) -> KiteConnect | None:
    """Return Shared API (Admin's Kite) for quotes/LTP. Per policy: users without valid broker connection use Shared API for Paper; only Live execution requires user's own connection."""
    return await _get_kite_for_any_user()


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


def invalidate_recommendation_cache(user_id: int) -> None:
    """Clear recommendation cache for user so next run uses fresh strategy params from saved settings."""
    _REC_CACHE_TS.pop(user_id, None)
    _REC_DETAILS_CACHE.pop(user_id, None)


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
) -> str:
    """Build human-readable list of failed conditions. Uses actual strategy thresholds. Crossover and volume spike are relaxed (not shown as failures)."""
    failed: list[str] = []
    if not primary_ok:
        failed.append("Primary(close<=VWAP)")
    if not ema_ok:
        failed.append("EMA9<=EMA21")
    if not rsi_ok:
        failed.append(f"RSI not in {rsi_min:.0f}-{rsi_max:.0f}")
    return "PASS" if not failed else "; ".join(failed)


def _failed_conditions_short_leg(
    primary_ok: bool,
    ema_ok: bool,
    rsi_ok: bool,
    *,
    rsi_min: float = 50,
    rsi_max: float = 75,
) -> str:
    """Bearish-style checks on option LTP series (short premium): LTP below VWAP, EMA9 below EMA21, mirrored RSI band."""
    failed: list[str] = []
    if not primary_ok:
        failed.append("LTP not below VWAP (want premium weakness)")
    if not ema_ok:
        failed.append("EMA9 not below EMA21 (want premium weakness)")
    if not rsi_ok:
        failed.append(f"RSI not in bearish mirror band vs spot {rsi_min:.0f}-{rsi_max:.0f}")
    return "PASS" if not failed else "; ".join(failed)


async def _get_live_candidates(
    kite: KiteConnect | None,
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    instrument = "NIFTY"
    if min_dte_calendar_days > 0:
        expiry_str = pick_expiry_with_min_calendar_dte(
            kite,
            instrument,
            min_dte_days=int(min_dte_calendar_days),
            weekday=nifty_weekly_expiry_weekday,
        )
    else:
        expiry_str = pick_primary_expiry_str(kite, instrument)
    if not expiry_str:
        return [], []
    try:
        expiry_date = datetime.strptime(expiry_str.strip().upper(), "%d%b%Y").date()
    except ValueError:
        expiry_date = date.today()
    use_short = str(position_intent).strip().lower() == "short_premium"
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
    if ema_crossover_max_candles is not None:
        indicator_params["max_candles_since_cross"] = ema_crossover_max_candles
    if adx_min_threshold is not None:
        indicator_params["adx_period"] = adx_period
        indicator_params["adx_min_threshold"] = float(adx_min_threshold)
    chain_payload = await asyncio.to_thread(
        fetch_option_chain_sync,
        kite,
        instrument,
        expiry_str,
        max_strike_distance,
        max_strike_distance,
        score_threshold,
        indicator_params if indicator_params else None,
    )
    chain = chain_payload.get("chain", [])
    spot = float(chain_payload.get("spot") or 0.0)
    if not chain or spot <= 0:
        return [], []
    spot_regime = chain_payload.get("spotRegime")
    spot_bull = int(chain_payload.get("spotBullishScore") or 0)
    spot_bear = int(chain_payload.get("spotBearishScore") or 0)
    strike_regime_mode = str(spot_regime_mode or "").strip().lower()
    step = 50
    atm = round(spot / step) * step
    conf_denom = max(1, int(score_max))
    liq_tiers: list[tuple[int, int, bool]] = [(strike_min_oi, strike_min_volume, False)]
    if strike_min_oi > 0 or strike_min_volume > 0:
        liq_tiers.append((0, 0, True))
    recs: list[dict] = []
    for min_oi, min_vol, relaxed_liq in liq_tiers:
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
                if use_short:
                    if strike_regime_mode == "ema_cross_vwap":
                        if opt_type == "PE" and not leg.get("regimeSellPe"):
                            continue
                        if opt_type == "CE" and not leg.get("regimeSellCe"):
                            continue
                    else:
                        if spot_regime == "bullish" and opt_type != "PE":
                            continue
                        if spot_regime == "bearish" and opt_type != "CE":
                            continue
                        if spot_regime not in ("bullish", "bearish"):
                            continue
                ltp = float(leg.get("ltp") or 0.0)
                delta = float(leg.get("delta") or 0.0)
                delta_abs = abs(delta)
                ivr = leg.get("ivr")
                if use_short:
                    if ivr_min_threshold > 0 or ivr_leg_max_threshold > 0:
                        if ivr is None:
                            continue
                        try:
                            ivf = float(ivr)
                            if ivr_min_threshold > 0 and ivf < float(ivr_min_threshold):
                                continue
                            if ivr_leg_max_threshold > 0 and ivf > float(ivr_leg_max_threshold):
                                continue
                        except (TypeError, ValueError):
                            continue
                    if delta_abs < strike_delta_min_abs - 1e-9 or delta_abs > strike_delta_max_abs + 1e-9:
                        continue
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
                    signal_eligible = spot_ok and leg_ok
                    ivr_note = float(ivr) if ivr is not None else None
                    parts: list[str] = []
                    if not spot_ok:
                        parts.append(
                            f"NIFTY spot trend score {score} < {st_int} (regime={spot_regime}, IVR={ivr_note})"
                            if strike_regime_mode != "ema_cross_vwap"
                            else "Strike regime (EMA cross + LTP<VWAP on this leg) not satisfied"
                        )
                    if not leg_ok:
                        leg_detail = _failed_conditions_short_leg(
                            bool(leg.get("primaryOk")),
                            bool(leg.get("emaOk")),
                            bool(leg.get("rsiOk")),
                            rsi_min=rsi_min,
                            rsi_max=rsi_max,
                        )
                        if leg_detail != "PASS":
                            parts.append(leg_detail)
                        else:
                            parts.append(
                                f"Option premium composite score {leg_score} < {int(score_threshold)} "
                                "(crossover/volume vs thresholds)"
                            )
                    failed_msg = "PASS" if signal_eligible else "; ".join(parts)
                    side = "SELL"
                    target_price = round(max(0.05, ltp * 0.75), 2)
                    stop_loss_price = round(ltp * 1.35, 2)
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
                    signal_eligible = bool(leg.get("signalEligible"))
                    side = "BUY"
                    target_price = round(ltp * 1.08, 2)
                    stop_loss_price = round(ltp * 0.94, 2)
                    failed_msg = _failed_conditions(
                        bool(leg.get("primaryOk")),
                        bool(leg.get("emaOk")),
                        bool(leg.get("rsiOk")),
                        rsi_min=rsi_min,
                        rsi_max=rsi_max,
                    )
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
                        "rsi": float(leg.get("rsi") or 0.0),
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
                        "refresh_interval_sec": 30,
                        "distance_to_atm": distance_to_atm,
                        "oi": oi,
                        "oi_chg_pct": oi_chg_pct,
                        "delta": delta,
                        "delta_distance": delta_distance,
                        "option_type": opt_type,
                        "gamma": gamma_val if use_short else 0.0,
                    }
                )
        if recs:
            break
    # Full scan before min-gamma cap / final sort — used for evaluation JSONL.
    scanned_before_rank = list(recs)
    if use_short and select_strike_by_min_gamma and recs:
        eligible_recs = [r for r in recs if r.get("signal_eligible")]
        if eligible_recs:
            eligible_recs.sort(
                key=lambda x: (
                    float(x.get("gamma") or 0.0),
                    x["delta_distance"],
                    abs(x["distance_to_atm"]),
                )
            )
            cap = max(1, int(max_strike_recommendations))
            recs = eligible_recs[:cap]
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
    return recs, scanned_before_rank


async def _get_live_candidates_trendpulse_z(
    kite: KiteConnect | None,
    max_strike_distance: int,
    score_params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Long CE/PE from PS_z vs VS_z cross + HTF bias; strike filters match rule-based long premium."""
    if kite is None:
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
    if min_dte_cal > 0:
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
                "refresh_interval_sec": 30,
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
                        "refresh_interval_sec": 30,
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
    """Get scoreThreshold, scoreMax, autoTradeScoreThreshold from strategy JSON. Marketplace catalog has higher priority; Settings as fallback when catalog has no config."""
    details = None
    catalog_row = await fetchrow(
        """
        SELECT strategy_details_json FROM s004_strategy_catalog
        WHERE strategy_id = $1 AND version = $2
        """,
        strategy_id,
        strategy_version,
    )
    if catalog_row:
        details = catalog_row.get("strategy_details_json")
    if details is None and user_id is not None:
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
            details = user_row.get("strategy_details_json")
    if isinstance(details, str):
        try:
            details = json.loads(details) if details else {}
        except json.JSONDecodeError:
            details = {}
    if not isinstance(details, dict):
        details = {}
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

    position_intent = str(details.get("positionIntent", "long_premium")).strip().lower()
    if position_intent not in ("long_premium", "short_premium"):
        position_intent = "long_premium"
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

    return {
        "strategy_type": strategy_type,
        "position_intent": position_intent,
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


async def ensure_recommendations(user_id: int, kite: KiteConnect | None = None) -> None:
    """Generate recommendations for all users subscribed to the strategy. Uses fallback Kite if user has none.
    Admin: generates for ALL active strategies so Trades screen shows recommendations from every strategy."""
    now_ts = time.time()
    if _REC_CACHE_TS.get(user_id) and (now_ts - _REC_CACHE_TS[user_id]) < 25:
        return

    is_admin = await _is_admin(user_id)
    if is_admin:
        strategy_list = await _get_all_active_strategies()
        if not strategy_list:
            strategy_list = [await _get_user_strategy(user_id)]
    else:
        sid, ver = await _get_user_strategy(user_id)
        strategy_list = [(sid, ver)]

    kite = kite or await _get_kite_for_any_user()
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
                _REC_CACHE_TS[uid] = now_ts
            continue

        strategy_type = score_params.get("strategy_type", "rule-based")
        score_threshold = score_params["score_threshold"]
        score_max = score_params["score_max"]
        ivr_max_threshold = score_params.get("ivr_max_threshold", 20.0)
        ivr_bonus = score_params.get("ivr_bonus", 0)
        position_intent = str(score_params.get("position_intent", "long_premium"))
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

        generated_rows: list[dict] = []
        scanned_for_log: list[dict] | None = None
        fetch_failed = False
        fetch_error: str | None = None
        try:
            if strategy_type == "trendpulse-z":
                if kite is None:
                    fetch_failed = True
                    generated_rows = []
                    fetch_error = "Zerodha session required (kite is None)"
                    _logger.warning(
                        "ensure_recommendations: skip refresh for %s %s — TrendPulse Z needs a Zerodha session "
                        "(connect Kite under Settings for admin or any user).",
                        strategy_id,
                        strategy_version,
                    )
                else:
                    generated_rows = await _get_live_candidates_trendpulse_z(
                        kite,
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
                generated_rows, scanned_for_log = await _get_live_candidates(
                    kite,
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
                )
        except Exception as exc:
            fetch_failed = True
            generated_rows = []
            scanned_for_log = None
            fetch_error = str(exc)
            _logger.warning(
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
        )

        if fetch_failed:
            for uid in subscribed_users:
                _REC_CACHE_TS[uid] = now_ts
            continue

        if not generated_rows:
            _logger.info(
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
                _REC_CACHE_TS[uid] = now_ts
            continue

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

        for rank_idx, rec in enumerate(generated_rows, start=1):
            rec_details = {
                "vwap": rec["vwap"],
                "ema9": rec["ema9"],
                "ema21": rec["ema21"],
                "rsi": rec["rsi"],
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
            }
            for uid in subscribed_users:
                rec_id = f"rec-{uuid4().hex[:10]}"
                await execute(
                    """
                    INSERT INTO s004_trade_recommendations (
                        recommendation_id, strategy_id, strategy_version, user_id, instrument, expiry, symbol, side,
                        entry_price, target_price, stop_loss_price, confidence_score, rank_value, score, reason_code, status, created_at,
                        details_json
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'TREND_SNAP','GENERATED',NOW(),$15::jsonb)
                    ON CONFLICT (recommendation_id) DO NOTHING
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
            _REC_CACHE_TS[uid] = now_ts


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
    if not rec or rec["status"] != "GENERATED":
        raise ValueError("Recommendation not found or already processed.")

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
        if conf < min_confidence_line:
            reasons.append(f"confidence_{round(conf, 2)}_below_{min_confidence_line}")
        if score_val is None:
            reasons.append("score_missing")
        elif score_val < auto_thresh:
            reasons.append("below_auto_trade_score_threshold")
        if signal_eligible is not True:
            reasons.append("signal_not_eligible_vs_display_threshold")
        eligible = (
            conf >= min_confidence_line
            and score_val is not None
            and score_val >= auto_thresh
            and signal_eligible is True
        )
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
    if (await get_platform_trading_paused())[0]:
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
            kite_user = await _get_kite_for_user(user_id)
            if not kite_user:
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


async def get_auto_execute_eligible_recommendations(
    user_id: int,
    mode: str,
    min_confidence: float = 80.0,
    min_score: int | None = None,
) -> list[dict]:
    """Return recommendations that meet auto-execute criteria: score >= autoTradeScoreThreshold, Eligible=Yes (or inferred from score>=threshold), confidence>=80."""
    strategy_id, strategy_version = await _get_user_strategy(user_id)
    score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
    auto_thresh = score_params["auto_trade_score_threshold"]
    score_threshold = float(score_params.get("score_threshold", 3))
    if min_score is None:
        min_score = auto_thresh
    min_score_val = float(min_score)
    rows = await list_recommendations_for_user(
        user_id=user_id,
        status="GENERATED",
        min_confidence=min_confidence,
        sort_by="rank",
        sort_dir="asc",
        limit=50,
        offset=0,
    )
    eligible: list[dict] = []
    for r in rows:
        score = r.get("score")
        if score is None:
            continue
        try:
            score_val = float(score)
        except (TypeError, ValueError):
            continue
        signal_eligible = r.get("signal_eligible")
        if signal_eligible is None:
            signal_eligible = score_val >= score_threshold
        if score_val >= min_score_val and signal_eligible is True:
            r["mode"] = mode
            eligible.append(r)
    return eligible


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
