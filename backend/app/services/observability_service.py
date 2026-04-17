"""Batched observability snapshot: spot indicators for subscribed strategies (same paths as the engine)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.db_client import fetch, fetchrow
from app.services.broker_runtime import ZerodhaProvider, resolve_broker_context
from app.services.ist_time_sql import IST_TODAY, opened_at_ist_date
from app.services.option_chain_zerodha import (
    _parse_candle_time_ist,
    fetch_option_minute_candles_today_ist_sync,
    nifty_index_candles_current_session,
    running_typical_price_average_series,
    sorted_candles_chronological,
)
from app.services.trades_service import get_strategy_score_params
from app.strategies.ps_vs_mtf import (
    compute_ps_vs_mtf_observability_series,
    resolve_ps_vs_mtf_config,
)
from app.strategies.stochastic_bnf import (
    compute_stochastic_bnf_observability_series,
    resolve_stochastic_bnf_config,
)
from app.strategies.supertrend_trail import (
    compute_supertrend_trail_observability_series,
    evaluate_supertrend_trail_signal,
    map_settings_timeframe_to_kite_interval,
    resolve_supertrend_trail_config,
)

OBS_SUPPORTED: frozenset[str] = frozenset(
    {"strat-stochastic-bnf", "strat-supertrend-trail", "strat-ps-vs-mtf"}
)
_CACHE_TTL_SEC = 45.0
_cache: dict[int, tuple[float, dict[str, Any]]] = {}


def _cache_get(user_id: int) -> dict[str, Any] | None:
    ent = _cache.get(user_id)
    if not ent:
        return None
    ts, payload = ent
    if time.monotonic() - ts > _CACHE_TTL_SEC:
        _cache.pop(user_id, None)
        return None
    return payload


def _cache_set(user_id: int, payload: dict[str, Any]) -> None:
    _cache[user_id] = (time.monotonic(), payload)


def _zerodha_kite_from_ctx(market_data: Any) -> Any:
    if isinstance(market_data, ZerodhaProvider):
        return market_data.kite
    return None


def _parse_ts_unix(row: dict[str, Any], key: str) -> int | None:
    raw = row.get(key)
    if raw is None:
        return None
    if hasattr(raw, "timestamp"):
        try:
            return int(raw.timestamp())
        except Exception:
            return None
    return None


def _obs_candles_with_session_fallback(
    candles: list[dict[str, Any]],
    *,
    min_required: int,
    fallback_tail: int = 240,
) -> tuple[list[dict[str, Any]], str]:
    """Prefer today's session candles; fallback to recent history when session is short/off-hours."""
    if not candles:
        return [], "empty"
    sess = nifty_index_candles_current_session(candles)
    if len(sess) >= min_required:
        return sess, "today_session"
    valid = sorted_candles_chronological(candles)
    if not valid:
        return [], "empty"
    return valid[-max(min_required, fallback_tail) :], "fallback_recent"


def _opt_side(sym: str) -> str | None:
    u = str(sym or "").upper().strip()
    if u.endswith("CE"):
        return "CE"
    if u.endswith("PE"):
        return "PE"
    return None


async def _fetch_obs_subscriptions(user_id: int) -> list[dict[str, Any]]:
    rows = await fetch(
        """
        SELECT s.strategy_id, s.strategy_version,
               COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version) AS display_name
        FROM s004_strategy_subscriptions s
        LEFT JOIN s004_strategy_catalog c ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
        WHERE s.user_id = $1 AND s.status = 'ACTIVE'
        ORDER BY display_name
        """,
        user_id,
    )
    out: list[dict[str, Any]] = []
    for r in rows or []:
        sid = str(r.get("strategy_id") or "")
        if sid not in OBS_SUPPORTED:
            continue
        out.append(
            {
                "strategy_id": sid,
                "strategy_version": str(r.get("strategy_version") or ""),
                "display_name": str(r.get("display_name") or sid),
            }
        )
    return out


async def _latest_option_symbol_for_strategy(user_id: int, strategy_id: str) -> str | None:
    row = await fetchrow(
        """
        SELECT symbol FROM s004_live_trades
        WHERE user_id = $1 AND strategy_id = $2
          AND current_state IN ('ENTRY', 'ACTIVE', 'TRAIL')
        ORDER BY opened_at DESC
        LIMIT 1
        """,
        user_id,
        strategy_id,
    )
    if row and str(row.get("symbol") or "").strip():
        return str(row["symbol"]).strip()
    row2 = await fetchrow(
        f"""
        SELECT symbol FROM s004_live_trades t
        WHERE t.user_id = $1 AND t.strategy_id = $2
          AND {opened_at_ist_date('t')} = {IST_TODAY}
        ORDER BY t.opened_at DESC
        LIMIT 1
        """,
        user_id,
        strategy_id,
    )
    if row2 and str(row2.get("symbol") or "").strip():
        return str(row2["symbol"]).strip()
    return None


async def _fetch_trade_markers(user_id: int, strategy_id: str) -> list[dict[str, Any]]:
    q = f"""
        SELECT trade_ref, symbol, mode, current_state, opened_at, closed_at, entry_price
        FROM s004_live_trades t
        WHERE t.user_id = $1 AND t.strategy_id = $2
          AND t.mode IN ('LIVE', 'PAPER')
          AND (
            {opened_at_ist_date('t')} = {IST_TODAY}
            OR (t.closed_at IS NOT NULL AND ((t.closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date = {IST_TODAY})
          )
        ORDER BY t.opened_at ASC
    """
    rows = await fetch(q, user_id, strategy_id)
    markers: list[dict[str, Any]] = []
    for r in rows or []:
        sym = str(r.get("symbol") or "")
        side = _opt_side(sym)
        o_at = _parse_ts_unix(r, "opened_at")
        if o_at is not None:
            markers.append(
                {
                    "kind": "ENTRY",
                    "time": o_at,
                    "tradeRef": str(r.get("trade_ref") or ""),
                    "symbol": sym,
                    "mode": str(r.get("mode") or ""),
                    "side": side,
                    "price": float(r.get("entry_price") or 0),
                }
            )
        if str(r.get("current_state") or "").upper() == "EXIT":
            c_at = _parse_ts_unix(r, "closed_at")
            if c_at is not None:
                markers.append(
                    {
                        "kind": "EXIT",
                        "time": c_at,
                        "tradeRef": str(r.get("trade_ref") or ""),
                        "symbol": sym,
                        "mode": str(r.get("mode") or ""),
                        "side": side,
                        "price": float(r.get("entry_price") or 0),
                    }
                )
    markers.sort(key=lambda x: x.get("time") or 0)
    return markers


def _option_vwap_minute_points(minute_candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not minute_candles:
        return []
    run = running_typical_price_average_series(minute_candles)
    out: list[dict[str, Any]] = []
    for i, c in enumerate(minute_candles):
        dti = _parse_candle_time_ist(c)
        if dti is None:
            continue
        out.append({"time": int(dti.timestamp()), "value": round(float(run[i]), 4)})
    return out


async def build_observability_snapshot(user_id: int, *, use_cache: bool = True) -> dict[str, Any]:
    """One batched payload for all Phase-1 strategies the user actively subscribes to."""
    if use_cache:
        hit = _cache_get(user_id)
        if hit is not None:
            return hit

    ctx = await resolve_broker_context(user_id, mode="PAPER")
    md = ctx.market_data
    if md is None or not await md.session_ok():
        payload = {
            "fetchedAt": int(time.time()),
            "brokerSource": ctx.source,
            "panels": [],
            "error": "Broker session unavailable for market data. Connect Zerodha (or use platform shared Paper) in Settings → Brokers.",
        }
        _cache_set(user_id, payload)
        return payload

    kite = _zerodha_kite_from_ctx(md)
    subs = await _fetch_obs_subscriptions(user_id)
    panels: list[dict[str, Any]] = []

    for sub in subs:
        sid = sub["strategy_id"]
        ver = sub["strategy_version"]
        score_params = await get_strategy_score_params(sid, ver, user_id)
        markers = await _fetch_trade_markers(user_id, sid)

        if sid == "strat-stochastic-bnf":
            raw_cfg = score_params.get("stochastic_bnf_config")
            cfg = resolve_stochastic_bnf_config(raw_cfg if isinstance(raw_cfg, dict) else {})
            interval = map_settings_timeframe_to_kite_interval(score_params.get("settings_timeframe"))
            days_back = max(2, int(cfg.get("candleDaysBack", 8) or 8))
            candles = await md.index_candles("BANKNIFTY", interval=interval, days_back=days_back)
            obs_candles, source = _obs_candles_with_session_fallback(candles, min_required=20)
            series = compute_stochastic_bnf_observability_series(obs_candles, cfg)
            panels.append(
                {
                    "kind": "stochastic_bnf",
                    "strategyId": sid,
                    "strategyVersion": ver,
                    "displayName": sub["display_name"],
                    "instrument": "BANKNIFTY",
                    "interval": interval,
                    "series": series,
                    "dataSource": source,
                    "markers": markers,
                }
            )
            continue

        if sid == "strat-ps-vs-mtf":
            raw_cfg = score_params.get("ps_vs_mtf_config")
            cfg = resolve_ps_vs_mtf_config(raw_cfg if isinstance(raw_cfg, dict) else {})
            days_back = max(2, int(cfg.get("candleDaysBack", 8) or 8))
            # Same 3m-only path as recommendation engine (no extra interval fetch).
            candles = await md.index_candles("BANKNIFTY", interval="3minute", days_back=days_back)
            obs_candles, source = _obs_candles_with_session_fallback(candles, min_required=30)
            series = compute_ps_vs_mtf_observability_series(obs_candles, cfg)
            panels.append(
                {
                    "kind": "ps_vs_mtf",
                    "strategyId": sid,
                    "strategyVersion": ver,
                    "displayName": sub["display_name"],
                    "instrument": "BANKNIFTY",
                    "interval": "3minute",
                    "series": series,
                    "dataSource": source,
                    "markers": markers,
                }
            )
            continue

        if sid == "strat-supertrend-trail":
            raw_cfg = score_params.get("supertrend_trail_config")
            cfg = resolve_supertrend_trail_config(raw_cfg if isinstance(raw_cfg, dict) else {})
            interval = map_settings_timeframe_to_kite_interval(score_params.get("settings_timeframe"))
            days_back = max(2, int(cfg.get("candleDaysBack", 5) or 5))
            candles = await md.index_candles("NIFTY", interval=interval, days_back=days_back)
            # Must match recommendation path: EMA/ST are computed on full multi-day series, not session-only.
            # Session-only strips (see _obs_candles_with_session_fallback) reset/warm EMAs and invert stacks vs engine.
            obs_candles = sorted_candles_chronological(candles)
            source = "full_history" if obs_candles else "empty"
            series = compute_supertrend_trail_observability_series(obs_candles, cfg)
            ev_sig = evaluate_supertrend_trail_signal(obs_candles, cfg)
            sig_m = ev_sig.get("metrics") if isinstance(ev_sig.get("metrics"), dict) else {}
            signal_bar: dict[str, Any] = {
                "ok": bool(ev_sig.get("ok")),
                "reason": ev_sig.get("reason"),
                "direction": ev_sig.get("direction"),
                "close": sig_m.get("close"),
                "closePrev": sig_m.get("close_prev"),
                "emaFast": sig_m.get("ema10"),
                "emaSlow": sig_m.get("ema20"),
                "emaFastPrev": sig_m.get("ema10_prev"),
                "emaSlowPrev": sig_m.get("ema20_prev"),
            }
            opt_sym = await _latest_option_symbol_for_strategy(user_id, sid)
            opt_pts: list[dict[str, Any]] = []
            if kite and opt_sym:

                def _opt_vwap_sync() -> list[dict[str, Any]]:
                    return _option_vwap_minute_points(
                        fetch_option_minute_candles_today_ist_sync(kite, opt_sym)
                    )

                opt_pts = await asyncio.to_thread(_opt_vwap_sync)
            panel = {
                "kind": "supertrend_trail",
                "strategyId": sid,
                "strategyVersion": ver,
                "displayName": sub["display_name"],
                "instrument": "NIFTY",
                "interval": interval,
                "optionSymbol": opt_sym,
                "series": series,
                "dataSource": source,
                "signalBar": signal_bar,
                "optionVwap": opt_pts,
                "markers": markers,
            }
            panels.append(panel)

    payload = {
        "fetchedAt": int(time.time()),
        "brokerSource": ctx.source,
        "panels": panels,
    }
    _cache_set(user_id, payload)
    return payload
