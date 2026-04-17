"""Position monitor: polls open LIVE and PAPER trades on a fixed interval.

LIVE: SL/target/trailing via Kite quotes + broker exit orders.
PAPER: same quote + SL/target/trailing logic; exits update DB only (no broker).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from app.db_client import execute, fetch, fetchrow
from app.services.lot_sizes import contract_multiplier_for_trade
from app.services.execution_service import place_exit_order
from app.services.trade_chain_snapshot_service import fire_and_forget_exit_snapshot

logger = logging.getLogger(__name__)
try:
    POLL_INTERVAL_SEC = max(1, int((os.getenv("POSITION_MONITOR_POLL_SEC") or "5").strip()))
except ValueError:
    POLL_INTERVAL_SEC = 5


def _symbol_to_kite_nfo(symbol: str) -> str:
    s = str(symbol or "").replace(" ", "").upper()
    return f"NFO:{s}" if s else "NFO:"


def _entry_market_snapshot_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            o = json.loads(raw)
            return dict(o) if isinstance(o, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _get_ltp_by_user(trades: list[dict]) -> dict[int, dict[str, float]]:
    """Fetch LTP per user via Zerodha quotes (own → platform shared → pool). FYERS active users fall back here."""
    from app.services.trades_service import get_kite_for_quotes

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
        kite = await get_kite_for_quotes(uid)
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
    row = await fetchrow(
        """
        SELECT lot_size, banknifty_lot_size FROM s004_user_strategy_settings
        WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1
        """,
        user_id,
    )
    nifty_l = max(1, int(row.get("lot_size") or 65)) if row else 65
    bn_l = max(1, int(row.get("banknifty_lot_size") or 30)) if row else 30
    mult = contract_multiplier_for_trade(
        strategy_id=str(trade.get("strategy_id") or ""),
        symbol=symbol,
        nifty_lot=nifty_l,
        banknifty_lot=bn_l,
    )
    contracts = quantity * mult

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
    fire_and_forget_exit_snapshot(trade_ref, user_id)
    return True


async def _process_paper_exit(
    trade: dict,
    ltp: float,
    reason: str,
    exit_price: float,
    pnl: float,
) -> None:
    """Mark PAPER trade EXIT in DB and log event (no broker order)."""
    user_id = int(trade["user_id"])
    trade_ref = str(trade["trade_ref"])
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
        json.dumps({"exit_price": exit_price, "pnl": pnl}),
    )
    logger.info("Position monitor (PAPER): exited %s reason=%s pnl=%.2f", trade_ref, reason, pnl)
    fire_and_forget_exit_snapshot(trade_ref, user_id)


async def run_monitor_cycle() -> None:
    """
    Poll open LIVE trades, fetch LTP per user, check SL/target/trailing, place exit orders.
    User-isolated: each user's trades use that user's Kite for quotes and exits.
    """
    rows = await fetch(
        """
        SELECT t.trade_ref, t.user_id, t.mode, t.symbol, t.side, t.quantity, t.entry_price,
               t.target_price, t.stop_loss_price, t.current_state,
               t.strategy_id, t.strategy_version, t.recommendation_id, r.instrument AS rec_instrument,
               t.entry_market_snapshot
        FROM s004_live_trades t
        LEFT JOIN s004_trade_recommendations r ON r.recommendation_id = t.recommendation_id
        WHERE t.mode IN ('LIVE', 'PAPER') AND t.current_state <> 'EXIT'
        """
    )
    if not rows:
        return

    params_cache: dict[int, dict] = {}
    for uid in set(int(r["user_id"]) for r in rows):
        row = await fetchrow(
            """
            SELECT sl_points, target_points, breakeven_trigger_pct, trailing_sl_points, lot_size,
                   banknifty_lot_size, trade_start, trade_end
            FROM s004_user_strategy_settings WHERE user_id = $1 ORDER BY updated_at DESC LIMIT 1
            """,
            uid,
        )
        params_cache[uid] = {
            "lot_size": max(1, int(row.get("lot_size") or 65)),
            "banknifty_lot_size": max(1, int(row.get("banknifty_lot_size") or 30)),
            "sl_points": float(row.get("sl_points") or 15),
            "target_points": float(row.get("target_points") or 10),
            "breakeven_trigger_pct": float(row.get("breakeven_trigger_pct") or 50),
            "trailing_sl_points": float(row.get("trailing_sl_points") or 20),
            "trade_start": row.get("trade_start"),
            "trade_end": row.get("trade_end"),
        } if row else {
            "lot_size": 65,
            "banknifty_lot_size": 30,
            "sl_points": 15.0,
            "target_points": 10.0,
            "breakeven_trigger_pct": 50.0,
            "trailing_sl_points": 20.0,
            "trade_start": None,
            "trade_end": None,
        }

    ltp_map = await _get_ltp_by_user(rows)

    def _row_contracts(rr: dict, uid: int) -> int:
        p = params_cache.get(uid, {})
        m = contract_multiplier_for_trade(
            strategy_id=str(rr.get("strategy_id") or ""),
            symbol=str(rr.get("symbol") or ""),
            instrument=str(rr.get("rec_instrument") or ""),
            nifty_lot=int(p.get("lot_size", 65)),
            banknifty_lot=int(p.get("banknifty_lot_size", 30)),
        )
        return int(rr.get("quantity") or 1) * m

    for r in rows:
        user_id = int(r["user_id"])
        trade_ref = str(r["trade_ref"])
        nfo = _symbol_to_kite_nfo(r["symbol"])
        ltp = ltp_map.get(user_id, {}).get(nfo, 0.0)
        if ltp <= 0:
            continue

        snap0 = _entry_market_snapshot_dict(r.get("entry_market_snapshot"))
        if snap0.get("live_entry_pending") is True:
            p0 = params_cache.get(user_id, {})
            sl_pts = float(p0.get("sl_points") or 15)
            tgt_pts = float(p0.get("target_points") or 10)
            side_u0 = str(r.get("side") or "BUY").upper()
            entry_new = round(float(ltp), 2)
            if side_u0 == "BUY":
                tgt_new = round(entry_new + tgt_pts, 2)
                sl_new = round(entry_new - sl_pts, 2)
            else:
                tgt_new = round(entry_new - tgt_pts, 2)
                sl_new = round(entry_new + sl_pts, 2)
            snap0["live_entry_pending"] = False
            snap0["entry_price_source"] = "live_quote_monitor_cycle"
            await execute(
                """
                UPDATE s004_live_trades
                SET entry_price = $1, current_price = $1, target_price = $2, stop_loss_price = $3,
                    entry_market_snapshot = $4::jsonb, updated_at = NOW()
                WHERE trade_ref = $5 AND user_id = $6 AND current_state <> 'EXIT'
                """,
                entry_new,
                tgt_new,
                sl_new,
                json.dumps(snap0),
                trade_ref,
                user_id,
            )
            r["entry_price"] = float(entry_new)
            r["target_price"] = float(tgt_new)
            r["stop_loss_price"] = float(sl_new)
            logger.info(
                "Position monitor: entry aligned to live LTP trade_ref=%s entry=%.2f tgt=%.2f sl=%.2f",
                trade_ref,
                entry_new,
                tgt_new,
                sl_new,
            )

        effective_sl = float(r["stop_loss_price"])

        strategy_id = str(r.get("strategy_id") or "")
        if strategy_id == "strat-supertrend-trail":
            pcache = params_cache.get(user_id, {})
            te_raw = pcache.get("trade_end")
            te: dt_time | None = None
            if hasattr(te_raw, "hour"):
                te = te_raw  # type: ignore[assignment]
            elif isinstance(te_raw, str):
                try:
                    parts = str(te_raw).strip().split(":")
                    te = dt_time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
                except (ValueError, IndexError):
                    te = None
            now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
            if (
                te is not None
                and now_ist.weekday() < 5
                and now_ist.time() > te
            ):
                entry = float(r["entry_price"])
                contracts = _row_contracts(r, user_id)
                side_u = str(r.get("side") or "BUY").upper()
                pnl = (entry - ltp) * contracts if side_u == "SELL" else (ltp - entry) * contracts
                ep, pn = round(ltp, 2), round(pnl, 2)
                mode = str(r.get("mode") or "LIVE").upper()
                if mode == "PAPER":
                    await _process_paper_exit(r, ltp, "TRADE_SESSION_END", ep, pn)
                else:
                    await _process_trade_exit(r, ltp, "TRADE_SESSION_END", ep, pn)
                continue
            try:
                from app.services.option_chain_zerodha import (
                    fetch_index_candles_sync,
                    fetch_option_minute_candles_today_ist_sync,
                    sorted_candles_chronological,
                )
                from app.services.trades_service import get_kite_for_quotes, get_strategy_score_params
                from app.strategies.supertrend_trail import (
                    compute_hybrid_sl_short_sell,
                    map_settings_timeframe_to_kite_interval,
                    session_vwap_from_ohlcv,
                    should_exit_on_spot_supertrend_flip,
                    snapshot_supertrend_state,
                )

                sp = await get_strategy_score_params(
                    str(r.get("strategy_id") or ""),
                    str(r.get("strategy_version") or "1.0.0"),
                    user_id,
                )
                cfg = sp.get("supertrend_trail_config") or {}
                if not isinstance(cfg, dict):
                    cfg = {}
                interval = map_settings_timeframe_to_kite_interval(sp.get("settings_timeframe"))
                days = int(cfg.get("candleDaysBack", 5) or 5)
                kite_pm = await get_kite_for_quotes(user_id)
                if kite_pm:
                    candles = await asyncio.to_thread(
                        fetch_index_candles_sync, kite_pm, "NIFTY", interval, days
                    )
                    candles = sorted_candles_chronological(candles)
                    snap = snapshot_supertrend_state(candles, cfg)
                    if str(r.get("side") or "").upper() == "SELL":
                        opt_mins = await asyncio.to_thread(
                            fetch_option_minute_candles_today_ist_sync,
                            kite_pm,
                            str(r.get("symbol") or "").strip(),
                        )
                        svwap = session_vwap_from_ohlcv(opt_mins) if opt_mins else None
                        nsl, sl_mode = compute_hybrid_sl_short_sell(
                            entry_premium=float(r["entry_price"]),
                            ltp=ltp,
                            session_vwap=svwap,
                            spot_snap=snap,
                            current_sl=effective_sl,
                            vwap_step_threshold_pct=float(cfg.get("vwapStepThresholdPct", 0.05) or 0.05),
                            entry_vs_vwap_eps_pct=float(cfg.get("entryVsVwapEpsPct", 0.02) or 0.02),
                        )
                        if nsl is not None and abs(nsl - effective_sl) >= 0.005:
                            await execute(
                                """
                                UPDATE s004_live_trades
                                SET stop_loss_price = $1, updated_at = NOW()
                                WHERE trade_ref = $2 AND user_id = $3 AND current_state IN ('ACTIVE', 'TRAIL')
                                """,
                                round(nsl, 2),
                                trade_ref,
                                user_id,
                            )
                            effective_sl = float(nsl)
                            logger.info(
                                "SuperTrendTrail hybrid SL trade_ref=%s mode=%s session_vwap=%s new_sl=%.2f",
                                trade_ref,
                                sl_mode,
                                svwap,
                                nsl,
                            )
                    rec_row = await fetchrow(
                        """
                        SELECT details_json FROM s004_trade_recommendations
                        WHERE recommendation_id = $1
                        """,
                        str(r.get("recommendation_id") or ""),
                    )
                    dj = rec_row.get("details_json") if rec_row else None
                    if isinstance(dj, str):
                        try:
                            dj = json.loads(dj) if dj.strip() else {}
                        except json.JSONDecodeError:
                            dj = {}
                    if not isinstance(dj, dict):
                        dj = {}
                    opt_type = str(dj.get("option_type") or "")
                    if (
                        snap
                        and opt_type
                        and should_exit_on_spot_supertrend_flip(
                            option_type=opt_type,
                            st_direction=int(snap.get("st_direction") or 0),
                        )
                    ):
                        entry_px = float(r["entry_price"])
                        contracts = _row_contracts(r, user_id)
                        side_u = str(r.get("side") or "BUY").upper()
                        pnl = (entry_px - ltp) * contracts if side_u == "SELL" else (ltp - entry_px) * contracts
                        ep, pn = round(ltp, 2), round(pnl, 2)
                        mode = str(r.get("mode") or "LIVE").upper()
                        if mode == "PAPER":
                            await _process_paper_exit(r, ltp, "SUPERTREND_FLIP", ep, pn)
                        else:
                            await _process_trade_exit(r, ltp, "SUPERTREND_FLIP", ep, pn)
                        continue
            except Exception as exc:
                logger.warning("SuperTrendTrail exit check failed for %s: %s", trade_ref, exc)

        if strategy_id == "strat-stochastic-bnf":
            try:
                from app.services.option_chain_zerodha import fetch_index_candles_sync
                from app.services.trades_service import get_kite_for_quotes, get_strategy_score_params
                from app.strategies.stochastic_bnf import (
                    map_settings_timeframe_to_kite_interval,
                    parse_exit_time_ist,
                    resolve_stochastic_bnf_config,
                    should_exit_on_ema5_15_cross,
                    snapshot_stochastic_bnf_ema_exit,
                )

                sp = await get_strategy_score_params(
                    str(r.get("strategy_id") or ""),
                    str(r.get("strategy_version") or "1.0.0"),
                    user_id,
                )
                raw_s = sp.get("stochastic_bnf_config")
                cfg = resolve_stochastic_bnf_config(raw_s if isinstance(raw_s, dict) else {})
                eh, emin = parse_exit_time_ist(cfg)
                now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
                if (
                    now_ist.weekday() < 5
                    and now_ist.time() >= dt_time(eh, emin)
                ):
                    entry = float(r["entry_price"])
                    contracts = _row_contracts(r, user_id)
                    side_u = str(r.get("side") or "BUY").upper()
                    pnl = (entry - ltp) * contracts if side_u == "SELL" else (ltp - entry) * contracts
                    ep, pn = round(ltp, 2), round(pnl, 2)
                    mode = str(r.get("mode") or "LIVE").upper()
                    if mode == "PAPER":
                        await _process_paper_exit(r, ltp, "TRADE_SESSION_END", ep, pn)
                    else:
                        await _process_trade_exit(r, ltp, "TRADE_SESSION_END", ep, pn)
                    continue
                interval = map_settings_timeframe_to_kite_interval(sp.get("settings_timeframe"))
                days = int(cfg.get("candleDaysBack", 8) or 8)
                kite_pm = await get_kite_for_quotes(user_id)
                if kite_pm:
                    candles = await asyncio.to_thread(
                        fetch_index_candles_sync, kite_pm, "BANKNIFTY", interval, days
                    )
                    snap = snapshot_stochastic_bnf_ema_exit(candles)
                    rec_row = await fetchrow(
                        """
                        SELECT details_json FROM s004_trade_recommendations
                        WHERE recommendation_id = $1
                        """,
                        str(r.get("recommendation_id") or ""),
                    )
                    dj = rec_row.get("details_json") if rec_row else None
                    if isinstance(dj, str):
                        try:
                            dj = json.loads(dj) if dj.strip() else {}
                        except json.JSONDecodeError:
                            dj = {}
                    if not isinstance(dj, dict):
                        dj = {}
                    opt_type = str(dj.get("option_type") or "")
                    if snap and opt_type and should_exit_on_ema5_15_cross(
                        option_type=opt_type,
                        ema5=float(snap.get("ema5") or 0),
                        ema15=float(snap.get("ema15") or 0),
                    ):
                        entry_px = float(r["entry_price"])
                        contracts = _row_contracts(r, user_id)
                        side_u = str(r.get("side") or "BUY").upper()
                        pnl = (entry_px - ltp) * contracts if side_u == "SELL" else (ltp - entry_px) * contracts
                        ep, pn = round(ltp, 2), round(pnl, 2)
                        mode = str(r.get("mode") or "LIVE").upper()
                        if mode == "PAPER":
                            await _process_paper_exit(r, ltp, "EMA_STRUCTURE_SL", ep, pn)
                        else:
                            await _process_trade_exit(r, ltp, "EMA_STRUCTURE_SL", ep, pn)
                        continue
            except Exception as exc:
                logger.warning("StochasticBNF exit check failed for %s: %s", trade_ref, exc)

        entry = float(r["entry_price"])
        target_price = float(r["target_price"])
        stop_loss_price = effective_sl
        side = str(r.get("side") or "BUY").upper()
        state = str(r.get("current_state") or "ACTIVE")
        params = params_cache.get(user_id, {})
        breakeven_pct = params.get("breakeven_trigger_pct", 50)
        trailing_pts = params.get("trailing_sl_points", 20)
        contracts = _row_contracts(r, user_id)

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

        mode = str(r.get("mode") or "LIVE").upper()
        if hit_target:
            ep, pn = round(ltp, 2), round(pnl, 2)
            if mode == "PAPER":
                await _process_paper_exit(r, ltp, "TARGET_HIT", ep, pn)
            else:
                await _process_trade_exit(r, ltp, "TARGET_HIT", ep, pn)
            continue
        if hit_sl:
            ep, pn = round(ltp, 2), round(pnl, 2)
            if mode == "PAPER":
                await _process_paper_exit(r, ltp, "SL_HIT", ep, pn)
            else:
                await _process_trade_exit(r, ltp, "SL_HIT", ep, pn)
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
