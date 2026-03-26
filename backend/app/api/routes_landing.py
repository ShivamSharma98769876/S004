"""Landing page: TrendPulse Z series + market context (NIFTY, PCR-style sentiment)."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.api.auth_context import get_user_id
from app.db_client import ensure_user, fetch, fetchrow
from app.services.option_chain_zerodha import (
    fetch_index_candles_sync,
    fetch_indices_spot_sync,
    fetch_option_chain_sync,
    get_expiries_for_instrument,
)
from app.services.option_symbol_compact import parse_compact_option_symbol
from app.services.sentiment_engine import compute_sentiment_snapshot
from app.services.trendpulse_phase3 import apply_trendpulse_hard_gates
from app.services.trendpulse_z import (
    build_trendpulse_chart_series,
    build_trendpulse_entry_events,
    evaluate_trendpulse_signal,
)
from app.services.redis_client import (
    sentiment_history_append as _redis_sentiment_append,
    sentiment_history_fetch as _redis_sentiment_fetch,
    sentiment_history_redis_available,
)
from app.services.news_sentiment import compute_news_sentiment_snapshot, news_sentiment_failure_payload
from app.services.strategy_day_fit import attach_strategy_day_fit_to_snapshot
from app.services.trades_service import get_strategy_score_params, get_kite_for_quotes, _get_user_strategy

TRENDPULSE_STRATEGY_ID = "strat-trendpulse-z"
TRENDPULSE_DEFAULT_VERSION = "1.0.0"

router = APIRouter(prefix="/landing", tags=["landing"])


def _coerce_expiry_str(val: Any) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _trendpulse_recommendation_for_api(row: dict[str, Any]) -> dict[str, Any]:
    """Shape top GENERATED recommendation for tradeSignal.recommendation (camelCase)."""
    sym = str(row.get("symbol") or "")
    parsed = parse_compact_option_symbol(sym)
    out: dict[str, Any] = {
        "recommendationId": str(row.get("recommendation_id") or ""),
        "symbol": sym,
        "instrument": str(row.get("instrument") or ""),
        "expiry": _coerce_expiry_str(row.get("expiry")),
        "side": str(row.get("side") or ""),
        "entryPrice": float(row.get("entry_price") or 0),
        "targetPrice": float(row.get("target_price") or 0),
        "stopLossPrice": float(row.get("stop_loss_price") or 0),
        "confidenceScore": float(row.get("confidence_score") or 0),
        "rankValue": int(row.get("rank_value") or 0),
        "status": str(row.get("status") or ""),
    }
    sc = row.get("score")
    if sc is not None:
        try:
            out["score"] = int(sc)
        except (TypeError, ValueError):
            pass
    if parsed:
        out["strike"] = int(parsed["strike"])
        out["optionType"] = str(parsed["optionType"])
    return out
_BROKER_TIMEOUT_SEC = 8.0
_SENTIMENT_HISTORY_MAX_POINTS = 240
_sentiment_history: dict[int, deque[dict[str, Any]]] = {}
_sentiment_history_lock = asyncio.Lock()


def _history_record(
    *,
    user_id: int,
    market_snapshot: dict[str, Any],
    sentiment: dict[str, Any],
    trendpulse: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    return {
        "userId": user_id,
        "timestamp": updated_at,
        "marketSnapshot": market_snapshot,
        "sentiment": {
            "sentimentLabel": sentiment.get("sentimentLabel"),
            "directionLabel": sentiment.get("directionLabel"),
            "directionScore": sentiment.get("directionScore"),
            "confidence": sentiment.get("confidence"),
            "regime": sentiment.get("regime"),
            "drivers": sentiment.get("drivers") or [],
            "alerts": sentiment.get("alerts") or [],
            "optionsIntel": sentiment.get("optionsIntel") or {},
        },
        "trendpulse": {
            "enabled": bool(trendpulse.get("trendpulseEnabled")),
            "htfBias": trendpulse.get("htfBias"),
            "tradeSignal": trendpulse.get("tradeSignal"),
        },
    }


async def _append_sentiment_history(
    *,
    user_id: int,
    market_snapshot: dict[str, Any],
    sentiment: dict[str, Any],
    trendpulse: dict[str, Any],
    updated_at: str,
) -> None:
    rec = _history_record(
        user_id=user_id,
        market_snapshot=market_snapshot,
        sentiment=sentiment,
        trendpulse=trendpulse,
        updated_at=updated_at,
    )
    # Prefer Redis (capped list + TTL): no Postgres growth, survives API restarts.
    if await sentiment_history_redis_available():
        await _redis_sentiment_append(
            user_id,
            rec,
            max_items=_SENTIMENT_HISTORY_MAX_POINTS,
        )
        return
    async with _sentiment_history_lock:
        if user_id not in _sentiment_history:
            _sentiment_history[user_id] = deque(maxlen=_SENTIMENT_HISTORY_MAX_POINTS)
        _sentiment_history[user_id].append(rec)


async def _load_sentiment_history_rows(user_id: int) -> tuple[list[dict[str, Any]], str, str]:
    if await sentiment_history_redis_available():
        rows = await _redis_sentiment_fetch(user_id)
        return (
            rows,
            "redis capped list (no Postgres; per-user key, max 240 points, TTL SENTIMENT_HISTORY_REDIS_TTL_SEC)",
            "redis",
        )
    async with _sentiment_history_lock:
        mem = list(_sentiment_history.get(user_id) or [])
    return mem, "in-memory ring buffer (set REDIS_URL to persist replay without database)", "memory"


async def _trendpulse_strategy_for_chart(user_id: int) -> tuple[str, str] | None:
    """Which catalog strategy to use for the landing chart: admin, TrendPulse subscriber, or active TPZ strategy."""
    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    if role_row and str(role_row.get("role", "")).upper() == "ADMIN":
        return (TRENDPULSE_STRATEGY_ID, TRENDPULSE_DEFAULT_VERSION)

    sub = await fetchrow(
        """
        SELECT strategy_id, strategy_version FROM s004_strategy_subscriptions
        WHERE user_id = $1 AND strategy_id = $2 AND status = 'ACTIVE'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
        TRENDPULSE_STRATEGY_ID,
    )
    if sub:
        return (str(sub["strategy_id"]), str(sub["strategy_version"]))

    sid, ver = await _get_user_strategy(user_id)
    params = await get_strategy_score_params(sid, ver, user_id)
    if str(params.get("strategy_type", "rule-based")).lower() == "trendpulse-z":
        return (sid, ver)
    return None


def _pcr_sentiment(pcr: float | None) -> str:
    if pcr is None:
        return "—"
    if pcr > 1.15:
        return "Cautious (puts heavier)"
    if pcr < 0.85:
        return "Constructive (calls heavier)"
    return "Balanced"


def _spot_trend_label(change_pct: float | None) -> str:
    if change_pct is None:
        return "—"
    if change_pct > 0.15:
        return "Up"
    if change_pct < -0.15:
        return "Down"
    return "Sideways"


def _to_utc_naive(dt_like: Any) -> datetime | None:
    if isinstance(dt_like, datetime):
        if dt_like.tzinfo is not None:
            return dt_like.astimezone(timezone.utc).replace(tzinfo=None)
        return dt_like
    if isinstance(dt_like, str) and dt_like:
        s = dt_like.replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(s)
            if d.tzinfo is not None:
                return d.astimezone(timezone.utc).replace(tzinfo=None)
            return d
        except ValueError:
            return None
    return None


def _series_time_to_utc_naive(dt_like: Any) -> datetime | None:
    """TrendPulse chart series bar time is exchange-local (IST) when tz-naive; normalize to UTC-naive."""
    d = _to_utc_naive(dt_like)
    if d is None:
        return None
    if isinstance(dt_like, datetime) and dt_like.tzinfo is None:
        ist = ZoneInfo("Asia/Kolkata")
        return dt_like.replace(tzinfo=ist).astimezone(timezone.utc).replace(tzinfo=None)
    if isinstance(dt_like, str) and dt_like:
        s = dt_like.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            return d
        if parsed.tzinfo is None:
            ist = ZoneInfo("Asia/Kolkata")
            return parsed.replace(tzinfo=ist).astimezone(timezone.utc).replace(tzinfo=None)
    return d


async def _fetch_nifty_market_and_chain(kite: Any) -> tuple[float, float, float | None, dict[str, Any] | None]:
    """Fetch NIFTY spot and compact chain payload once for landing widgets/sentiment."""
    nifty_spot = 0.0
    nifty_chg = 0.0
    chain_payload: dict[str, Any] | None = None
    pcr: float | None = None
    if kite:
        try:
            idx = await asyncio.wait_for(
                asyncio.to_thread(fetch_indices_spot_sync, kite),
                timeout=_BROKER_TIMEOUT_SEC,
            )
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
    return nifty_spot, nifty_chg, pcr, chain_payload


@router.get("/market-snapshot")
async def market_snapshot(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    await ensure_user(user_id)
    kite = await get_kite_for_quotes(user_id)
    nifty_spot, nifty_chg, pcr, _ = await _fetch_nifty_market_and_chain(kite)

    return {
        "nifty": {"spot": round(nifty_spot, 2), "changePct": round(nifty_chg, 2)},
        "pcr": round(pcr, 2) if pcr is not None else None,
        "sentimentLabel": _pcr_sentiment(pcr),
        "intradayTrendLabel": _spot_trend_label(nifty_chg),
    }


@router.get("/decision-snapshot")
async def decision_snapshot(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    """Phase-1 composite endpoint: market + sentiment + TrendPulse payload in one call."""
    await ensure_user(user_id)
    kite = await get_kite_for_quotes(user_id)
    nifty_spot, nifty_chg, pcr, chain_payload = await _fetch_nifty_market_and_chain(kite)
    tp, news_raw = await asyncio.gather(
        trendpulse_series(user_id),
        compute_news_sentiment_snapshot(),
        return_exceptions=True,
    )
    if isinstance(tp, BaseException):
        raise tp
    news_sentiment = (
        news_sentiment_failure_payload(news_raw)
        if isinstance(news_raw, BaseException)
        else news_raw
    )
    sentiment = compute_sentiment_snapshot(
        chain_payload=chain_payload,
        spot_chg_pct=nifty_chg,
        trendpulse_signal=tp.get("tradeSignal") if isinstance(tp, dict) else None,
    )
    market = {
        "nifty": {"spot": round(nifty_spot, 2), "changePct": round(nifty_chg, 2)},
        "pcr": round(pcr, 2) if pcr is not None else None,
        "sentimentLabel": sentiment.get("sentimentLabel") or _pcr_sentiment(pcr),
        "intradayTrendLabel": _spot_trend_label(nifty_chg),
    }
    updated_at = datetime.utcnow().isoformat() + "Z"
    strategy_day_fit = await attach_strategy_day_fit_to_snapshot(
        sentiment=sentiment,
        trendpulse=tp if isinstance(tp, dict) else {},
        market=market,
    )
    payload = {
        "marketSnapshot": market,
        "sentiment": sentiment,
        "trendpulse": tp,
        "strategyDayFit": strategy_day_fit,
        "newsSentiment": news_sentiment,
        "updatedAt": updated_at,
    }
    await _append_sentiment_history(
        user_id=user_id,
        market_snapshot=market,
        sentiment=sentiment,
        trendpulse=tp if isinstance(tp, dict) else {},
        updated_at=updated_at,
    )
    return payload


@router.get("/trendpulse-series")
async def trendpulse_series(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    """PS_z / VS_z series for admins, TrendPulse Z subscribers, or when TrendPulse Z is the active strategy."""
    await ensure_user(user_id)
    resolved = await _trendpulse_strategy_for_chart(user_id)
    if resolved is None:
        return {
            "strategyId": None,
            "strategyVersion": None,
            "strategyType": "rule-based",
            "trendpulseEnabled": False,
            "series": None,
            "htfBias": None,
            "message": "Subscribe to TrendPulse Z in Marketplace (or set it as your active strategy) to see this chart.",
        }

    sid, ver = resolved
    params = await get_strategy_score_params(sid, ver, user_id)
    st = str(params.get("strategy_type", "rule-based")).lower()
    if st != "trendpulse-z":
        return {
            "strategyId": sid,
            "strategyVersion": ver,
            "strategyType": st,
            "trendpulseEnabled": False,
            "series": None,
            "htfBias": None,
            "message": "TrendPulse Z configuration is missing in the catalog for this version. Contact admin.",
        }

    tpc = params.get("trendpulse_config") or {}
    if not isinstance(tpc, dict):
        tpc = {}
    z_window = int(tpc.get("zWindow", 50))
    slope_k = int(tpc.get("slopeLookback", 4))
    st_int = str(tpc.get("stInterval", "5minute"))
    htf_int = str(tpc.get("htfInterval", "15minute"))
    days = int(tpc.get("candleDaysBack", 5))
    adx_period = int(tpc.get("adxPeriod", 14))
    htf_ef = int(tpc.get("htfEmaFast", 13))
    htf_es = int(tpc.get("htfEmaSlow", 34))
    adx_min = float(tpc.get("adxMin", 18))

    kite = await get_kite_for_quotes(user_id)
    if not kite:
        return {
            "strategyId": sid,
            "strategyVersion": ver,
            "strategyType": st,
            "trendpulseEnabled": True,
            "series": None,
            "htfBias": None,
            "message": "Broker connection required for historical candles.",
        }

    try:
        st_candles = await asyncio.wait_for(
            asyncio.to_thread(fetch_index_candles_sync, kite, "NIFTY", st_int, days),
            timeout=_BROKER_TIMEOUT_SEC,
        )
        htf_candles = await asyncio.wait_for(
            asyncio.to_thread(fetch_index_candles_sync, kite, "NIFTY", htf_int, days),
            timeout=_BROKER_TIMEOUT_SEC,
        )
    except Exception:
        return {
            "strategyId": sid,
            "strategyVersion": ver,
            "strategyType": st,
            "trendpulseEnabled": True,
            "series": None,
            "htfBias": None,
            "message": "Timed out fetching market candles from broker; please retry.",
        }

    ev = evaluate_trendpulse_signal(
        st_candles,
        htf_candles,
        z_window=z_window,
        slope_lookback=slope_k,
        adx_period=adx_period,
        adx_min=adx_min,
        htf_ema_fast=htf_ef,
        htf_ema_slow=htf_es,
    )
    try:
        _, nifty_chg, pcr_lp, _ = await asyncio.wait_for(
            _fetch_nifty_market_and_chain(kite),
            timeout=_BROKER_TIMEOUT_SEC,
        )
    except Exception:
        nifty_chg, pcr_lp = None, None
    ev = apply_trendpulse_hard_gates(
        ev,
        tpc,
        spot_chg_pct=float(nifty_chg) if nifty_chg is not None else None,
        pcr=float(pcr_lp) if pcr_lp is not None else None,
        now_utc=datetime.now(timezone.utc),
    )
    htf_bias = ev.htf_bias

    series = build_trendpulse_chart_series(
        st_candles,
        z_window=z_window,
        slope_lookback=slope_k,
        tail=120,
        adx_period=adx_period,
        now_utc=datetime.now(timezone.utc),
    )
    tail_start = int(series.get("tail_start_index", 0))
    entry_events = build_trendpulse_entry_events(
        st_candles,
        htf_candles,
        z_window=z_window,
        slope_lookback=slope_k,
        adx_period=adx_period,
        adx_min=adx_min,
        htf_ema_fast=htf_ef,
        htf_ema_slow=htf_es,
        tail_start_index=tail_start,
    )

    trade_events: list[dict[str, Any]] = []
    series_times = series.get("times") or []
    if isinstance(series_times, list) and series_times:
        parsed_times = [_series_time_to_utc_naive(t) for t in series_times]
        if all(t is not None for t in parsed_times):
            ts_points = [t for t in parsed_times if t is not None]
            display_day_raw = str(series.get("displayDate") or "").strip()
            try:
                display_day = date.fromisoformat(display_day_raw)
            except ValueError:
                # Fallback to today's IST session date if displayDate is unexpectedly malformed.
                display_day = datetime.now(ZoneInfo("Asia/Kolkata")).date()
            # opened_at is stored as UTC-naive; IST session date for markers matches chart displayDate.
            # Also fall back from (strategy_id + strategy_version) to strategy_id-only when versions drift.
            trade_rows = await fetch(
                """
                SELECT trade_ref, mode, side, symbol, opened_at
                FROM s004_live_trades
                WHERE user_id = $1
                  AND strategy_id = $2
                  AND strategy_version = $3
                  AND ((opened_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date = $4::date
                ORDER BY opened_at ASC
                LIMIT 250
                """,
                user_id,
                sid,
                ver,
                display_day,
            )
            if not trade_rows:
                trade_rows = await fetch(
                    """
                    SELECT trade_ref, mode, side, symbol, opened_at
                    FROM s004_live_trades
                    WHERE user_id = $1
                      AND strategy_id = $2
                      AND ((opened_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date = $3::date
                    ORDER BY opened_at ASC
                    LIMIT 250
                    """,
                    user_id,
                    sid,
                    display_day,
                )
            for tr in trade_rows or []:
                ot = _to_utc_naive(tr.get("opened_at"))
                if ot is None:
                    continue
                nearest_idx = None
                nearest_abs = None
                for idx, t in enumerate(ts_points):
                    delta = abs((t - ot).total_seconds())
                    if nearest_abs is None or delta < nearest_abs:
                        nearest_abs = delta
                        nearest_idx = idx
                if nearest_idx is None:
                    continue
                sym = str(tr.get("symbol") or "")
                parsed = parse_compact_option_symbol(sym)
                te: dict[str, Any] = {
                    "tailIndex": int(nearest_idx),
                    # opened_at is stored as UTC-naive in DB; append Z so frontend parses it as UTC explicitly.
                    "openedAt": f"{ot.isoformat()}Z",
                    "mode": str(tr.get("mode") or "PAPER"),
                    "side": str(tr.get("side") or ""),
                    "symbol": sym,
                    "tradeRef": str(tr.get("trade_ref") or ""),
                }
                if parsed:
                    te["strike"] = int(parsed["strike"])
                    te["optionType"] = str(parsed["optionType"])
                    te["underlying"] = str(parsed["underlying"])
                trade_events.append(te)

    # Attach strike from executed trade at same chart index (paper/live is user setting — chart shows signal only).
    trades_by_idx: dict[int, dict[str, Any]] = {}
    for te in trade_events:
        tid = int(te.get("tailIndex", -1))
        if tid >= 0:
            trades_by_idx[tid] = te
    for entry_ev in entry_events:
        entry_ev["leg"] = "CE" if str(entry_ev.get("cross")) == "bullish" else "PE"
        mate = trades_by_idx.get(int(entry_ev.get("tailIndex", -1)))
        if mate:
            if mate.get("strike") is not None:
                entry_ev["strike"] = mate["strike"]
            if mate.get("optionType"):
                entry_ev["optionType"] = mate["optionType"]
            if mate.get("symbol"):
                entry_ev["optionSymbol"] = str(mate["symbol"])

    rec_row = await fetchrow(
        """
        SELECT recommendation_id, symbol, instrument, expiry, side, entry_price, target_price, stop_loss_price,
               confidence_score, rank_value, score, status, created_at
        FROM s004_trade_recommendations
        WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
          AND status = 'GENERATED'
        ORDER BY rank_value ASC NULLS LAST, created_at DESC
        LIMIT 1
        """,
        user_id,
        sid,
        ver,
    )
    n_chart = len(series_times) if isinstance(series_times, list) else 0
    last_chart_idx = n_chart - 1 if n_chart > 0 else -1
    if rec_row and last_chart_idx >= 0:
        plan_sym = str(rec_row.get("symbol") or "").strip()
        if plan_sym:
            for entry_ev in entry_events:
                if int(entry_ev.get("tailIndex", -1)) == last_chart_idx and not entry_ev.get("optionSymbol"):
                    entry_ev["planSymbol"] = plan_sym
                    break

    st_label = st_int.replace("minute", "m").replace("m", "m")
    if ev.ok:
        leg = "Long CE (buy call)" if ev.cross == "bullish" else "Long PE (buy put)"
        cross_w = "above" if ev.cross == "bullish" else "below"
        trade_summary = (
            f"Entry signal: {leg}. On latest {st_label} bar, PS_z crossed {cross_w} VS_z; "
            f"HTF bias is {ev.htf_bias}; ADX {ev.adx_st:.1f} ≥ {adx_min:.0f}."
        )
    else:
        trade_summary = ev.reason

    trade_signal: dict[str, Any] = {
        "entryEligible": ev.ok,
        "htfBias": ev.htf_bias,
        "cross": ev.cross,
        "psZ": round(ev.ps_z, 4),
        "vsZ": round(ev.vs_z, 4),
        "adxSt": round(ev.adx_st, 2),
        "adxMin": adx_min,
        "reasonCode": ev.reason,
        "summary": trade_summary,
    }

    trade_signal["recommendation"] = _trendpulse_recommendation_for_api(dict(rec_row)) if rec_row else None

    sess = tpc.get("session") if isinstance(tpc.get("session"), dict) else {}
    brd = tpc.get("breadth") if isinstance(tpc.get("breadth"), dict) else {}

    return {
        "strategyId": sid,
        "strategyVersion": ver,
        "strategyType": st,
        "trendpulseEnabled": True,
        "stInterval": st_int,
        "htfInterval": htf_int,
        "htfBias": htf_bias,
        "series": series,
        "entryEvents": entry_events,
        "tradeEvents": trade_events,
        "tradeSignal": trade_signal,
        "phase3": {
            "profile": str(tpc.get("profile") or "balanced"),
            "sessionEnabled": bool(sess.get("enabled")),
            "breadthEnabled": bool(brd.get("enabled")),
            "chartNote": "Chart shows one IST session day (today when bars exist); z-scores use full history. Phase-3 gates apply to live eligibility only.",
        },
        "message": None,
    }


@router.get("/sentiment-history")
async def sentiment_history(
    user_id: int = Depends(get_user_id),
    limit: int = Query(default=60, ge=1, le=_SENTIMENT_HISTORY_MAX_POINTS),
) -> dict[str, Any]:
    """
    Phase-2 replay endpoint.
    Returns recent sentiment snapshots in chronological order for trend widget + driver waterfall replay.
    """
    await ensure_user(user_id)
    rows, retention, storage = await _load_sentiment_history_rows(user_id)
    if not rows:
        return {
            "limit": int(limit),
            "available": 0,
            "storage": storage,
            "retention": retention,
            "points": [],
            "replay": [],
        }

    tail = rows[-int(limit) :]
    points = []
    for r in tail:
        s = r.get("sentiment") or {}
        oi = s.get("optionsIntel") if isinstance(s.get("optionsIntel"), dict) else {}
        points.append(
            {
                "timestamp": r.get("timestamp"),
                "directionScore": float(s.get("directionScore") or 0.0),
                "confidence": int(s.get("confidence") or 0),
                "directionLabel": str(s.get("directionLabel") or "NEUTRAL"),
                "sentimentLabel": str(s.get("sentimentLabel") or "Balanced"),
                "regime": str(s.get("regime") or "RANGE_CHOP"),
                "modelOptionTilt": str(oi.get("modelOptionTilt") or "NEUTRAL"),
                "ceStrengthPct": int(oi.get("ceStrengthPct") or 50),
            }
        )
    return {
        "limit": int(limit),
        "available": len(rows),
        "storage": storage,
        "retention": retention,
        "points": points,
        "replay": tail,
    }
