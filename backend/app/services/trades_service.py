from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from kiteconnect import KiteConnect

from app.db_client import execute, fetch, fetchrow
from app.services.heuristic_scorer import score_leg as heuristic_score_leg
from app.services.heuristic_enhancements import (
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
from app.services.option_chain_zerodha import fetch_option_chain_sync, get_expiries_for_instrument


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
) -> list[dict]:
    instrument = "NIFTY"
    expiries = get_expiries_for_instrument(instrument)
    if not expiries:
        return []
    expiry_str = expiries[0]
    indicator_params: dict[str, Any] = {
        "rsi_min": float(rsi_min),
        "rsi_max": float(rsi_max),
        "volume_min_ratio": float(volume_min_ratio),
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
        indicator_params if indicator_params else None,
    )
    chain = chain_payload.get("chain", [])
    spot = float(chain_payload.get("spot") or 0.0)
    if not chain or spot <= 0:
        return []
    step = 50
    atm = round(spot / step) * step
    recs: list[dict] = []
    for row in chain:
        strike = int(float(row.get("strike", 0)))
        distance_to_atm = int((strike - atm) / step)
        if abs(distance_to_atm) > strike_max_otm_steps:
            continue
        for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
            leg = row.get(leg_key) or {}
            oi = int(float(leg.get("oi") or 0))
            volume = int(float(leg.get("volume") or 0))
            if oi < strike_min_oi or volume < strike_min_volume:
                continue
            score = int(leg.get("score") or 0)
            ivr = leg.get("ivr")
            if ivr_bonus > 0 and ivr is not None:
                try:
                    ivr_val = float(ivr)
                    if ivr_val < ivr_max_threshold:
                        score = min(score_max, score + ivr_bonus)
                except (TypeError, ValueError):
                    pass
            signal_eligible = bool(leg.get("signalEligible"))
            vol_ratio = float(leg.get("volumeSpikeRatio") or 0.0)
            base_conf = (score / max(1, score_max)) * 100
            vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
            confidence = min(99.0, round(base_conf + vol_bonus, 2))
            ltp = float(leg.get("ltp") or 0.0)
            primary_ok = bool(leg.get("primaryOk"))
            ema_ok = bool(leg.get("emaOk"))
            ema_crossover_ok = bool(leg.get("emaCrossoverOk"))
            rsi_ok = bool(leg.get("rsiOk"))
            volume_ok = bool(leg.get("volumeOk"))
            delta = float(leg.get("delta") or 0.0)
            oi_chg_pct = float(leg.get("oiChgPct") or 0.0)
            target_delta = strike_delta_ce if opt_type == "CE" else strike_delta_pe
            delta_distance = abs(delta - target_delta)
            symbol = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(instrument, expiry_str, strike, opt_type)
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
                    "failed_conditions": _failed_conditions(
                        primary_ok, ema_ok, rsi_ok,
                        rsi_min=rsi_min, rsi_max=rsi_max,
                    ),
                    "spot_price": round(spot, 2),
                    "timeframe": "3m",
                    "refresh_interval_sec": 30,
                    "distance_to_atm": distance_to_atm,
                    "oi": oi,
                    "oi_chg_pct": oi_chg_pct,
                    "delta": delta,
                    "delta_distance": delta_distance,
                }
            )
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
    expiries = get_expiries_for_instrument(instrument)
    if not expiries:
        return []
    expiry_str = expiries[0]
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

    recs: list[dict] = []
    for row in chain:
        strike = int(float(row.get("strike", 0)))
        distance_to_atm = int((strike - atm) / step)
        if abs(distance_to_atm) > strike_max_otm_steps:
            continue
        for leg_key, opt_type in (("call", "CE"), ("put", "PE")):
            leg = row.get(leg_key) or {}
            oi = int(float(leg.get("oi") or 0))
            volume = int(float(leg.get("volume") or 0))
            if oi < strike_min_oi or volume < strike_min_volume:
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

            base_conf = (enhanced_score / max(1.0, score_max)) * 100
            vol_bonus = max(0.0, min(19.0, (vol_ratio - 1.0) * 10))
            confidence = min(99.0, round(base_conf + vol_bonus, 2))
            heuristic_reasons = "; ".join(extra_reasons) if extra_reasons else "PASS"
            symbol = str(leg.get("tradingsymbol") or "").strip() or _compact_option_symbol(instrument, expiry_str, strike, opt_type)
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

    return {
        "strategy_type": strategy_type,
        "heuristics": heuristics_cfg,
        "heuristics_ce": heuristics_ce,
        "heuristics_pe": heuristics_pe,
        "heuristic_enhancements": heuristic_enhancements,
        "score_threshold_ce": score_threshold_ce,
        "score_threshold_pe": score_threshold_pe,
        "score_threshold": float(details.get("scoreThreshold", 3)),
        "score_max": int(details.get("scoreMax", 6)),
        "auto_trade_score_threshold": float(details.get("autoTradeScoreThreshold", 4)),
        "ivr_max_threshold": float(ivr_cfg.get("maxThreshold", 20)),
        "ivr_bonus": int(ivr_cfg.get("bonus", 0)),
        "ema_crossover_max_candles": ema_cross_cfg.get("maxCandlesSinceCross"),
        "adx_period": int(adx_cfg.get("period", 14)),
        "adx_min_threshold": adx_cfg.get("minThreshold"),
        "rsi_min": float(rsi_cfg.get("min", 50)),
        "rsi_max": float(rsi_cfg.get("max", 75)),
        "volume_min_ratio": float(vol_cfg.get("minRatio", 1.5)),
        "strike_min_oi": int(strike_cfg.get("minOi", 10000)),
        "strike_min_volume": int(strike_cfg.get("minVolume", 500)),
        "strike_delta_ce": float(strike_cfg.get("deltaPreferredCE", 0.35)),
        "strike_delta_pe": float(strike_cfg.get("deltaPreferredPE", -0.35)),
        "strike_max_otm_steps": int(strike_cfg.get("maxOtmSteps", 3)),
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
    row = await fetchrow(
        """
        SELECT strategy_id, strategy_version FROM s004_user_strategy_settings
        WHERE user_id = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if row:
        return str(row["strategy_id"]), str(row["strategy_version"])
    # User has no settings - check ACTIVE subscription (e.g. subscribed in Marketplace but never opened Settings).
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

        score_params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
        strategy_type = score_params.get("strategy_type", "rule-based")
        score_threshold = score_params["score_threshold"]
        score_max = score_params["score_max"]
        ivr_max_threshold = score_params.get("ivr_max_threshold", 20.0)
        ivr_bonus = score_params.get("ivr_bonus", 0)
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

        generated_rows: list[dict] = []
        try:
            if strategy_type == "heuristic-voting":
                heuristics_cfg = score_params.get("heuristics") or {}
                raw_enh = score_params.get("heuristic_enhancements")
                enhancement_cfg = (
                    HeuristicEnhancementConfig.from_dict(raw_enh)
                    if isinstance(raw_enh, dict)
                    else None
                )
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
                generated_rows = await _get_live_candidates(
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
                )
        except Exception:
            generated_rows = []

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
            }
            for uid in subscribed_users:
                rec_id = f"rec-{uuid4().hex[:10]}"
                await execute(
                    """
                    INSERT INTO s004_trade_recommendations (
                        recommendation_id, strategy_id, strategy_version, user_id, instrument, expiry, symbol, side,
                        entry_price, target_price, stop_loss_price, confidence_score, rank_value, score, reason_code, status, created_at
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'TREND_SNAP','GENERATED',NOW())
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
) -> dict:
    """Execute a recommendation and create trade. Returns {trade_ref, order_ref}."""
    await _check_mode_approval(user_id, mode)
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
        SELECT 1 FROM s004_live_trades
        WHERE user_id = $1 AND symbol = $2 AND mode = $3 AND current_state <> 'EXIT'
        LIMIT 1
        """,
        user_id,
        rec["symbol"],
        mode,
    )
    if existing:
        raise ValueError(f"You already have an open {mode} trade for {rec['symbol']}. Close it first to open another.")

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

    await execute(
        """
        INSERT INTO s004_live_trades (
            trade_ref, order_ref, recommendation_id, user_id, strategy_id, strategy_version, symbol, mode, side, quantity,
            entry_price, current_price, target_price, stop_loss_price, current_state,
            realized_pnl, unrealized_pnl, broker_order_id, opened_at, created_at, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11,$12,$13,'ACTIVE',0,0,$14,NOW(),NOW(),NOW())
        """,
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
    """Get trade_start and trade_end from user's strategy settings. Uses IST for market hours. Defaults 09:20–15:00."""
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
        return time(9, 20), time(15, 0)
    start = row.get("trade_start")
    end = row.get("trade_end")
    if start is None or end is None:
        return time(9, 20), time(15, 0)

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

    start_t = _parse_time(start) or time(9, 20)
    end_t = _parse_time(end) or time(15, 0)
    return start_t, end_t


def _is_within_trade_window(trade_start: datetime.time, trade_end: datetime.time) -> bool:
    """True if current IST time is within [trade_start, trade_end] (inclusive)."""
    now_ist = datetime.now(ZoneInfo("Asia/Kolkata")).time()
    if trade_start <= trade_end:
        return trade_start <= now_ist <= trade_end
    # Window spans midnight (e.g. 22:00–02:00) – treat as outside for typical market hours
    return trade_start <= now_ist or now_ist <= trade_end


async def run_auto_execute_cycle() -> None:
    """Run auto-execute for all users with engine_running=true. Respects Trade Start/End from Settings. Picks trades with score >= threshold, Eligible=Yes, confidence>=80."""
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
        trade_start, trade_end = await _get_trade_window(user_id)
        if not _is_within_trade_window(trade_start, trade_end):
            continue
        if mode == "LIVE":
            kite_user = await _get_kite_for_user(user_id)
            if not kite_user:
                continue  # Skip LIVE users without valid Kite credentials
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
                continue

            trades_today = await fetchrow(
                """
                SELECT COUNT(*) AS n FROM s004_live_trades
                WHERE user_id = $1 AND created_at::date = CURRENT_DATE
                """,
                user_id,
            )
            trades_today = int(trades_today["n"] or 0) if trades_today else 0
            max_per_day = int(r.get("max_trades_day") or 4)
            if trades_today >= max_per_day:
                continue

            kite = await _get_kite_for_any_user()  # Shared API for recommendations; user's connection only needed for Live execution
            await ensure_recommendations(user_id, kite)
            eligible = await get_auto_execute_eligible_recommendations(user_id, mode, min_confidence=80.0)
            for rec in eligible[: max_parallel - open_trades]:
                try:
                    await execute_recommendation(user_id, rec["recommendation_id"], mode, quantity=1, manual=False)
                except Exception:
                    pass
        except Exception:
            pass


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
    cache = _REC_DETAILS_CACHE.get(user_id, {})
    eligible: list[dict] = []
    for r in rows:
        rec_id = r["recommendation_id"]
        details = cache.get(rec_id, {})
        score = details.get("score") if details.get("score") is not None else r.get("score")
        if score is None:
            continue
        try:
            score_val = float(score)
        except (TypeError, ValueError):
            continue
        signal_eligible = details.get("signal_eligible")
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
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name
            FROM (
                SELECT recommendation_id, symbol, instrument, expiry, side, entry_price, target_price, stop_loss_price,
                       confidence_score, rank_value, score, status, created_at, strategy_id, strategy_version,
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
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name
            FROM (
                SELECT recommendation_id, symbol, instrument, expiry, side, entry_price, target_price, stop_loss_price,
                       confidence_score, rank_value, score, status, created_at, strategy_id, strategy_version,
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
    cache = _REC_DETAILS_CACHE.get(user_id, {})
    for r in rows:
        item = dict(r)
        item.update(cache.get(item["recommendation_id"], {}))
        enriched.append(item)
    return enriched
