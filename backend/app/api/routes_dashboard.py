from __future__ import annotations

from datetime import date, datetime, timedelta

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth_context import get_user_id
from app.db_client import ensure_user, execute, fetch, fetchrow
from app.services.ist_time_sql import IST_TODAY, closed_at_ist_date_bare
from app.services.platform_risk import (
    evaluate_trade_entry_allowed,
    get_platform_trading_paused,
    user_today_realized_pnl_ist,
)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _consecutive_iso_week_streak(sorted_iso_keys_desc: list[str]) -> int:
    """ISO weeks (IYYYIW) with ≥1 close; count backward from most recent without gaps."""
    if not sorted_iso_keys_desc:
        return 0
    active = set(sorted_iso_keys_desc)
    cur = sorted_iso_keys_desc[0]
    n = 0
    while cur in active:
        n += 1
        if len(cur) != 6:
            break
        y = int(cur[:4])
        w = int(cur[4:6])
        d = date.fromisocalendar(y, w, 1) - timedelta(days=7)
        ic = d.isocalendar()
        cur = f"{ic.year:04d}{ic.week:02d}"
    return n


async def _fetch_trading_week_streak(user_id: int) -> int:
    # IST calendar date of each close, then ISO year+week (Mon-based). Order by last close in week
    # (string sort of IYYYIW is wrong across calendar years).
    # Assumes closed_at is TIMESTAMP WITHOUT TIME ZONE in UTC. If already IST-naive, replace inner
    # expression with: ((closed_at AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata')::date
    rows = await fetch(
        """
        SELECT iso_week
        FROM (
            SELECT
                to_char(d, 'IYYYIW') AS iso_week,
                MAX(d) AS last_d
            FROM (
                SELECT ((closed_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Kolkata')::date AS d
                FROM s004_live_trades
                WHERE user_id = $1
                  AND current_state = 'EXIT'
                  AND closed_at IS NOT NULL
            ) x
            GROUP BY to_char(d, 'IYYYIW')
        ) y
        ORDER BY last_d DESC
        """,
        user_id,
    )
    keys = [str(r["iso_week"]) for r in rows if r.get("iso_week")]
    return _consecutive_iso_week_streak(keys)


def _est_charges_per_trade(
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


class EnginePayload(BaseModel):
    engineRunning: bool
    mode: str = "PAPER"


async def _active_strategy_for_banner(user_id: int) -> dict | None:
    """
    Read-only: strategy for the status strip. Always requires an ACTIVE subscription so we never
    show a stale settings row for a strategy the user is no longer subscribed to.
    """
    row = await fetchrow(
        """
        SELECT s.strategy_id, s.strategy_version, c.display_name
        FROM s004_user_strategy_settings s
        JOIN s004_strategy_subscriptions sub
            ON sub.user_id = s.user_id AND sub.strategy_id = s.strategy_id AND sub.strategy_version = s.strategy_version
        JOIN s004_strategy_catalog c ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
        WHERE s.user_id = $1 AND sub.status = 'ACTIVE'
        ORDER BY s.updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    async def _build_payload(sid: str, ver: str, dn: Any) -> dict[str, Any]:
        from app.services.trades_service import get_strategy_score_params

        position_intent = "long_premium"
        try:
            params = await get_strategy_score_params(sid, ver, user_id)
            pi = str(
                params.get("execution_action_intent", params.get("position_intent", "long_premium"))
            ).strip().lower()
            if pi in {"long_premium", "short_premium"}:
                position_intent = pi
        except Exception:
            position_intent = "long_premium"
        return {
            "strategyId": sid,
            "strategyVersion": ver,
            "displayName": str(dn).strip() if dn else sid,
            "positionIntent": position_intent,
        }

    if row:
        sid = str(row["strategy_id"])
        ver = str(row["strategy_version"])
        dn = row.get("display_name")
        return await _build_payload(sid, ver, dn)
    row = await fetchrow(
        """
        SELECT sub.strategy_id, sub.strategy_version, c.display_name
        FROM s004_strategy_subscriptions sub
        JOIN s004_strategy_catalog c ON c.strategy_id = sub.strategy_id AND c.version = sub.strategy_version
        WHERE sub.user_id = $1 AND sub.status = 'ACTIVE'
        ORDER BY sub.updated_at DESC
        LIMIT 1
        """,
        user_id,
    )
    if row:
        sid = str(row["strategy_id"])
        ver = str(row["strategy_version"])
        dn = row.get("display_name")
        return await _build_payload(sid, ver, dn)
    return None


@router.get("/engine")
async def get_engine_status(user_id: int = Depends(get_user_id)) -> dict:
    await ensure_user(user_id)
    """Return engine_running, mode, broker status, shared API, kite source, and resolved active strategy for dashboard."""
    row = await fetchrow(
        """
        SELECT m.engine_running, m.mode, m.broker_connected, m.shared_api_connected,
               u.role, u.approved_live,
               COALESCE(m.platform_api_online, TRUE) AS platform_api_online,
               COALESCE(m.max_trades_day, 4) AS max_trades_day
        FROM s004_user_master_settings m
        LEFT JOIN s004_users u ON u.id = m.user_id
        WHERE m.user_id = $1
        """,
        user_id,
    )
    active_strategy = await _active_strategy_for_banner(user_id)
    if not row:
        out: dict = {
            "engineRunning": False,
            "mode": "PAPER",
            "brokerConnected": False,
            "sharedApiConnected": True,
            "isAdmin": False,
            "kiteStatus": "shared",
            "platformApiOnline": True,
            "maxTradesDay": 4,
        }
        if active_strategy:
            out["activeStrategy"] = active_strategy
        return out
    broker_connected = bool(row.get("broker_connected"))
    shared_api = bool(row.get("shared_api_connected", True))
    is_admin = row and str(row.get("role", "")).upper() == "ADMIN"
    mode = str(row.get("mode") or "PAPER").upper()
    approved_live = bool(row.get("approved_live"))
    if not is_admin and mode == "LIVE" and not approved_live:
        mode = "PAPER"
        await execute(
            """
            UPDATE s004_user_master_settings
            SET mode = $2, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            mode,
        )
    if is_admin and broker_connected:
        kite_status = "connected"
    elif shared_api:
        kite_status = "shared"
    else:
        kite_status = "none"
    out = {
        "engineRunning": bool(row.get("engine_running")),
        "mode": mode,
        "brokerConnected": broker_connected,
        "sharedApiConnected": shared_api,
        "isAdmin": is_admin,
        "kiteStatus": kite_status,
        "platformApiOnline": bool(row.get("platform_api_online", True)),
        "maxTradesDay": int(row.get("max_trades_day") or 4),
    }
    if active_strategy:
        out["activeStrategy"] = active_strategy
    return out


@router.put("/engine")
async def set_engine_status(payload: EnginePayload, user_id: int = Depends(get_user_id)) -> dict:
    """Update engine_running and mode for the current user."""
    await ensure_user(user_id)
    from app.api.auth_context import check_mode_approval
    await check_mode_approval(user_id, payload.mode or "PAPER")
    await execute(
        """
        UPDATE s004_user_master_settings
        SET engine_running = $2, mode = $3, updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
        payload.engineRunning,
        payload.mode.upper() if payload.mode else "PAPER",
    )
    return {"engineRunning": payload.engineRunning, "mode": payload.mode}


@router.get("/risk-status")
async def get_risk_status(user_id: int = Depends(get_user_id)) -> dict:
    """Today's realized P&L vs daily caps, platform pause, and whether new trades are allowed."""
    await ensure_user(user_id)
    paused, pause_reason = await get_platform_trading_paused()
    today = await user_today_realized_pnl_ist(user_id)
    master = await fetchrow(
        """
        SELECT COALESCE(max_loss_day, 0)::float AS max_loss_day,
               COALESCE(max_profit_day, 0)::float AS max_profit_day
        FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    max_loss = float(master["max_loss_day"] or 0) if master else 0.0
    max_profit = float(master["max_profit_day"] or 0) if master else 0.0
    allowed, code, _msg = await evaluate_trade_entry_allowed(user_id)
    return {
        "platformTradingPaused": paused,
        "platformPauseReason": pause_reason,
        "todayRealizedPnl": round(today, 2),
        "maxLossDay": round(max_loss, 2),
        "maxProfitDay": round(max_profit, 2),
        "newTradesAllowed": allowed,
        "blockReasonCode": code if not allowed else None,
    }


@router.get("/summary")
async def get_dashboard_summary(user_id: int = Depends(get_user_id)) -> dict:
    summary = await fetchrow(
        """
        SELECT open_trades, closed_trades, realized_pnl, unrealized_pnl, latest_update_at
        FROM s004_dashboard_live_view
        WHERE user_id = $1
        """,
        user_id,
    )

    if summary is None:
        tws = await _fetch_trading_week_streak(user_id)
        return {
            "open_trades": 0,
            "closed_trades": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "gross_pnl": 0.0,
            "charges_today": 0.0,
            "win_rate_pct": 0.0,
            "trading_week_streak": tws,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

    try:
        master = await fetchrow(
            """
            SELECT COALESCE(charges_per_trade, 20) AS charges_per_trade
            FROM s004_user_master_settings
            WHERE user_id = $1
            """,
            user_id,
        )
        charges_per_trade = float(master["charges_per_trade"] or 20) if master else 20.0
    except asyncpg.UndefinedColumnError:
        charges_per_trade = 20.0

    open_count = await fetchrow(
        """
        SELECT COUNT(*) AS n FROM s004_live_trades
        WHERE user_id = $1 AND current_state <> 'EXIT'
        """,
        user_id,
    )
    open_trades = int(open_count["n"] or 0) if open_count else 0

    today_stats = await fetchrow(
        f"""
        SELECT
            COUNT(*) AS closed_today,
            COALESCE(SUM(realized_pnl), 0) AS realized_today,
            COUNT(*) FILTER (WHERE (realized_pnl - $2) > 0) AS winners_net
        FROM s004_live_trades
        WHERE user_id = $1
          AND current_state = 'EXIT'
          AND closed_at IS NOT NULL
          AND {closed_at_ist_date_bare()} = {IST_TODAY}
        """,
        user_id,
        charges_per_trade,
    )

    stt_and_other = 0.0
    if today_stats and int(today_stats["closed_today"] or 0) > 0:
        closed_trades = int(today_stats["closed_today"])
        realized = float(today_stats["realized_today"] or 0.0)
        winners_net = int(today_stats["winners_net"] or 0)
        win_rate = round((winners_net / closed_trades) * 100, 2)
        trade_count = open_trades + closed_trades
        brokerage = trade_count * charges_per_trade
        gst_on_brokerage = round(brokerage * 0.18, 2)
        lot_size_row = await fetchrow(
            "SELECT COALESCE(lot_size, 65) AS lot_size FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1",
            user_id,
        )
        lot_size = int(lot_size_row["lot_size"] or 65) if lot_size_row else 65
        stt_row = await fetchrow(
            f"""
            SELECT
                COALESCE(SUM(entry_price * quantity * $2), 0) AS buy_value,
                COALESCE(SUM(COALESCE(current_price, entry_price) * quantity * $2), 0) AS sell_value
            FROM s004_live_trades
            WHERE user_id = $1
              AND current_state = 'EXIT'
              AND closed_at IS NOT NULL
              AND {closed_at_ist_date_bare()} = {IST_TODAY}
            """,
            user_id,
            lot_size,
        )
        buy_value = float(stt_row["buy_value"] or 0.0) if stt_row else 0.0
        sell_value = float(stt_row["sell_value"] or 0.0) if stt_row else 0.0
        turnover = buy_value + sell_value
        stt = round(turnover * 0.001, 2)
        exchange_sebi_stamp = round(turnover * (0.00035 + 0.00001 + 0.00003) / 100, 2)
        stt_and_other = stt + gst_on_brokerage + exchange_sebi_stamp
        charges_today = round(brokerage + stt_and_other, 2)
    else:
        closed_trades = 0
        realized = 0.0
        win_rate = 0.0
        trade_count = open_trades
        charges_today = round(trade_count * charges_per_trade, 2)

    unrealized = float(summary["unrealized_pnl"] or 0.0)
    trading_week_streak = await _fetch_trading_week_streak(user_id)

    return {
        "open_trades": int(summary["open_trades"] or 0),
        "closed_trades": closed_trades,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "gross_pnl": round(realized + unrealized, 2),
        "charges_today": charges_today,
        "win_rate_pct": win_rate,
        "trading_week_streak": trading_week_streak,
        "updated_at": summary["latest_update_at"].isoformat() if summary["latest_update_at"] else datetime.utcnow().isoformat() + "Z",
    }


@router.get("/funds")
async def get_dashboard_funds(user_id: int = Depends(get_user_id)) -> dict:
    master = await fetchrow(
        """
        SELECT initial_capital, max_investment_per_trade
        FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    if master is None:
        return {
            "initial_capital": 100000.0,
            "used_margin": 0.0,
            "available_cash": 100000.0,
            "net_balance": 100000.0,
            "bot_capital": 50000.0,
        }

    initial = float(master["initial_capital"])
    bot_cap = float(master["max_investment_per_trade"])

    lot_row = await fetchrow(
        "SELECT COALESCE(lot_size, 65) AS lot_size FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1",
        user_id,
    )
    lot_size = int(lot_row["lot_size"] or 65) if lot_row else 65

    chg_row = await fetchrow(
        "SELECT COALESCE(charges_per_trade, 20) AS charges_per_trade FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    charges_per_trade = float(chg_row["charges_per_trade"] or 20) if chg_row else 20.0

    used = await fetchrow(
        """
        SELECT COALESCE(SUM(entry_price * quantity * $2), 0) AS used_margin
        FROM s004_live_trades
        WHERE user_id = $1 AND current_state <> 'EXIT'
        """,
        user_id,
        lot_size,
    )
    used_margin = float(used["used_margin"] or 0.0) if used else 0.0

    closed_rows = await fetch(
        """
        SELECT entry_price, current_price, quantity, realized_pnl
        FROM s004_live_trades
        WHERE user_id = $1 AND current_state = 'EXIT' AND closed_at IS NOT NULL
        """,
        user_id,
    )
    net_realized = 0.0
    for r in closed_rows:
        gross = float(r["realized_pnl"] or 0)
        entry = float(r["entry_price"] or 0)
        exit_p = float(r["current_price"] or r["entry_price"] or 0)
        qty = int(r["quantity"] or 1)
        contracts = qty * lot_size
        est_ch = _est_charges_per_trade(entry, exit_p, contracts, charges_per_trade)
        net_realized += gross - est_ch

    net_balance = initial + net_realized
    available_cash = net_balance - used_margin

    return {
        "initial_capital": initial,
        "used_margin": round(used_margin, 2),
        "available_cash": round(available_cash, 2),
        "net_balance": round(net_balance, 2),
        "bot_capital": bot_cap,
        "realized_pnl": round(net_realized, 2),
    }
