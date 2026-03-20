"""Position monitor: polls open LIVE trades every 5s, executes SL/target/trailing exits via Kite."""
from __future__ import annotations

import asyncio
import json
import logging

from app.db_client import execute, fetch, fetchrow
from app.services.execution_service import place_exit_order

logger = logging.getLogger(__name__)
POLL_INTERVAL_SEC = 5


def _symbol_to_kite_nfo(symbol: str) -> str:
    s = str(symbol or "").replace(" ", "").upper()
    return f"NFO:{s}" if s else "NFO:"


async def _get_ltp_by_user(trades: list[dict]) -> dict[int, dict[str, float]]:
    """Fetch LTP for each (user_id, symbol) using that user's Kite. Returns {user_id: {nfo_symbol: ltp}}."""
    from app.services.trades_service import _get_kite_for_user

    result: dict[int, dict[str, float]] = {}
    seen: dict[int, set[str]] = {}
    for t in trades:
        uid = int(t["user_id"])
        sym = t["symbol"]
        nfo = _symbol_to_kite_nfo(sym)
        if uid not in seen:
            seen[uid] = set()
        if nfo in seen[uid]:
            continue
        seen[uid].add(nfo)

    for uid, symbols in seen.items():
        kite = await _get_kite_for_user(uid)
        if not kite:
            result[uid] = {}
            continue
        nfo_list = list(symbols)
        try:
            q = await asyncio.to_thread(kite.quote, nfo_list)
            data = q.get("data", q) if isinstance(q, dict) else {}
            quotes = data if isinstance(data, dict) else {}
            result[uid] = {
                nfo: float(raw.get("last_price") or 0.0)
                for nfo, raw in quotes.items()
                if isinstance(raw, dict)
            }
        except Exception as e:
            logger.warning("Position monitor: quote fetch failed for user %s: %s", uid, e)
            result[uid] = {}

    return result


async def _process_trade_exit(
    trade: dict,
    ltp: float,
    reason: str,
    exit_price: float,
    pnl: float,
) -> bool:
    """Place exit order and update DB. Returns True if succeeded."""
    user_id = int(trade["user_id"])
    trade_ref = str(trade["trade_ref"])
    symbol = str(trade["symbol"])
    side = str(trade.get("side") or "BUY").upper()
    quantity = int(trade.get("quantity") or 1)
    lot_size = 65
    row = await fetchrow(
        "SELECT lot_size FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1",
        user_id,
    )
    if row:
        lot_size = max(1, int(row.get("lot_size") or 65))
    contracts = quantity * lot_size

    result = await place_exit_order(user_id=user_id, symbol=symbol, side=side, quantity=contracts)
    if not result.success:
        if result.error_code == "TOKEN_EXPIRED":
            logger.warning("Position monitor: user %s token expired, skipping exit for %s", user_id, trade_ref)
        else:
            logger.error("Position monitor: exit order failed for %s: %s", trade_ref, result.error_message)
        return False

    await execute(
        """
        UPDATE s004_live_trades
        SET current_state = 'EXIT', current_price = $1, realized_pnl = $2,
            unrealized_pnl = 0, closed_at = NOW(), updated_at = NOW()
        WHERE trade_ref = $3 AND user_id = $4 AND current_state <> 'EXIT'
        """,
        exit_price,
        pnl,
        trade_ref,
        user_id,
    )
    await execute(
        """
        INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
        VALUES ($1,'AUTO_EXIT','ACTIVE','EXIT',$2,$3::jsonb,NOW())
        """,
        trade_ref,
        reason,
        json.dumps({"exit_price": exit_price, "pnl": pnl, "broker_order_id": result.order_id}),
    )
    logger.info("Position monitor: exited %s reason=%s pnl=%.2f", trade_ref, reason, pnl)
    return True


async def run_monitor_cycle() -> None:
    """
    Poll open LIVE trades, fetch LTP per user, check SL/target/trailing, place exit orders.
    User-isolated: each user's trades use that user's Kite for quotes and exits.
    """
    rows = await fetch(
        """
        SELECT t.trade_ref, t.user_id, t.symbol, t.side, t.quantity, t.entry_price,
               t.target_price, t.stop_loss_price, t.current_state
        FROM s004_live_trades t
        WHERE t.mode = 'LIVE' AND t.current_state <> 'EXIT'
        """
    )
    if not rows:
        return

    params_cache: dict[int, dict] = {}
    for uid in set(int(r["user_id"]) for r in rows):
        row = await fetchrow(
            """
            SELECT sl_points, target_points, breakeven_trigger_pct, trailing_sl_points, lot_size
            FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1
            """,
            uid,
        )
        params_cache[uid] = {
            "lot_size": max(1, int(row.get("lot_size") or 65)),
            "breakeven_trigger_pct": float(row.get("breakeven_trigger_pct") or 50),
            "trailing_sl_points": float(row.get("trailing_sl_points") or 20),
        } if row else {"lot_size": 65, "breakeven_trigger_pct": 50.0, "trailing_sl_points": 20.0}

    ltp_map = await _get_ltp_by_user(rows)

    for r in rows:
        user_id = int(r["user_id"])
        trade_ref = str(r["trade_ref"])
        nfo = _symbol_to_kite_nfo(r["symbol"])
        ltp = ltp_map.get(user_id, {}).get(nfo, 0.0)
        if ltp <= 0:
            continue

        entry = float(r["entry_price"])
        target_price = float(r["target_price"])
        stop_loss_price = float(r["stop_loss_price"])
        side = str(r.get("side") or "BUY").upper()
        state = str(r.get("current_state") or "ACTIVE")
        params = params_cache.get(user_id, {})
        lot_size = params.get("lot_size", 65)
        breakeven_pct = params.get("breakeven_trigger_pct", 50)
        trailing_pts = params.get("trailing_sl_points", 20)
        contracts = int(r.get("quantity") or 1) * lot_size

        if side == "BUY":
            pnl = (ltp - entry) * contracts
            hit_target = ltp >= target_price
            hit_sl = ltp <= stop_loss_price
            profit_pct = 100.0 * (ltp - entry) / entry if entry > 0 else 0
            new_trailing_sl = ltp - trailing_pts
        else:
            pnl = (entry - ltp) * contracts
            hit_target = ltp <= target_price
            hit_sl = ltp >= stop_loss_price
            profit_pct = 100.0 * (entry - ltp) / entry if entry > 0 else 0
            new_trailing_sl = ltp + trailing_pts

        if hit_target:
            await _process_trade_exit(r, ltp, "TARGET_HIT", round(ltp, 2), round(pnl, 2))
            continue
        if hit_sl:
            await _process_trade_exit(r, ltp, "SL_HIT", round(ltp, 2), round(pnl, 2))
            continue

        if state == "ACTIVE" and profit_pct >= breakeven_pct:
            if side == "BUY" and new_trailing_sl > stop_loss_price:
                await execute(
                    """
                    UPDATE s004_live_trades
                    SET current_state = 'TRAIL', stop_loss_price = $1, updated_at = NOW()
                    WHERE trade_ref = $2 AND user_id = $3 AND current_state = 'ACTIVE'
                    """,
                    round(new_trailing_sl, 2),
                    trade_ref,
                    user_id,
                )
                await execute(
                    """
                    INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
                    VALUES ($1,'TRAIL_ACTIVATED','ACTIVE','TRAIL','BREAKEVEN',$2::jsonb,NOW())
                    """,
                    trade_ref,
                    json.dumps({"stop_loss_price": round(new_trailing_sl, 2), "ltp": ltp}),
                )
            elif side == "SELL" and new_trailing_sl < stop_loss_price:
                await execute(
                    """
                    UPDATE s004_live_trades
                    SET current_state = 'TRAIL', stop_loss_price = $1, updated_at = NOW()
                    WHERE trade_ref = $2 AND user_id = $3 AND current_state = 'ACTIVE'
                    """,
                    round(new_trailing_sl, 2),
                    trade_ref,
                    user_id,
                )
                await execute(
                    """
                    INSERT INTO s004_trade_events (trade_ref, event_type, prev_state, next_state, reason_code, event_payload, occurred_at)
                    VALUES ($1,'TRAIL_ACTIVATED','ACTIVE','TRAIL','BREAKEVEN',$2::jsonb,NOW())
                    """,
                    trade_ref,
                    json.dumps({"stop_loss_price": round(new_trailing_sl, 2), "ltp": ltp}),
                )

        elif state == "TRAIL":
            if side == "BUY" and new_trailing_sl > stop_loss_price:
                await execute(
                    """
                    UPDATE s004_live_trades SET stop_loss_price = $1, updated_at = NOW()
                    WHERE trade_ref = $2 AND user_id = $3 AND current_state = 'TRAIL'
                    """,
                    round(new_trailing_sl, 2),
                    trade_ref,
                    user_id,
                )
            elif side == "SELL" and new_trailing_sl < stop_loss_price:
                await execute(
                    """
                    UPDATE s004_live_trades SET stop_loss_price = $1, updated_at = NOW()
                    WHERE trade_ref = $2 AND user_id = $3 AND current_state = 'TRAIL'
                    """,
                    round(new_trailing_sl, 2),
                    trade_ref,
                    user_id,
                )


async def position_monitor_loop() -> None:
    """Background loop: run monitor every POLL_INTERVAL_SEC."""
    while True:
        try:
            await run_monitor_cycle()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Position monitor cycle error: %s", e)
        await asyncio.sleep(POLL_INTERVAL_SEC)
