from __future__ import annotations

from datetime import datetime

import asyncpg
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.auth_context import get_user_id
from app.db_client import ensure_user, execute, fetch, fetchrow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


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


@router.get("/engine")
async def get_engine_status(user_id: int = Depends(get_user_id)) -> dict:
    await ensure_user(user_id)
    """Return engine_running, mode, broker status, shared API, and kite source for dashboard."""
    row = await fetchrow(
        """
        SELECT m.engine_running, m.mode, m.broker_connected, m.shared_api_connected,
               u.role
        FROM s004_user_master_settings m
        LEFT JOIN s004_users u ON u.id = m.user_id
        WHERE m.user_id = $1
        """,
        user_id,
    )
    if not row:
        return {
            "engineRunning": False,
            "mode": "PAPER",
            "brokerConnected": False,
            "sharedApiConnected": True,
            "isAdmin": False,
            "kiteStatus": "shared",
        }
    broker_connected = bool(row.get("broker_connected"))
    shared_api = bool(row.get("shared_api_connected", True))
    is_admin = row and str(row.get("role", "")).upper() == "ADMIN"
    if is_admin and broker_connected:
        kite_status = "connected"
    elif shared_api:
        kite_status = "shared"
    else:
        kite_status = "none"
    return {
        "engineRunning": bool(row.get("engine_running")),
        "mode": str(row.get("mode") or "PAPER"),
        "brokerConnected": broker_connected,
        "sharedApiConnected": shared_api,
        "isAdmin": is_admin,
        "kiteStatus": kite_status,
    }


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
        return {
            "open_trades": 0,
            "closed_trades": 0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "gross_pnl": 0.0,
            "charges_today": 0.0,
            "win_rate_pct": 0.0,
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
        """
        SELECT
            COUNT(*) AS closed_today,
            COALESCE(SUM(realized_pnl), 0) AS realized_today,
            COUNT(*) FILTER (WHERE (realized_pnl - $2) > 0) AS winners_net
        FROM s004_live_trades
        WHERE user_id = $1
          AND current_state = 'EXIT'
          AND closed_at IS NOT NULL
          AND closed_at::date = CURRENT_DATE
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
            """
            SELECT
                COALESCE(SUM(entry_price * quantity * $2), 0) AS buy_value,
                COALESCE(SUM(COALESCE(current_price, entry_price) * quantity * $2), 0) AS sell_value
            FROM s004_live_trades
            WHERE user_id = $1
              AND current_state = 'EXIT'
              AND closed_at IS NOT NULL
              AND closed_at::date = CURRENT_DATE
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

    return {
        "open_trades": int(summary["open_trades"] or 0),
        "closed_trades": closed_trades,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "gross_pnl": round(realized + unrealized, 2),
        "charges_today": charges_today,
        "win_rate_pct": win_rate,
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
