from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from kiteconnect import KiteConnect
from pydantic import ValidationError

from app.db_client import ensure_user, execute, fetch, fetchrow
from app.api.auth_context import get_user_id
from app.services.ist_calendar import ist_today
from app.services.ist_time_sql import IST_TODAY, closed_at_ist_date, closed_at_ist_date_bare
from app.api.schemas import ExecuteRequest, ExecuteResponse, RecommendationOut, TradeOut
from app.services.trades_service import (
    ensure_recommendations,
    execute_recommendation,
    filter_recommendations_short_delta_band_only,
    get_kite_for_quotes,
    get_strategy_score_params,
    list_recommendations_for_user,
)

router = APIRouter(prefix="/trades", tags=["trades"])
logger = logging.getLogger(__name__)


def _coerce_recommendation_row(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize DB + details_json shapes so RecommendationOut validation does not 500 the Trades page."""
    out = dict(item)

    def to_float(k: str) -> None:
        v = out.get(k)
        if v is None:
            return
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            out[k] = float(v)
        elif isinstance(v, str) and v.strip():
            try:
                out[k] = float(v)
            except ValueError:
                pass

    for k in (
        "entry_price",
        "target_price",
        "stop_loss_price",
        "confidence_score",
        "vwap",
        "ema9",
        "ema21",
        "rsi",
        "ivr",
        "volume",
        "avg_volume",
        "volume_spike_ratio",
        "score_max",
        "spot_price",
        "delta",
        "gamma",
    ):
        to_float(k)
    sc = out.get("score")
    if sc is not None and not isinstance(sc, (int, float)):
        try:
            out["score"] = float(sc)
        except (TypeError, ValueError):
            out["score"] = None
    rv = out.get("rank_value")
    if rv is not None:
        try:
            out["rank_value"] = int(rv)
        except (TypeError, ValueError):
            out["rank_value"] = 0
    oi_v = out.get("oi")
    if oi_v is not None:
        try:
            out["oi"] = int(float(oi_v))
        except (TypeError, ValueError):
            out["oi"] = None
    atm = out.get("atm_distance")
    if atm is not None:
        try:
            out["atm_distance"] = int(atm)
        except (TypeError, ValueError):
            out["atm_distance"] = None
    ris = out.get("refresh_interval_sec")
    if ris is not None:
        try:
            out["refresh_interval_sec"] = int(ris)
        except (TypeError, ValueError):
            out["refresh_interval_sec"] = None
    hr = out.get("heuristic_reasons")
    if hr is None:
        pass
    elif isinstance(hr, str):
        out["heuristic_reasons"] = [hr] if hr.strip() else None
    elif isinstance(hr, list):
        out["heuristic_reasons"] = [str(x) for x in hr]
    else:
        out["heuristic_reasons"] = None
    tp = out.get("trendpulse")
    if tp is not None and not isinstance(tp, dict):
        out["trendpulse"] = None
    return out


def _coerce_trade_row(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize asyncpg / legacy rows so TradeOut validation does not 500 Open/Closed lists."""
    out = dict(item)

    def to_float(k: str) -> None:
        v = out.get(k)
        if v is None:
            return
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, bool):
            return
        elif isinstance(v, (int, float)):
            out[k] = float(v)
        elif isinstance(v, str) and v.strip():
            try:
                out[k] = float(v)
            except ValueError:
                pass

    for k in (
        "entry_price",
        "current_price",
        "target_price",
        "stop_loss_price",
        "unrealized_pnl",
        "realized_pnl",
        "confidence_score",
        "score",
    ):
        to_float(k)
    q = out.get("quantity")
    if q is not None:
        try:
            out["quantity"] = int(q)
        except (TypeError, ValueError):
            out["quantity"] = 1
    return out


@router.get("/strategy-params")
async def get_strategy_params_debug(
    user_id: int = Depends(get_user_id),
) -> dict:
    """Debug: show effective strategy params (scoreThreshold, RSI, volume, ADX, etc.) for current user. Use to verify JSON changes are applied."""
    from app.services.trades_service import get_strategy_score_params, _get_user_strategy
    try:
        strategy_id, strategy_version = await _get_user_strategy(user_id)
        params = await get_strategy_score_params(strategy_id, strategy_version, user_id)
        return {
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "params": {
                "strategy_type": params.get("strategy_type"),
                "score_threshold": params.get("score_threshold"),
                "score_max": params.get("score_max"),
                "auto_trade_score_threshold": params.get("auto_trade_score_threshold"),
                "rsi_min": params.get("rsi_min"),
                "rsi_max": params.get("rsi_max"),
                "volume_min_ratio": params.get("volume_min_ratio"),
                "ema_crossover_max_candles": params.get("ema_crossover_max_candles"),
                "adx_period": params.get("adx_period"),
                "adx_min_threshold": params.get("adx_min_threshold"),
                "heuristics": params.get("heuristics"),
            },
        }
    except Exception as e:
        return {"error": str(e)}


def _symbol_to_kite_nfo(symbol: str) -> str:
    """Convert stored symbol to Zerodha NFO quote format. Ours (NIFTY2631723250CE) matches Zerodha weekly YYMDD."""
    s = str(symbol or "").replace(" ", "").upper()
    return f"NFO:{s}" if s else "NFO:"


async def _get_kite_client_or_none(user_id: int) -> KiteConnect | None:
    row = await fetchrow(
        """
        SELECT credentials_json FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    cred = row["credentials_json"] if row else None
    if isinstance(cred, str):
        try:
            cred = json.loads(cred)
        except json.JSONDecodeError:
            cred = {}
    if not isinstance(cred, dict):
        cred = {}
    api_key = str(cred.get("apiKey", "")).strip()
    access_token = str(cred.get("accessToken", "")).strip()
    if not api_key or not access_token:
        return None
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


@router.get("/recommendations")
async def get_recommendations(
    user_id: int = Depends(get_user_id),
    status: str = Query(default="GENERATED"),
    min_confidence: float = Query(default=0.0, ge=0.0, le=100.0),
    sort_by: str = Query(default="rank"),
    sort_dir: str = Query(default="asc"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    eligible_only: bool = Query(default=False, description="Return only strikes eligible for auto-trade (score, signal_eligible)"),
    all_strategies: bool = Query(default=False, description="Admin only: show recommendations from all strategies (Trades screen). Omit for subscribed strategy only (STRATEGY SIGNALS on Dashboard)."),
    short_delta_band_only: bool = Query(
        default=True,
        description="For short_premium strategies, keep only rows whose delta is inside the active short delta gate (VIX bands or fallback). Set false to show all stored rows.",
    ),
) -> list[RecommendationOut]:
    await ensure_user(user_id)
    kite = await get_kite_for_quotes(user_id)  # Shared API for recommendations
    try:
        await ensure_recommendations(user_id, kite)
    except Exception:
        # Admin "all strategies" refresh can fail on one strategy, broker, or schema drift; still return DB rows.
        logger.exception("ensure_recommendations failed user_id=%s", user_id)

    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"
    use_all_strategies = is_admin and all_strategies

    if eligible_only:
        rows = await list_recommendations_for_user(
            user_id=user_id,
            status=status.upper(),
            min_confidence=min_confidence,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=min(limit * 2, 100),
            offset=0,
            all_strategies=use_all_strategies,
        )
        rows = [r for r in rows if r.get("signal_eligible") is True][:limit]
    else:
        rows = await list_recommendations_for_user(
            user_id=user_id,
            status=status.upper(),
            min_confidence=min_confidence,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
            all_strategies=use_all_strategies,
        )
    if short_delta_band_only:
        rows = await filter_recommendations_short_delta_band_only(user_id, kite, rows)
    score_max: int | None = None
    if rows:
        sid = rows[0].get("strategy_id")
        ver = rows[0].get("strategy_version")
        if sid and ver:
            try:
                params = await get_strategy_score_params(str(sid), str(ver), user_id)
                score_max = params.get("score_max")
            except Exception:
                logger.exception(
                    "get_strategy_score_params failed for recommendations sid=%s ver=%s",
                    sid,
                    ver,
                )
    out: list[RecommendationOut] = []
    for r in rows:
        item = _coerce_recommendation_row(dict(r))
        if score_max is not None:
            item["score_max"] = score_max
        try:
            out.append(RecommendationOut.model_validate(item))
        except Exception:
            logger.warning(
                "Skipping recommendation row that failed validation recommendation_id=%s",
                item.get("recommendation_id"),
                exc_info=True,
            )
    return out


@router.post("/execute")
async def execute_trade(
    payload: ExecuteRequest,
    user_id: int = Depends(get_user_id),
) -> ExecuteResponse:
    await ensure_user(user_id)
    try:
        result = await execute_recommendation(
            user_id,
            payload.recommendation_id,
            payload.mode.upper(),
            quantity=payload.quantity,
            manual=True,
        )
        return ExecuteResponse(status="ok", trade_ref=result["trade_ref"], order_ref=result["order_ref"])
    except ValueError as e:
        if "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail="Recommendation not found.")
        if "already processed" in str(e).lower():
            raise HTTPException(status_code=400, detail="Recommendation already processed.")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        err_msg = str(e)
        if "broker_order_id" in err_msg or "does not exist" in err_msg:
            raise HTTPException(
                status_code=500,
                detail="Database schema outdated. Run: python run_broker_order_id_migration.py",
            )
        raise HTTPException(status_code=500, detail=err_msg or "Order execution failed.")


async def _get_user_strategy_params(user_id: int) -> dict:
    row = await fetchrow(
        """
        SELECT lot_size, sl_points, target_points FROM s004_user_strategy_settings
        WHERE user_id = $1
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if not row:
        return {"lot_size": 65, "sl_points": 15.0, "target_points": 10.0}
    return {
        "lot_size": max(1, int(row.get("lot_size") or 65)),
        "sl_points": float(row.get("sl_points") or 15),
        "target_points": float(row.get("target_points") or 10),
    }


async def _get_user_lot_size(user_id: int) -> int:
    p = await _get_user_strategy_params(user_id)
    return p["lot_size"]


async def _get_lot_size_by_user_ids(user_ids: list[int]) -> dict[int, int]:
    """Return {user_id: lot_size} for given users. Defaults to 65 if not found."""
    if not user_ids:
        return {}
    placeholders = ",".join(f"${i + 1}" for i in range(len(user_ids)))
    rows = await fetch(
        f"""
        SELECT DISTINCT ON (user_id) user_id, lot_size
        FROM s004_user_strategy_settings
        WHERE user_id IN ({placeholders})
        ORDER BY user_id, updated_at DESC
        """,
        *user_ids,
    )
    return {int(r["user_id"]): max(1, int(r.get("lot_size") or 65)) for r in rows or []}


@router.get("/open")
async def get_open_trades(user_id: int = Depends(get_user_id)) -> list[TradeOut]:
    rows = await fetch(
        """
        SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
               t.target_price, t.stop_loss_price, t.unrealized_pnl, t.opened_at, t.updated_at,
               o.manual_execute, r.score, r.confidence_score,
               COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name
        FROM s004_live_trades t
        LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
        LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = o.recommendation_id
        LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
        WHERE t.user_id = $1 AND t.current_state <> 'EXIT'
        ORDER BY t.updated_at DESC
        """,
        user_id,
    )
    params = await _get_user_strategy_params(user_id)
    lot_size = params["lot_size"]
    sl_pts = params["sl_points"]
    tgt_pts = params["target_points"]
    out: list[TradeOut] = []
    kite = await get_kite_for_quotes(user_id)
    if kite and rows:
        symbols = list(dict.fromkeys(_symbol_to_kite_nfo(r["symbol"]) for r in rows))
        try:
            q = await asyncio.to_thread(kite.quote, symbols)
            data = q.get("data", q) if isinstance(q, dict) else {}
            quotes = data if isinstance(data, dict) else {}
        except Exception:
            quotes = {}
        for r in rows:
            d = dict(r)
            nfo = _symbol_to_kite_nfo(r["symbol"])
            entry = float(r.get("entry_price") or 0.0)
            lots = int(r.get("quantity") or 1)
            side = str(r.get("side") or "BUY").upper()
            target_price = float(r.get("target_price") or entry + 10)
            stop_loss_price = float(r.get("stop_loss_price") or entry - 15)
            raw = quotes.get(nfo) if isinstance(quotes.get(nfo), dict) else {}
            ltp = float(raw.get("last_price") or 0.0)
            contracts = lots * lot_size
            if ltp > 0:
                d["current_price"] = round(ltp, 2)
                if side == "BUY":
                    d["unrealized_pnl"] = round((ltp - entry) * contracts, 2)
                    hit_target = ltp >= target_price
                    hit_sl = ltp <= stop_loss_price
                else:
                    d["unrealized_pnl"] = round((entry - ltp) * contracts, 2)
                    hit_target = ltp <= target_price
                    hit_sl = ltp >= stop_loss_price
                if hit_target or hit_sl:
                    if r.get("mode") == "PAPER":
                        exit_price = round(ltp, 2)
                        reason = "TARGET_HIT" if hit_target else "SL_HIT"
                        pnl = round((exit_price - entry) * contracts, 2) if side == "BUY" else round((entry - exit_price) * contracts, 2)
                        await execute(
                            """
                            UPDATE s004_live_trades
                            SET current_state = 'EXIT', current_price = $1, realized_pnl = $2,
                                unrealized_pnl = 0, closed_at = NOW(), updated_at = NOW()
                            WHERE trade_ref = $3 AND user_id = $4 AND current_state <> 'EXIT'
                            """,
                            exit_price,
                            pnl,
                            r["trade_ref"],
                            user_id,
                        )
                        await execute(
                            """
                            INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
                            VALUES ($1,'AUTO_EXIT','ACTIVE','EXIT',$2,$3::jsonb,NOW())
                            """,
                            r["trade_ref"],
                            reason,
                            json.dumps({"exit_price": exit_price, "pnl": pnl}),
                        )
                        continue
                    # LIVE: position monitor handles exit orders; fall through to display current state
            else:
                d["current_price"] = round(entry, 2)
                d["unrealized_pnl"] = 0.0
            if d.get("target_price") is None:
                d["target_price"] = round(entry + tgt_pts, 2) if side == "BUY" else round(entry - tgt_pts, 2)
            if d.get("stop_loss_price") is None:
                d["stop_loss_price"] = round(entry - sl_pts, 2) if side == "BUY" else round(entry + sl_pts, 2)
            d["qty"] = contracts  # LotSize × quantity
            try:
                out.append(TradeOut.model_validate(_coerce_trade_row(d)))
            except ValidationError:
                logger.warning(
                    "Skipping open trade row (validation) trade_ref=%s",
                    d.get("trade_ref"),
                    exc_info=True,
                )
    else:
        for r in rows:
            lots = int(r.get("quantity") or 1)
            contracts = lots * lot_size
            entry = float(r.get("entry_price") or 0.0)
            curr = float(r.get("current_price") or entry)
            side = str(r.get("side") or "BUY").upper()
            d = dict(r)
            if side == "BUY":
                d["unrealized_pnl"] = round((curr - entry) * contracts, 2)
            else:
                d["unrealized_pnl"] = round((entry - curr) * contracts, 2)
            if d.get("target_price") is None:
                d["target_price"] = round(entry + tgt_pts, 2) if side == "BUY" else round(entry - tgt_pts, 2)
            if d.get("stop_loss_price") is None:
                d["stop_loss_price"] = round(entry - sl_pts, 2) if side == "BUY" else round(entry + sl_pts, 2)
            d["qty"] = contracts  # LotSize × quantity
            try:
                out.append(TradeOut.model_validate(_coerce_trade_row(d)))
            except ValidationError:
                logger.warning(
                    "Skipping open trade row (validation) trade_ref=%s",
                    d.get("trade_ref"),
                    exc_info=True,
                )
    return out


@router.get("/closed")
async def get_closed_trades(user_id: int = Depends(get_user_id)) -> list[TradeOut]:
    from app.api.schemas import _format_exit_reason

    rows = await fetch(
        """
        SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
               t.target_price, t.stop_loss_price, t.realized_pnl, t.opened_at, t.closed_at, t.updated_at,
               t.current_state, o.manual_execute, r.score, r.confidence_score,
               COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
               (SELECT e.reason_code FROM s004_trade_events e
                WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
        FROM s004_live_trades t
        LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
        LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = o.recommendation_id
        LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
        WHERE t.user_id = $1 AND t.current_state = 'EXIT'
        ORDER BY t.updated_at DESC
        """,
        user_id,
    )
    params = await _get_user_strategy_params(user_id)
    lot_size = params["lot_size"]
    out = []
    for r in rows:
        d = dict(r)
        d["reason"] = _format_exit_reason(d.pop("exit_reason_code", None))
        lots = int(d.get("quantity") or 1)
        d["qty"] = lots * lot_size
        try:
            out.append(TradeOut.model_validate(_coerce_trade_row(d)))
        except ValidationError:
            logger.warning(
                "Skipping closed trade row (validation) trade_ref=%s",
                d.get("trade_ref"),
                exc_info=True,
            )
    return out


@router.get("/history")
async def get_trade_history(
    user_id: int = Depends(get_user_id),
    today_only: bool = Query(default=False, description="Return only today's closed trades for TODAY'S CLOSED TRADES section"),
) -> list[TradeOut]:
    from app.api.schemas import _format_exit_reason

    if today_only:
        rows = await fetch(
            f"""
            SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
                   t.target_price, t.stop_loss_price, t.current_state, t.realized_pnl, t.unrealized_pnl,
                   t.opened_at, t.closed_at, t.updated_at, o.manual_execute, r.score, r.confidence_score,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
                   (SELECT e.reason_code FROM s004_trade_events e
                    WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                    ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
            FROM s004_live_trades t
            LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
            LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = o.recommendation_id
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.user_id = $1
              AND t.current_state = 'EXIT'
              AND t.closed_at IS NOT NULL
              AND {closed_at_ist_date("t")} = {IST_TODAY}
            ORDER BY t.closed_at DESC
            LIMIT 500
            """,
            user_id,
        )
    else:
        rows = await fetch(
            """
            SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
                   t.target_price, t.stop_loss_price, t.current_state, t.realized_pnl, t.unrealized_pnl,
                   t.opened_at, t.closed_at, t.updated_at, o.manual_execute, r.score, r.confidence_score,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
                   (SELECT e.reason_code FROM s004_trade_events e
                    WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                    ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
            FROM s004_live_trades t
            LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
            LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = o.recommendation_id
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.user_id = $1
            ORDER BY t.updated_at DESC
            LIMIT 100
            """,
            user_id,
        )
    params = await _get_user_strategy_params(user_id)
    lot_size = params["lot_size"]
    out = []
    for r in rows:
        d = dict(r)
        exit_code = d.pop("exit_reason_code", None)
        d["reason"] = _format_exit_reason(exit_code) if d.get("current_state") == "EXIT" else None
        lots = int(d.get("quantity") or 1)
        d["qty"] = lots * lot_size
        try:
            out.append(TradeOut.model_validate(_coerce_trade_row(d)))
        except ValidationError:
            logger.warning(
                "Skipping history trade row (validation) trade_ref=%s",
                d.get("trade_ref"),
                exc_info=True,
            )
    return out


def _build_reports_filter_sql(
    *,
    is_admin: bool,
    user_id: int,
    from_d: date | None,
    to_d: date | None,
    mode_upper: str,
    strategy_id: str | None,
    strategy_version: str | None,
    filter_user_id: int | None,
    taken_upper: str,
) -> tuple[str, list]:
    """Returns WHERE fragment (without WHERE keyword) and args list for parameterized query."""
    parts = ["t.current_state = 'EXIT'", "t.closed_at IS NOT NULL"]
    args: list = []
    idx = 1

    c_ist = closed_at_ist_date("t")
    if from_d is not None:
        parts.append(f"{c_ist} >= ${idx}")
        args.append(from_d)
        idx += 1
    if to_d is not None:
        parts.append(f"{c_ist} <= ${idx}")
        args.append(to_d)
        idx += 1

    if mode_upper == "PAPER":
        parts.append("t.mode = 'PAPER'")
    elif mode_upper == "LIVE":
        parts.append("t.mode = 'LIVE'")

    if strategy_id and strategy_id.strip():
        parts.append(f"t.strategy_id = ${idx}")
        args.append(strategy_id.strip())
        idx += 1
    if strategy_version and strategy_version.strip():
        parts.append(f"t.strategy_version = ${idx}")
        args.append(strategy_version.strip())
        idx += 1

    if not is_admin:
        parts.append(f"t.user_id = ${idx}")
        args.append(user_id)
        idx += 1
    elif filter_user_id is not None and filter_user_id > 0:
        parts.append(f"t.user_id = ${idx}")
        args.append(filter_user_id)
        idx += 1

    if taken_upper == "AUTO":
        parts.append("o.manual_execute = false")
    elif taken_upper == "MANUAL":
        parts.append("o.manual_execute = true")

    return " AND ".join(parts), args


@router.get("/reports/strategies")
async def get_report_strategy_options(user_id: int = Depends(get_user_id)) -> dict:
    """Distinct strategies from closed trades (scoped: admin = all, user = own). For Reports filter dropdown."""
    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    if is_admin:
        rows = await fetch(
            """
            SELECT DISTINCT t.strategy_id, t.strategy_version,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS display_name
            FROM s004_live_trades t
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
            ORDER BY display_name, t.strategy_id, t.strategy_version
            """
        )
    else:
        rows = await fetch(
            """
            SELECT DISTINCT t.strategy_id, t.strategy_version,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS display_name
            FROM s004_live_trades t
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.user_id = $1 AND t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
            ORDER BY display_name, t.strategy_id, t.strategy_version
            """,
            user_id,
        )
    return {
        "strategies": [
            {
                "strategy_id": str(r["strategy_id"]),
                "strategy_version": str(r["strategy_version"]),
                "display_name": str(r["display_name"] or r["strategy_id"]),
            }
            for r in (rows or [])
        ]
    }


@router.get("/reports")
async def get_trade_reports(
    user_id: int = Depends(get_user_id),
    from_date: str | None = Query(None, description="Closed on/after this date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="Closed on/before this date (YYYY-MM-DD)"),
    mode: str = Query("BOTH", description="PAPER, LIVE, or BOTH"),
    strategy_id: str | None = Query(None, description="Filter by strategy_id"),
    strategy_version: str | None = Query(None, description="Filter by strategy_version (optional)"),
    filter_user_id: int | None = Query(None, alias="userId", description="Admin only: filter by user"),
    taken_by: str = Query("ALL", description="ALL, AUTO, or MANUAL"),
) -> list[dict]:
    """Return closed trades for Reports. Admin sees all users' trades unless userId set. Filterable by date, mode, strategy, taken-by."""
    from app.api.schemas import _format_exit_reason

    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    if not is_admin and filter_user_id is not None and filter_user_id != user_id:
        filter_user_id = None

    from_d: date | None = None
    to_d: date | None = None
    if from_date:
        try:
            from_d = date.fromisoformat(from_date.strip())
        except ValueError:
            pass
    if to_date:
        try:
            to_d = date.fromisoformat(to_date.strip())
        except ValueError:
            pass
    if from_d and to_d and from_d > to_d:
        from_d, to_d = to_d, from_d

    mode_upper = (mode or "BOTH").upper()
    if mode_upper not in ("PAPER", "LIVE", "BOTH"):
        mode_upper = "BOTH"
    taken_upper = (taken_by or "ALL").upper()
    if taken_upper not in ("ALL", "AUTO", "MANUAL"):
        taken_upper = "ALL"

    has_filters = bool(
        from_d
        or to_d
        or mode_upper != "BOTH"
        or (strategy_id and strategy_id.strip())
        or (strategy_version and strategy_version.strip())
        or (is_admin and filter_user_id is not None and filter_user_id > 0)
        or taken_upper != "ALL"
    )
    limit_n = 2000 if has_filters else 500

    where_sql, filter_args = _build_reports_filter_sql(
        is_admin=is_admin,
        user_id=user_id,
        from_d=from_d,
        to_d=to_d,
        mode_upper=mode_upper,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        filter_user_id=filter_user_id,
        taken_upper=taken_upper,
    )

    base_select_admin = """
            SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
                   t.target_price, t.stop_loss_price, t.current_state, t.realized_pnl, t.unrealized_pnl,
                   t.opened_at, t.closed_at, t.updated_at, t.user_id, o.manual_execute,
                   COALESCE(u.username, 'user#' || t.user_id) AS username,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
                   (SELECT e.reason_code FROM s004_trade_events e
                    WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                    ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
            FROM s004_live_trades t
            LEFT JOIN s004_users u ON u.id = t.user_id
            LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE """ + where_sql + """
            ORDER BY t.closed_at DESC
            LIMIT """ + str(
        limit_n
    )

    base_select_user = """
            SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
                   t.target_price, t.stop_loss_price, t.current_state, t.realized_pnl, t.unrealized_pnl,
                   t.opened_at, t.closed_at, t.updated_at, t.user_id, o.manual_execute,
                   COALESCE(u.username, 'user#' || t.user_id) AS username,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
                   (SELECT e.reason_code FROM s004_trade_events e
                    WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                    ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
            FROM s004_live_trades t
            LEFT JOIN s004_users u ON u.id = t.user_id
            LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE """ + where_sql + """
            ORDER BY t.closed_at DESC
            LIMIT """ + str(
        limit_n
    )

    if is_admin:
        rows = await fetch(base_select_admin, *filter_args)
    else:
        rows = await fetch(base_select_user, *filter_args)

    user_ids = list(dict.fromkeys(int(r["user_id"]) for r in rows)) if is_admin else [user_id]
    lot_sizes = await _get_lot_size_by_user_ids(user_ids) if user_ids else {}
    default_lot = (await _get_user_strategy_params(user_id))["lot_size"] if user_ids else 65

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        exit_code = d.pop("exit_reason_code", None)
        d["reason"] = _format_exit_reason(exit_code)
        uid = int(d.get("user_id", user_id))
        lot_size = lot_sizes.get(uid, default_lot)
        d["qty"] = int(d.get("quantity") or 1) * lot_size
        out.append(d)
    return out


def _est_charges_per_trade(
    realized: float,
    entry: float,
    exit_price: float,
    contracts: int,
    charges_per_trade: float,
) -> float:
    """Estimate charges for one closed trade: brokerage (2 orders) + STT + GST + exchange."""
    brokerage = 2 * charges_per_trade
    turnover = (entry + exit_price) * contracts
    stt = round(turnover * 0.001, 2)
    gst = round(brokerage * 0.18, 2)
    exchange = round(turnover * (0.00035 + 0.00001 + 0.00003) / 100, 2)
    return brokerage + stt + gst + exchange


@router.get("/performance-analytics")
async def get_performance_analytics(
    user_id: int = Depends(get_user_id),
    from_date: str | None = Query(None, description="Start date YYYY-MM-DD"),
    to_date: str | None = Query(None, description="End date YYYY-MM-DD"),
    filter_user_id: int | None = Query(None, alias="userId", description="Filter by user (admin only); omit for all"),
    mode: str = Query("BOTH", description="PAPER, LIVE, or BOTH"),
) -> dict:
    """Return daily P&L and per-user P&L for Performance Analytics. Net P&L (after charges)."""
    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    to_d = ist_today()
    from_d = to_d - timedelta(days=89)
    if from_date:
        try:
            from_d = date.fromisoformat(from_date.strip())
        except ValueError:
            pass
    if to_date:
        try:
            to_d = date.fromisoformat(to_date.strip())
        except ValueError:
            pass
    if from_d > to_d:
        from_d, to_d = to_d, from_d

    mode_upper = (mode or "BOTH").upper()
    mode_filter = ""
    mode_args = []
    if mode_upper == "PAPER":
        mode_filter = " AND t.mode = 'PAPER'"
    elif mode_upper == "LIVE":
        mode_filter = " AND t.mode = 'LIVE'"

    user_filter = ""
    user_args: list = []
    if not is_admin:
        user_filter = " AND t.user_id = $3"
        user_args = [user_id]
    elif is_admin and filter_user_id is not None and filter_user_id > 0:
        user_filter = " AND t.user_id = $3"
        user_args = [filter_user_id]

    cdate = closed_at_ist_date("t")
    query_trades = f"""
        SELECT t.user_id, {cdate} AS trade_date, t.realized_pnl,
               t.entry_price, t.current_price, t.quantity
        FROM s004_live_trades t
        WHERE t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
          AND {cdate} >= $1 AND {cdate} <= $2
          {user_filter}
          {mode_filter}
        ORDER BY t.closed_at
    """
    all_args: list = [from_d, to_d] + user_args
    rows = await fetch(query_trades, *all_args)

    user_ids_all = list(set(int(r["user_id"]) for r in rows))
    username_map: dict[int, str] = {}
    if user_ids_all:
        ph = ",".join(f"${i+1}" for i in range(len(user_ids_all)))
        urows = await fetch(
            f"SELECT id, username FROM s004_users WHERE id IN ({ph})",
            *user_ids_all,
        )
        if urows:
            for ur in urows:
                username_map[int(ur["id"])] = str(ur.get("username") or f"user{ur['id']}")

    user_lot_charges: dict[int, tuple[int, float]] = {}
    for uid in user_ids_all:
        lot_row = await fetchrow(
            "SELECT COALESCE(lot_size, 65) AS lot_size FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1",
            uid,
        )
        chg_row = await fetchrow(
            "SELECT COALESCE(charges_per_trade, 20) AS charges_per_trade FROM s004_user_master_settings WHERE user_id = $1",
            uid,
        )
        lot = int(lot_row["lot_size"] or 65) if lot_row else 65
        chg = float(chg_row["charges_per_trade"] or 20) if chg_row else 20.0
        user_lot_charges[uid] = (lot, chg)

    daily_net: dict[str, float] = {}
    user_net: dict[int, float] = {}
    by_date_user: dict[tuple[str, int], tuple[float, float]] = {}  # (date, uid) -> (charges_sum, pnl_sum)

    for r in rows:
        uid = int(r["user_id"])
        dt = str(r["trade_date"])
        gross = float(r["realized_pnl"] or 0)
        entry = float(r["entry_price"] or 0)
        exit_p = float(r["current_price"] or r["entry_price"] or 0)
        qty = int(r["quantity"] or 1)
        lot, chg = user_lot_charges.get(uid, (65, 20.0))
        contracts = qty * lot
        est_ch = _est_charges_per_trade(gross, entry, exit_p, contracts, chg)
        net = gross - est_ch

        daily_net[dt] = daily_net.get(dt, 0) + net
        user_net[uid] = user_net.get(uid, 0) + net
        key = (dt, uid)
        prev_ch, prev_pnl = by_date_user.get(key, (0.0, 0.0))
        by_date_user[key] = (prev_ch + est_ch, prev_pnl + net)

    daily_list = [{"date": d, "pnl": round(v, 2)} for d, v in sorted(daily_net.items())]

    user_ids_sorted = sorted(user_net.keys(), key=lambda u: user_net.get(u, 0), reverse=True)
    user_list = [
        {"userId": uid, "username": username_map.get(uid, f"user{uid}"), "pnl": round(user_net.get(uid, 0), 2)}
        for uid in user_ids_sorted
    ]

    trade_rows = [
        {
            "trade_date": dt,
            "userId": uid,
            "username": username_map.get(uid, f"user{uid}"),
            "charges": round(ch_pnl[0], 2),
            "pnl": round(ch_pnl[1], 2),
        }
        for (dt, uid), ch_pnl in sorted(by_date_user.items(), key=lambda x: (x[0][0], x[0][1]))
    ]

    return {"dailyPnl": daily_list, "userPnl": user_list, "tradeRows": trade_rows}


def _sp_index_from_symbol(sym: str) -> str:
    u = sym.upper()
    if "BANKNIFTY" in u:
        return "BANKNIFTY"
    if "FINNIFTY" in u:
        return "FINNIFTY"
    if "MIDCPNIFTY" in u:
        return "MIDCPNIFTY"
    if "NIFTY" in u:
        return "NIFTY"
    return "OTHER"


def _sp_opt_type_from_symbol(sym: str) -> str | None:
    u = sym.upper().strip()
    if u.endswith("CE"):
        return "CE"
    if u.endswith("PE"):
        return "PE"
    return None


def _sp_to_ist(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Kolkata"))


@router.get("/strategy-performance")
async def get_strategy_performance(
    user_id: int = Depends(get_user_id),
    from_date: str | None = Query(None, description="Start date YYYY-MM-DD (closed_at)"),
    to_date: str | None = Query(None, description="End date YYYY-MM-DD"),
    filter_user_id: int | None = Query(None, alias="userId", description="Admin only: filter by user"),
    mode: str = Query("BOTH", description="PAPER, LIVE, or BOTH"),
    index: str = Query("ALL", description="ALL, NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, OTHER — filter by underlying in symbol"),
) -> dict:
    """Per-strategy stats plus overview widgets. Win/loss counts, win rates, streak, and profit factor use net P&L (after estimated charges)."""
    from app.api.schemas import _format_exit_reason

    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"
    if not is_admin and filter_user_id is not None and filter_user_id != user_id:
        filter_user_id = None

    to_d = ist_today()
    from_d = to_d - timedelta(days=89)
    if from_date:
        try:
            from_d = date.fromisoformat(from_date.strip())
        except ValueError:
            pass
    if to_date:
        try:
            to_d = date.fromisoformat(to_date.strip())
        except ValueError:
            pass
    if from_d > to_d:
        from_d, to_d = to_d, from_d

    mode_upper = (mode or "BOTH").upper()
    mode_filter = ""
    if mode_upper == "PAPER":
        mode_filter = " AND t.mode = 'PAPER'"
    elif mode_upper == "LIVE":
        mode_filter = " AND t.mode = 'LIVE'"

    user_filter = ""
    user_args: list = []
    if not is_admin:
        user_filter = " AND t.user_id = $3"
        user_args = [user_id]
    elif is_admin and filter_user_id is not None and filter_user_id > 0:
        user_filter = " AND t.user_id = $3"
        user_args = [filter_user_id]

    idx_upper = (index or "ALL").strip().upper()
    if idx_upper not in ("ALL", "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "OTHER"):
        idx_upper = "ALL"

    c_ist = closed_at_ist_date("t")
    q = f"""
        SELECT t.user_id, t.strategy_id, t.strategy_version,
               t.closed_at, t.opened_at, t.symbol,
               t.realized_pnl, t.entry_price, t.current_price, t.quantity,
               COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS display_name,
               (SELECT e.reason_code FROM s004_trade_events e
                WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
        FROM s004_live_trades t
        LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
        WHERE t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
          AND {c_ist} >= $1 AND {c_ist} <= $2
          {user_filter}
          {mode_filter}
        ORDER BY t.closed_at
    """
    rows = await fetch(q, from_d, to_d, *user_args)

    user_ids_all = list(set(int(r["user_id"]) for r in rows)) if rows else []
    user_lot_charges: dict[int, tuple[int, float]] = {}
    for uid in user_ids_all:
        lot_row = await fetchrow(
            "SELECT COALESCE(lot_size, 65) AS lot_size FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1",
            uid,
        )
        chg_row = await fetchrow(
            "SELECT COALESCE(charges_per_trade, 20) AS charges_per_trade FROM s004_user_master_settings WHERE user_id = $1",
            uid,
        )
        lot = int(lot_row["lot_size"] or 65) if lot_row else 65
        chg = float(chg_row["charges_per_trade"] or 20) if chg_row else 20.0
        user_lot_charges[uid] = (lot, chg)

    trades: list[dict[str, Any]] = []
    for r in rows:
        uid = int(r["user_id"])
        gross = float(r["realized_pnl"] or 0)
        entry = float(r["entry_price"] or 0)
        exit_p = float(r["current_price"] or r["entry_price"] or 0)
        qty = int(r["quantity"] or 1)
        lot, chg = user_lot_charges.get(uid, (65, 20.0))
        contracts = qty * lot
        est_ch = _est_charges_per_trade(gross, entry, exit_p, contracts, chg)
        net = gross - est_ch
        sym = str(r.get("symbol") or "")
        ix = _sp_index_from_symbol(sym)
        if idx_upper != "ALL" and ix != idx_upper:
            continue
        closed_raw = r.get("closed_at")
        opened_raw = r.get("opened_at")
        closed_dt = closed_raw if isinstance(closed_raw, datetime) else None
        opened_dt = opened_raw if isinstance(opened_raw, datetime) else None
        trades.append(
            {
                "user_id": uid,
                "strategy_id": str(r.get("strategy_id") or "unknown"),
                "strategy_version": str(r.get("strategy_version") or ""),
                "display_name": str(r.get("display_name") or ""),
                "symbol": sym,
                "index": ix,
                "opt_type": _sp_opt_type_from_symbol(sym),
                "gross": gross,
                "net": round(net, 2),
                "charges": round(est_ch, 2),
                "closed_at": closed_dt,
                "opened_at": opened_dt,
                "exit_reason_code": r.get("exit_reason_code"),
            }
        )

    # Per-strategy aggregation + per-trade nets for best/worst/avg
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for t in trades:
        sid, sver = t["strategy_id"], t["strategy_version"]
        key = (sid, sver)
        if key not in agg:
            agg[key] = {
                "display_name": t["display_name"] or f"{sid} {sver}".strip(),
                "trade_count": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "gross_pnl": 0.0,
                "charges": 0.0,
                "net_pnl": 0.0,
                "nets": [],
            }
        a = agg[key]
        n = t["net"]
        a["trade_count"] += 1
        if n > 0:
            a["wins"] += 1
        elif n < 0:
            a["losses"] += 1
        else:
            a["breakeven"] += 1
        a["gross_pnl"] += t["gross"]
        a["charges"] += t["charges"]
        a["net_pnl"] += t["net"]
        a["nets"].append(t["net"])

    strategies_out: list[dict] = []
    weekly_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for (sid, sver), a in agg.items():
        wr = round(float(a["wins"]) / float(a["trade_count"]) * 100.0, 1) if a["trade_count"] else 0.0
        nets: list[float] = a["nets"]
        strategies_out.append(
            {
                "strategy_id": sid,
                "strategy_version": sver,
                "display_name": a["display_name"],
                "trade_count": int(a["trade_count"]),
                "wins": int(a["wins"]),
                "losses": int(a["losses"]),
                "breakeven": int(a["breakeven"]),
                "win_rate_pct": wr,
                "gross_pnl": round(float(a["gross_pnl"]), 2),
                "charges": round(float(a["charges"]), 2),
                "net_pnl": round(float(a["net_pnl"]), 2),
                "avg_net_pnl": round(float(a["net_pnl"]) / float(a["trade_count"]), 2) if a["trade_count"] else 0.0,
                "best_trade_net": round(max(nets), 2) if nets else 0.0,
                "worst_trade_net": round(min(nets), 2) if nets else 0.0,
            }
        )
        sk = f"{sid}|{sver}"
        weekly_by_strategy[sk] = []

    strategies_out.sort(key=lambda x: x["net_pnl"], reverse=True)

    # Weekly splits per strategy (ISO week Monday, IST)
    wk_agg: dict[tuple[str, str, date], dict[str, Any]] = {}
    for t in trades:
        sk = f"{t['strategy_id']}|{t['strategy_version']}"
        cdt = _sp_to_ist(t["closed_at"])
        if cdt is None:
            continue
        d = cdt.date()
        monday = d - timedelta(days=d.weekday())
        wkey = (t["strategy_id"], t["strategy_version"], monday)
        if wkey not in wk_agg:
            wk_agg[wkey] = {"nets": [], "wins": 0, "losses": 0, "breakeven": 0}
        w = wk_agg[wkey]
        w["nets"].append(t["net"])
        n = t["net"]
        if n > 0:
            w["wins"] += 1
        elif n < 0:
            w["losses"] += 1
        else:
            w["breakeven"] += 1

    for (sid, sver, monday), w in sorted(wk_agg.items(), key=lambda x: (x[0][0], x[0][1], x[0][2])):
        sk = f"{sid}|{sver}"
        nets = w["nets"]
        n = len(nets)
        tw = int(w["wins"])
        tl = int(w["losses"])
        weekly_by_strategy.setdefault(sk, []).append(
            {
                "week_start": monday.isoformat(),
                "trade_count": n,
                "wins": tw,
                "losses": tl,
                "win_rate_pct": round(100.0 * tw / n, 1) if n else 0.0,
                "net_pnl": round(sum(nets), 2),
            }
        )

    n_all = len(trades)
    wins_all = sum(1 for t in trades if t["net"] > 0)
    losses_all = sum(1 for t in trades if t["net"] < 0)
    be_all = sum(1 for t in trades if t["net"] == 0)
    total_net = round(sum(t["net"] for t in trades), 2)
    total_charges = round(sum(t["charges"] for t in trades), 2)
    total_gross = round(sum(t["gross"] for t in trades), 2)
    win_rate = round(100.0 * wins_all / n_all, 1) if n_all else 0.0
    avg_net = round(total_net / n_all, 2) if n_all else 0.0
    nets_list = [t["net"] for t in trades]
    best_trade = round(max(nets_list), 2) if nets_list else 0.0
    worst_trade = round(min(nets_list), 2) if nets_list else 0.0

    sum_win_net = sum(t["net"] for t in trades if t["net"] > 0)
    sum_loss_net = sum(t["net"] for t in trades if t["net"] < 0)
    profit_factor = None
    if sum_loss_net < 0:
        profit_factor = round(sum_win_net / abs(sum_loss_net), 2)

    durations_min: list[float] = []
    for t in trades:
        o = _sp_to_ist(t["opened_at"])
        c = _sp_to_ist(t["closed_at"])
        if o and c:
            durations_min.append((c - o).total_seconds() / 60.0)
    avg_duration_min = round(sum(durations_min) / len(durations_min), 1) if durations_min else None

    sorted_by_close = sorted(
        [t for t in trades if t["closed_at"]],
        key=lambda x: x["closed_at"] or datetime.min.replace(tzinfo=timezone.utc),
    )
    max_dd = 0.0
    peak = 0.0
    cum = 0.0
    for t in sorted_by_close:
        cum += t["net"]
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    max_drawdown = round(max_dd, 2)

    streak_type: str | None = None
    streak_n = 0
    desc = sorted(
        [t for t in trades if t["closed_at"]],
        key=lambda x: x["closed_at"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if desc:
        fn = desc[0]["net"]
        if fn > 0:
            streak_type = "W"
        elif fn < 0:
            streak_type = "L"
        if streak_type:
            for t in desc:
                n = t["net"]
                if n == 0:
                    break
                if streak_type == "W" and n > 0:
                    streak_n += 1
                elif streak_type == "L" and n < 0:
                    streak_n += 1
                else:
                    break

    # Monthly net (IST month)
    monthly: dict[str, float] = {}
    for t in trades:
        c = _sp_to_ist(t["closed_at"])
        if not c:
            continue
        mk = f"{c.year}-{c.month:02d}"
        monthly[mk] = monthly.get(mk, 0.0) + t["net"]
    monthly_list = [{"month": k, "net_pnl": round(v, 2)} for k, v in sorted(monthly.items())]

    # Hourly 9–15 IST
    hourly: dict[int, dict[str, Any]] = {h: {"nets": [], "wins": 0, "losses": 0} for h in range(9, 16)}
    for t in trades:
        c = _sp_to_ist(t["closed_at"])
        if not c:
            continue
        h = c.hour
        if h not in hourly:
            continue
        hourly[h]["nets"].append(t["net"])
        n = t["net"]
        if n > 0:
            hourly[h]["wins"] += 1
        elif n < 0:
            hourly[h]["losses"] += 1
    hourly_list = []
    for h in range(9, 16):
        block = hourly[h]
        nets = block["nets"]
        n = len(nets)
        tw, tl = int(block["wins"]), int(block["losses"])
        hourly_list.append(
            {
                "hour_start": h,
                "label": f"{h:02d}:00–{h + 1:02d}:00",
                "trade_count": n,
                "wins": tw,
                "losses": tl,
                "win_rate_pct": round(100.0 * tw / n, 1) if n else None,
                "net_pnl": round(sum(nets), 2),
            }
        )

    # Weekday Mon–Fri (IST)
    wd_agg: dict[int, dict[str, Any]] = {
        i: {"nets": [], "wins": 0, "losses": 0} for i in range(5)
    }
    wd_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for t in trades:
        c = _sp_to_ist(t["closed_at"])
        if not c:
            continue
        wd = c.weekday()
        if wd > 4:
            continue
        wd_agg[wd]["nets"].append(t["net"])
        n = t["net"]
        if n > 0:
            wd_agg[wd]["wins"] += 1
        elif n < 0:
            wd_agg[wd]["losses"] += 1
    weekday_list = []
    for i in range(5):
        nets = wd_agg[i]["nets"]
        n = len(nets)
        tw, tl = int(wd_agg[i]["wins"]), int(wd_agg[i]["losses"])
        weekday_list.append(
            {
                "day_index": i,
                "day": wd_names[i],
                "trade_count": n,
                "wins": tw,
                "losses": tl,
                "win_rate_pct": round(100.0 * tw / n, 1) if n else None,
                "net_pnl": round(sum(nets), 2),
            }
        )

    # By index
    ix_agg: dict[str, dict[str, Any]] = {}
    for t in trades:
        ix = t["index"]
        if ix not in ix_agg:
            ix_agg[ix] = {"nets": [], "wins": 0, "losses": 0}
        ix_agg[ix]["nets"].append(t["net"])
        n = t["net"]
        if n > 0:
            ix_agg[ix]["wins"] += 1
        elif n < 0:
            ix_agg[ix]["losses"] += 1
    by_index = []
    for ix, b in sorted(ix_agg.items()):
        nets = b["nets"]
        n = len(nets)
        tw, tl = int(b["wins"]), int(b["losses"])
        by_index.append(
            {
                "index": ix,
                "trade_count": n,
                "wins": tw,
                "losses": tl,
                "win_rate_pct": round(100.0 * tw / n, 1) if n else 0.0,
                "net_pnl": round(sum(nets), 2),
            }
        )

    # CE / PE
    ce = {"nets": [], "wins": 0, "losses": 0}
    pe = {"nets": [], "wins": 0, "losses": 0}
    for t in trades:
        ot = t["opt_type"]
        bucket = ce if ot == "CE" else pe if ot == "PE" else None
        if bucket is None:
            continue
        bucket["nets"].append(t["net"])
        n = t["net"]
        if n > 0:
            bucket["wins"] += 1
        elif n < 0:
            bucket["losses"] += 1

    def _side_block(bucket: dict[str, Any]) -> dict[str, Any]:
        nets = bucket["nets"]
        n = len(nets)
        tw, tl = int(bucket["wins"]), int(bucket["losses"])
        return {
            "trade_count": n,
            "wins": tw,
            "losses": tl,
            "win_rate_pct": round(100.0 * tw / n, 1) if n else 0.0,
            "net_pnl": round(sum(nets), 2),
        }

    ce_pe = {"CE": _side_block(ce), "PE": _side_block(pe)}

    # Exit reasons
    reason_counts: dict[str, int] = {}
    for t in trades:
        code = str(t["exit_reason_code"] or "UNKNOWN").upper()
        reason_counts[code] = reason_counts.get(code, 0) + 1
    exit_reasons = [
        {"code": k, "label": _format_exit_reason(k if k != "UNKNOWN" else None), "count": v}
        for k, v in sorted(reason_counts.items(), key=lambda x: -x[1])
    ]

    # ACTIVE marketplace subscriptions (not tied to date range)
    active_sub_scope: str
    active_strategies_out: list[dict[str, Any]]
    _sub_sql_base = """
        SELECT s.strategy_id, s.strategy_version,
               COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version) AS display_name
        FROM s004_strategy_subscriptions s
        LEFT JOIN s004_strategy_catalog c ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
    """
    if not is_admin:
        active_sub_scope = "user"
        _sr = await fetch(
            _sub_sql_base + " WHERE s.user_id = $1 AND s.status = 'ACTIVE' ORDER BY display_name",
            user_id,
        )
        active_strategies_out = [
            {
                "strategy_id": str(r["strategy_id"]),
                "strategy_version": str(r["strategy_version"]),
                "display_name": str(r["display_name"] or ""),
                "subscriber_count": 1,
            }
            for r in (_sr or [])
        ]
    elif filter_user_id is not None and filter_user_id > 0:
        active_sub_scope = "user"
        _sr = await fetch(
            _sub_sql_base + " WHERE s.user_id = $1 AND s.status = 'ACTIVE' ORDER BY display_name",
            filter_user_id,
        )
        active_strategies_out = [
            {
                "strategy_id": str(r["strategy_id"]),
                "strategy_version": str(r["strategy_version"]),
                "display_name": str(r["display_name"] or ""),
                "subscriber_count": 1,
            }
            for r in (_sr or [])
        ]
    else:
        active_sub_scope = "platform"
        _sr = await fetch(
            """
            SELECT s.strategy_id, s.strategy_version,
                   COALESCE(MAX(c.display_name), s.strategy_id || ' ' || s.strategy_version) AS display_name,
                   COUNT(DISTINCT s.user_id)::int AS subscriber_count
            FROM s004_strategy_subscriptions s
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
            WHERE s.status = 'ACTIVE'
            GROUP BY s.strategy_id, s.strategy_version
            ORDER BY display_name
            """,
        )
        active_strategies_out = [
            {
                "strategy_id": str(r["strategy_id"]),
                "strategy_version": str(r["strategy_version"]),
                "display_name": str(r["display_name"] or ""),
                "subscriber_count": int(r["subscriber_count"] or 0),
            }
            for r in (_sr or [])
        ]

    return {
        "strategies": strategies_out,
        "summary": {
            "strategy_count": len(strategies_out),
            "total_trades": n_all,
            "total_net_pnl": total_net,
        },
        "overview": {
            "total_trades": n_all,
            "wins": wins_all,
            "losses": losses_all,
            "breakeven": be_all,
            "win_rate_pct": win_rate,
            "total_net_pnl": total_net,
            "total_gross_pnl": total_gross,
            "total_charges": total_charges,
            "avg_net_pnl_per_trade": avg_net,
            "best_trade_net": best_trade,
            "worst_trade_net": worst_trade,
            "profit_factor": profit_factor,
            "avg_duration_min": avg_duration_min,
            "max_drawdown": max_drawdown,
            "current_streak": {"type": streak_type, "count": streak_n},
        },
        "monthly_net_pnl": monthly_list,
        "hourly_performance": hourly_list,
        "weekday_performance": weekday_list,
        "by_index": by_index,
        "ce_pe": ce_pe,
        "exit_reasons": exit_reasons,
        "strategy_weekly_splits": weekly_by_strategy,
        "filters": {"index": idx_upper, "from_date": from_d.isoformat(), "to_date": to_d.isoformat()},
        "active_subscriptions_scope": active_sub_scope,
        "active_strategies": active_strategies_out,
    }


@router.get("/daily-pnl")
async def get_daily_pnl(user_id: int = Depends(get_user_id)) -> list[dict]:
    """Return day-wise P&L for Cumulative P&L chart. Admin sees all users; users see own."""
    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    cda = closed_at_ist_date_bare()
    if is_admin:
        rows = await fetch(
            f"""
            SELECT {cda} AS trade_date, COALESCE(SUM(realized_pnl), 0)::float AS pnl
            FROM s004_live_trades
            WHERE current_state = 'EXIT' AND closed_at IS NOT NULL
            GROUP BY {cda}
            ORDER BY trade_date ASC
            LIMIT 90
            """,
        )
    else:
        rows = await fetch(
            f"""
            SELECT {cda} AS trade_date, COALESCE(SUM(realized_pnl), 0)::float AS pnl
            FROM s004_live_trades
            WHERE user_id = $1 AND current_state = 'EXIT' AND closed_at IS NOT NULL
            GROUP BY {cda}
            ORDER BY trade_date ASC
            LIMIT 90
            """,
            user_id,
        )

    return [{"date": str(r["trade_date"]), "pnl": float(r["pnl"] or 0)} for r in rows]


@router.post("/simulate-close/{trade_ref}")
async def simulate_close_trade(trade_ref: str, user_id: int = Depends(get_user_id)) -> dict:
    row = await fetchrow(
        """
        SELECT entry_price, current_price, quantity, current_state, side, mode, symbol
        FROM s004_live_trades
        WHERE trade_ref = $1 AND user_id = $2
        """,
        trade_ref,
        user_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Trade not found for current user.")
    current_state = str(row.get("current_state") or "ACTIVE").upper()
    if current_state == "EXIT":
        raise HTTPException(status_code=400, detail="Trade is already closed.")

    entry = float(row.get("entry_price") or row.get("current_price") or 0.0)
    lots = int(row.get("quantity") or 1)
    side = str(row.get("side") or "BUY").upper()
    mode = str(row.get("mode") or "PAPER").upper()
    if entry <= 0:
        raise HTTPException(status_code=400, detail="Trade has invalid entry/current price.")
    lot_size = await _get_user_lot_size(user_id)
    contracts = lots * lot_size

    exit_price: float
    if mode == "LIVE":
        from app.services.execution_service import place_exit_order

        result = await place_exit_order(
            user_id=user_id,
            symbol=str(row.get("symbol") or ""),
            side=side,
            quantity=contracts,
        )
        if not result.success:
            if result.error_code == "TOKEN_EXPIRED":
                raise HTTPException(status_code=400, detail="Kite session expired. Reconnect Zerodha in Settings.")
            if result.error_code == "NO_CREDENTIALS":
                raise HTTPException(status_code=400, detail="Connect Zerodha in Settings to close Live trades.")
            raise HTTPException(status_code=400, detail=result.error_message or "Exit order failed.")
        exit_price = round(float(row.get("current_price") or entry), 2)
        if kite := await _get_kite_client_or_none(user_id):
            try:
                q = await asyncio.to_thread(kite.quote, [_symbol_to_kite_nfo(str(row.get("symbol") or ""))])
                data = (q.get("data") or q) if isinstance(q, dict) else {}
                raw = data.get(_symbol_to_kite_nfo(str(row.get("symbol") or ""))) if isinstance(data, dict) else {}
                if isinstance(raw, dict) and raw.get("last_price"):
                    exit_price = round(float(raw["last_price"]), 2)
            except Exception:
                pass
    else:
        exit_price = round(entry * 1.03, 2)
    pnl = round((exit_price - entry) * contracts, 2) if side == "BUY" else round((entry - exit_price) * contracts, 2)

    await execute(
        """
        UPDATE s004_live_trades
        SET current_state = 'EXIT',
            current_price = $1,
            realized_pnl = $2,
            unrealized_pnl = 0,
            closed_at = NOW(),
            updated_at = NOW()
        WHERE trade_ref = $3
          AND user_id = $4
          AND (current_state IS NULL OR current_state <> 'EXIT')
        """,
        exit_price,
        pnl,
        trade_ref,
        user_id,
    )

    await execute(
        """
        INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
        VALUES ($1,'MANUAL_CLOSE',$2,'EXIT','USER_CLOSE',$3::jsonb,NOW())
        """,
        trade_ref,
        current_state,
        json.dumps({"exit_price": exit_price, "pnl": pnl}),
    )

    return {"status": "ok", "trade_ref": trade_ref, "exit_price": exit_price, "pnl": pnl}
