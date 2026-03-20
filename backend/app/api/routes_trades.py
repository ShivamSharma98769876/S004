from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from kiteconnect import KiteConnect

from app.db_client import ensure_user, execute, fetch, fetchrow
from app.api.auth_context import get_user_id
from app.api.schemas import ExecuteRequest, ExecuteResponse, RecommendationOut, TradeOut
from app.services.trades_service import ensure_recommendations, execute_recommendation, get_kite_for_quotes, get_strategy_score_params, list_recommendations_for_user

router = APIRouter(prefix="/trades", tags=["trades"])


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
) -> list[RecommendationOut]:
    await ensure_user(user_id)
    kite = await get_kite_for_quotes(user_id)  # Shared API for recommendations
    await ensure_recommendations(user_id, kite)

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
    score_max: int | None = None
    if rows:
        sid = rows[0].get("strategy_id")
        ver = rows[0].get("strategy_version")
        if sid and ver:
            params = await get_strategy_score_params(str(sid), str(ver), user_id)
            score_max = params.get("score_max")
    out = []
    for r in rows:
        item = dict(r)
        if score_max is not None:
            item["score_max"] = score_max
        out.append(RecommendationOut(**item))
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
            out.append(TradeOut(**d))
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
            out.append(TradeOut(**d))
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
        out.append(TradeOut(**d))
    return out


@router.get("/history")
async def get_trade_history(
    user_id: int = Depends(get_user_id),
    today_only: bool = Query(default=False, description="Return only today's closed trades for TODAY'S CLOSED TRADES section"),
) -> list[TradeOut]:
    from app.api.schemas import _format_exit_reason

    if today_only:
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
              AND t.current_state = 'EXIT'
              AND t.closed_at IS NOT NULL
              AND t.closed_at::date = CURRENT_DATE
            ORDER BY t.closed_at DESC
            LIMIT 100
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
        out.append(TradeOut(**d))
    return out


@router.get("/reports")
async def get_trade_reports(user_id: int = Depends(get_user_id)) -> list[dict]:
    """Return all closed trades for Reports. Admin sees all users' trades with username; users see only their own."""
    from app.api.schemas import _format_exit_reason

    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    if is_admin:
        rows = await fetch(
            """
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
            WHERE t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
            ORDER BY t.closed_at DESC
            LIMIT 500
            """,
        )
    else:
        rows = await fetch(
            """
            SELECT t.trade_ref, t.symbol, t.mode, t.side, t.quantity, t.entry_price, t.current_price,
                   t.target_price, t.stop_loss_price, t.current_state, t.realized_pnl, t.unrealized_pnl,
                   t.opened_at, t.closed_at, t.updated_at, o.manual_execute,
                   COALESCE(c.display_name, t.strategy_id || ' ' || t.strategy_version) AS strategy_name,
                   (SELECT e.reason_code FROM s004_trade_events e
                    WHERE e.trade_ref = t.trade_ref AND e.next_state = 'EXIT'
                    ORDER BY e.occurred_at DESC LIMIT 1) AS exit_reason_code
            FROM s004_live_trades t
            LEFT JOIN s004_execution_orders o ON o.order_ref = t.order_ref
            LEFT JOIN s004_strategy_catalog c ON c.strategy_id = t.strategy_id AND c.version = t.strategy_version
            WHERE t.user_id = $1 AND t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
            ORDER BY t.closed_at DESC
            LIMIT 500
            """,
            user_id,
        )

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

    to_d = date.today()
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

    query_trades = f"""
        SELECT t.user_id, t.closed_at::date AS trade_date, t.realized_pnl,
               t.entry_price, t.current_price, t.quantity
        FROM s004_live_trades t
        WHERE t.current_state = 'EXIT' AND t.closed_at IS NOT NULL
          AND t.closed_at::date >= $1 AND t.closed_at::date <= $2
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


@router.get("/daily-pnl")
async def get_daily_pnl(user_id: int = Depends(get_user_id)) -> list[dict]:
    """Return day-wise P&L for Cumulative P&L chart. Admin sees all users; users see own."""
    role_row = await fetchrow("SELECT role FROM s004_users WHERE id = $1", user_id)
    is_admin = role_row and str(role_row.get("role", "")).upper() == "ADMIN"

    if is_admin:
        rows = await fetch(
            """
            SELECT closed_at::date AS trade_date, COALESCE(SUM(realized_pnl), 0)::float AS pnl
            FROM s004_live_trades
            WHERE current_state = 'EXIT' AND closed_at IS NOT NULL
            GROUP BY closed_at::date
            ORDER BY trade_date ASC
            LIMIT 90
            """,
        )
    else:
        rows = await fetch(
            """
            SELECT closed_at::date AS trade_date, COALESCE(SUM(realized_pnl), 0)::float AS pnl
            FROM s004_live_trades
            WHERE user_id = $1 AND current_state = 'EXIT' AND closed_at IS NOT NULL
            GROUP BY closed_at::date
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
