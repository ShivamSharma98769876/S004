"""Landing page: TrendPulse Z series + market context (NIFTY, PCR-style sentiment)."""

from __future__ import annotations

import asyncio
import os
import time
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
    fetch_nifty_spot_trail_5m_for_session_sync,
    fetch_option_chain_sync,
    get_expiries_for_analytics,
    get_expiries_for_instrument,
    nifty_index_candles_current_session,
)
from app.services.option_symbol_compact import parse_compact_option_symbol
from app.services.sentiment_engine import compute_sentiment_snapshot, compute_sideways_regime_snapshot
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
from app.services.landing_oi_walls import build_oi_walls_from_chain, oi_walls_stub
from app.services.strategy_day_fit import attach_strategy_day_fit_to_snapshot
from app.services.broker_runtime import resolve_broker_context
from app.services.market_data_kite_session import get_market_data_session_bundle
from app.services.trades_service import get_strategy_score_params, get_kite_for_quotes, _get_user_strategy

TRENDPULSE_STRATEGY_ID = "strat-trendpulse-z"
TRENDPULSE_DEFAULT_VERSION = "1.0.0"

router = APIRouter(prefix="/landing", tags=["landing"])


async def _async_empty_list() -> list[Any]:
    return []


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
# Landing: one chain fetch (not two in parallel) to avoid Kite 429; wider window needs more time for indicators.
_LANDING_CHAIN_STRIKES_HALF = 20
_LANDING_CHAIN_TIMEOUT_SEC = 28.0
_LANDING_CHAIN_FALLBACK_HALF = 8
_LANDING_DECISION_BUNDLE_TIMEOUT_SEC = 10.0
_LANDING_TRENDPULSE_TIMEOUT_SEC = 8.0
_LANDING_REGIME_CANDLES_TIMEOUT_SEC = 6.0
_SENTIMENT_HISTORY_MAX_POINTS = 240


def _nifty_primary_expiry_str(kite: Any) -> tuple[str | None, str]:
    """Nearest NIFTY expiry: real NFO list when Kite is available (same as Analytics), else estimated weeklies."""
    if kite is not None:
        ex, src = get_expiries_for_analytics(kite, "NIFTY")
        if ex:
            return str(ex[0]), src
    est = get_expiries_for_instrument("NIFTY")
    if est:
        return str(est[0]), "estimated_weeklies"
    return None, "none"
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
            "inputs": sentiment.get("inputs") or {},
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


async def _fetch_nifty_15m_trend(provider: Any | None, kite: Any | None) -> dict[str, Any]:
    """Last completed 15m bar vs prior bar on NIFTY (IST session), for landing Trend row."""
    candles: list[Any] = []
    try:
        if provider:
            candles = await asyncio.wait_for(
                provider.index_candles("NIFTY", "15minute", 5),
                timeout=7.0,
            )
        elif kite:
            candles = await asyncio.wait_for(
                asyncio.to_thread(fetch_index_candles_sync, kite, "NIFTY", "15minute", 5),
                timeout=7.0,
            )
    except Exception:
        candles = []
    if not isinstance(candles, list):
        candles = []
    sess = nifty_index_candles_current_session(candles)
    closes = [float(c.get("close") or 0) for c in sess if float(c.get("close") or 0) > 0]
    if len(closes) < 2:
        closes = [float(c.get("close") or 0) for c in candles if float(c.get("close") or 0) > 0]
    change_pct: float | None = None
    if len(closes) >= 2:
        prev_c, last_c = closes[-2], closes[-1]
        if prev_c > 0:
            change_pct = round((last_c - prev_c) / prev_c * 100, 2)
    label = _spot_trend_label(change_pct) if change_pct is not None else "—"
    return {"changePct": change_pct, "trendLabel": label}


async def _empty_nifty_15m_trend() -> dict[str, Any]:
    return {"changePct": None, "trendLabel": "—"}


def _coalesce_nifty_from_chain(
    nifty_spot: float,
    nifty_chg: float,
    chain_payload: dict[str, Any] | None,
) -> tuple[float, float]:
    """If index quote returned 0 but option-chain build has a spot, use chain (same session as PCR)."""
    if not chain_payload:
        return nifty_spot, nifty_chg
    try:
        c_spot = float(chain_payload.get("spot") or 0)
    except (TypeError, ValueError):
        c_spot = 0.0
    if float(nifty_spot or 0) <= 0 and c_spot > 0:
        nifty_spot = c_spot
        try:
            nifty_chg = float(chain_payload.get("spotChgPct") or 0)
        except (TypeError, ValueError):
            nifty_chg = 0.0
    return nifty_spot, nifty_chg


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


async def _fetch_nifty_market_and_chain(
    market_provider: Any | None,
    kite: Any | None = None,
) -> tuple[float, float, float | None, dict[str, Any] | None]:
    """Fetch NIFTY spot and compact chain payload once for landing widgets/sentiment."""
    nifty_spot = 0.0
    nifty_chg = 0.0
    chain_payload: dict[str, Any] | None = None
    pcr: float | None = None
    if market_provider:
        try:
            idx = await asyncio.wait_for(
                market_provider.indices(),
                timeout=_BROKER_TIMEOUT_SEC,
            )
            n = idx.get("NIFTY") or {}
            nifty_spot = float(n.get("spot") or 0)
            nifty_chg = float(n.get("spotChgPct") or 0)
        except Exception:
            pass
        try:
            ex, _src = await market_provider.expiries("NIFTY")
            exp_str = str(ex[0]) if ex else None
            if exp_str:
                chain_payload = await asyncio.wait_for(
                    market_provider.option_chain("NIFTY", exp_str, 3, 3, True),
                    timeout=_BROKER_TIMEOUT_SEC,
                )
                if chain_payload and chain_payload.get("pcr") is not None:
                    pcr = float(chain_payload.get("pcr"))
        except Exception:
            pass
    elif kite:
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
            exp_str, _ = _nifty_primary_expiry_str(kite)
            if exp_str:
                chain_payload = await asyncio.wait_for(
                    asyncio.to_thread(
                        fetch_option_chain_sync,
                        kite,
                        "NIFTY",
                        exp_str,
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
    nifty_spot, nifty_chg = _coalesce_nifty_from_chain(nifty_spot, nifty_chg, chain_payload)
    return nifty_spot, nifty_chg, pcr, chain_payload


async def _fetch_nifty_decision_bundle(
    market_provider: Any | None,
    kite: Any,
    *,
    no_broker_detail: str | None = None,
) -> tuple[float, float, float | None, dict[str, Any] | None, dict[str, Any]]:
    """
    Indices once, then **one** NIFTY option chain (ATM ± N strikes) for both sentiment/PCR and OI walls.

    We previously fetched narrow+wide **in parallel**, which doubled Kite traffic and often hit **429** or **8s timeouts**
    on the heavy wide build. Single fetch + longer timeout is kinder to the broker and more reliable.
    """
    nifty_spot, nifty_chg = 0.0, 0.0
    chain_narrow: dict[str, Any] | None = None
    pcr: float | None = None

    if market_provider:
        try:
            idx = await asyncio.wait_for(
                market_provider.indices(),
                timeout=_BROKER_TIMEOUT_SEC,
            )
            n = idx.get("NIFTY") or {}
            nifty_spot = float(n.get("spot") or 0)
            nifty_chg = float(n.get("spotChgPct") or 0)
        except Exception:
            pass
    elif kite:
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

    if not market_provider and not kite:
        return nifty_spot, nifty_chg, pcr, chain_narrow, oi_walls_stub(
            status="no_broker",
            detail=no_broker_detail
            or (
                "No broker market-data session for quotes. Connect broker under Settings → Brokers, "
                "or use the admin shared broker connection for paper."
            ),
            spot=nifty_spot,
        )

    if market_provider:
        try:
            ex, _src = await market_provider.expiries("NIFTY")
            expiry_str = str(ex[0]) if ex else None
        except Exception:
            expiry_str = None
    else:
        expiry_str, _ex_src = _nifty_primary_expiry_str(kite)
    if not expiry_str:
        return nifty_spot, nifty_chg, pcr, chain_narrow, oi_walls_stub(
            status="no_expiries",
            detail="NIFTY F&O expiries are missing. From backend: python -m app.scripts.bootstrap_nfo_cache",
            spot=nifty_spot,
        )

    async def _one_chain(half: int, timeout_sec: float) -> dict[str, Any]:
        if market_provider:
            return await asyncio.wait_for(
                market_provider.option_chain("NIFTY", expiry_str, half, half, True),
                timeout=timeout_sec,
            )
        return await asyncio.wait_for(
            asyncio.to_thread(
                fetch_option_chain_sync,
                kite,
                "NIFTY",
                expiry_str,
                half,
                half,
                3,
                None,
            ),
            timeout=timeout_sec,
        )

    payload: dict[str, Any] | None = None
    try:
        # Brief pause after index quote so we don’t burst Kite with back-to-back calls.
        await asyncio.sleep(0.25)
        payload = await _one_chain(_LANDING_CHAIN_STRIKES_HALF, _LANDING_CHAIN_TIMEOUT_SEC)
    except Exception:
        try:
            await asyncio.sleep(0.5)
            payload = await _one_chain(_LANDING_CHAIN_FALLBACK_HALF, _BROKER_TIMEOUT_SEC * 2.5)
        except Exception:
            return nifty_spot, nifty_chg, None, None, oi_walls_stub(
                status="chain_error",
                detail=(
                    "NIFTY option chain failed (rate limit, timeout, or market-data session error). "
                    "Wait 1–2 minutes, avoid hammering refresh, then retry. If it persists, "
                    "reconnect your broker under Settings → Brokers."
                ),
                spot=nifty_spot,
                expiry=expiry_str,
            )

    chain_narrow = payload
    if payload.get("pcr") is not None:
        pcr = float(payload.get("pcr"))
    nifty_spot, nifty_chg = _coalesce_nifty_from_chain(nifty_spot, nifty_chg, chain_narrow)
    spot = float(nifty_spot or payload.get("spot") or 0)
    oi_walls = build_oi_walls_from_chain(payload.get("chain") or [], spot, expiry_str)

    try:
        await asyncio.sleep(0.15)
        if market_provider:
            cs = await market_provider.index_candles("NIFTY", "5minute", 2)
            trail = []
            for c in cs if isinstance(cs, list) else []:
                tv = c.get("time")
                if isinstance(tv, str) and tv:
                    try:
                        ts = int(datetime.fromisoformat(tv.replace("Z", "+00:00")).timestamp() * 1000)
                    except Exception:
                        continue
                elif isinstance(tv, datetime):
                    ts = int(tv.timestamp() * 1000)
                else:
                    continue
                trail.append({"ts": ts, "spot": float(c.get("close") or 0)})
        else:
            trail = await asyncio.to_thread(fetch_nifty_spot_trail_5m_for_session_sync, kite, "NIFTY")
    except Exception:
        trail = []
    if isinstance(oi_walls, dict):
        oi_walls["spotTrail"] = trail if isinstance(trail, list) else []

    return nifty_spot, nifty_chg, pcr, chain_narrow, oi_walls


@router.get("/market-snapshot")
async def market_snapshot(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    await ensure_user(user_id)
    ctx = await resolve_broker_context(user_id, mode="PAPER")
    kite = await get_kite_for_quotes(user_id)
    nifty_spot, nifty_chg, pcr, _ = await _fetch_nifty_market_and_chain(ctx.market_data, kite)
    nifty_15m = await _fetch_nifty_15m_trend(ctx.market_data, kite)

    return {
        "nifty": {"spot": round(nifty_spot, 2), "changePct": round(nifty_chg, 2)},
        "nifty15m": {
            "changePct": nifty_15m.get("changePct"),
            "trendLabel": nifty_15m.get("trendLabel") or "—",
        },
        "pcr": round(pcr, 2) if pcr is not None else None,
        "sentimentLabel": _pcr_sentiment(pcr),
        "intradayTrendLabel": _spot_trend_label(nifty_chg),
    }


@router.get("/decision-snapshot")
async def decision_snapshot(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    """Phase-1 composite endpoint: market + sentiment + TrendPulse payload in one call."""
    await ensure_user(user_id)
    md_bundle = await get_market_data_session_bundle(user_id)
    ctx = await resolve_broker_context(user_id, mode="PAPER")
    provider = ctx.market_data
    kite = await get_kite_for_quotes(user_id)
    prev_rows, _, _ = await _load_sentiment_history_rows(user_id)
    prev_rec = prev_rows[-1] if prev_rows else None
    prev_ms = (prev_rec or {}).get("marketSnapshot") or {}
    prev_vix = prev_ms.get("vix")
    prev_inputs = ((prev_rec or {}).get("sentiment") or {}).get("inputs") or {}
    prev_ce_oi = prev_inputs.get("ceOi")
    prev_pe_oi = prev_inputs.get("peOi")

    bundle_timeout = max(4.0, min(20.0, float(os.getenv("LANDING_DECISION_BUNDLE_TIMEOUT_SEC", str(_LANDING_DECISION_BUNDLE_TIMEOUT_SEC)))))
    news_timeout = max(4.0, min(12.0, float(os.getenv("LANDING_NEWS_TIMEOUT_SEC", "8"))))
    tp_timeout = max(3.0, min(15.0, float(os.getenv("LANDING_TRENDPULSE_TIMEOUT_SEC", str(_LANDING_TRENDPULSE_TIMEOUT_SEC)))))
    regime_timeout = max(2.0, min(12.0, float(os.getenv("LANDING_REGIME_CANDLES_TIMEOUT_SEC", str(_LANDING_REGIME_CANDLES_TIMEOUT_SEC)))))
    total_budget = max(
        8.0,
        min(
            45.0,
            float(
                os.getenv(
                    "LANDING_DECISION_TOTAL_BUDGET_SEC",
                    "28",
                )
            ),
        ),
    )

    async def _shield_wait(task: asyncio.Task[Any], cap: float) -> Any:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=float(cap))
        except Exception as exc:
            if not task.done():
                task.cancel()
            return exc

    bundle_task = asyncio.create_task(
        _fetch_nifty_decision_bundle(
            provider,
            kite,
            no_broker_detail=(md_bundle["session_hint"] if not provider else None),
        )
    )
    tp_task = asyncio.create_task(trendpulse_series(user_id))
    news_task = asyncio.create_task(compute_news_sentiment_snapshot())
    regime_task = (
        asyncio.create_task(provider.index_candles("NIFTY", "30minute", 12))
        if provider
        else asyncio.create_task(_async_empty_list())
    )
    nifty_15m_task = (
        asyncio.create_task(_fetch_nifty_15m_trend(provider, kite))
        if (provider or kite)
        else asyncio.create_task(_empty_nifty_15m_trend())
    )

    try:
        bundle_raw, tp, news_raw, regime_candles_raw, nifty_15m_raw = await asyncio.wait_for(
            asyncio.gather(
                _shield_wait(bundle_task, bundle_timeout),
                _shield_wait(tp_task, tp_timeout),
                _shield_wait(news_task, news_timeout),
                _shield_wait(regime_task, regime_timeout),
                _shield_wait(nifty_15m_task, 8.0),
            ),
            timeout=total_budget,
        )
    except asyncio.TimeoutError:
        for t in (bundle_task, tp_task, news_task, regime_task, nifty_15m_task):
            if not t.done():
                t.cancel()
        bundle_raw = TimeoutError("decision_snapshot total budget")
        tp = news_raw = regime_candles_raw = nifty_15m_raw = bundle_raw

    if (
        isinstance(bundle_raw, tuple)
        and len(bundle_raw) == 5
    ):
        nifty_spot, nifty_chg, pcr, chain_payload, oi_walls = bundle_raw
    else:
        # Keep endpoint responsive under broker slowness; return partial snapshot.
        nifty_spot, nifty_chg, pcr, chain_payload = 0.0, 0.0, None, None
        oi_walls = oi_walls_stub(
            status="chain_timeout",
            detail="Decision bundle timed out while fetching broker chain data.",
            spot=0.0,
        )
    if isinstance(tp, BaseException):
        tp = {
            "strategyId": None,
            "strategyVersion": None,
            "strategyType": "rule-based",
            "trendpulseEnabled": False,
            "series": None,
            "htfBias": None,
            "message": "TrendPulse snapshot timed out; retry in a few seconds.",
        }
    news_sentiment = (
        news_sentiment_failure_payload(news_raw)
        if isinstance(news_raw, BaseException)
        else news_raw
    )
    regime_candles: list[Any] = []
    if isinstance(regime_candles_raw, list):
        regime_candles = regime_candles_raw
    nifty_15m_block: dict[str, Any] = {"changePct": None, "trendLabel": "—"}
    if isinstance(nifty_15m_raw, dict):
        nifty_15m_block = nifty_15m_raw
    sentiment = compute_sentiment_snapshot(
        chain_payload=chain_payload,
        spot_chg_pct=nifty_chg,
        trendpulse_signal=tp.get("tradeSignal") if isinstance(tp, dict) else None,
    )
    vix_val: float | None = None
    if chain_payload and chain_payload.get("vix") is not None:
        try:
            vix_val = float(chain_payload.get("vix"))
        except (TypeError, ValueError):
            vix_val = None
    vix_prev_f: float | None = None
    if prev_vix is not None:
        try:
            vix_prev_f = float(prev_vix)
        except (TypeError, ValueError):
            vix_prev_f = None
    spot_for_regime = float(chain_payload.get("spot") or nifty_spot or 0) if chain_payload else float(nifty_spot or 0)
    prev_ce_f = float(prev_ce_oi) if prev_ce_oi is not None else None
    prev_pe_f = float(prev_pe_oi) if prev_pe_oi is not None else None
    sideways_regime = compute_sideways_regime_snapshot(
        candles=regime_candles,
        spot=spot_for_regime,
        sentiment=sentiment,
        vix=vix_val,
        vix_prev=vix_prev_f,
        ce_oi_prev=prev_ce_f,
        pe_oi_prev=prev_pe_f,
    )
    market = {
        "nifty": {"spot": round(nifty_spot, 2), "changePct": round(nifty_chg, 2)},
        "nifty15m": {
            "changePct": nifty_15m_block.get("changePct"),
            "trendLabel": nifty_15m_block.get("trendLabel") or "—",
        },
        "pcr": round(pcr, 2) if pcr is not None else None,
        "sentimentLabel": sentiment.get("sentimentLabel") or _pcr_sentiment(pcr),
        "intradayTrendLabel": _spot_trend_label(nifty_chg),
        "vix": round(vix_val, 2) if vix_val is not None else None,
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
        "oiWalls": oi_walls,
        "sidewaysRegime": sideways_regime,
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

    ctx = await resolve_broker_context(user_id, mode="PAPER")
    provider = ctx.market_data
    kite = await get_kite_for_quotes(user_id)
    if not provider:
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
            provider.index_candles("NIFTY", st_int, days),
            timeout=_BROKER_TIMEOUT_SEC,
        )
        htf_candles = await asyncio.wait_for(
            provider.index_candles("NIFTY", htf_int, days),
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
            _fetch_nifty_market_and_chain(provider, kite),
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
