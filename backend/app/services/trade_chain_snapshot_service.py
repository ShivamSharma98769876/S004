"""Capture trimmed option chain context (ATM ±12 strikes) at entry, on a 30s cadence while open, and at exit."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db_client import execute, fetch, fetchrow
from app.services.option_chain_zerodha import fetch_option_chain_sync
from app.services.option_symbol_compact import parse_compact_option_symbol

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_LAST_PURGE_IST_DATE: date | None = None
_PURGE_LOCK = asyncio.Lock()
_sample_cycle_bg_task: asyncio.Task | None = None


def chain_snapshots_enabled() -> bool:
    return os.getenv("S004_CHAIN_SNAPSHOT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}


def strikes_each_side() -> int:
    try:
        return max(1, min(30, int(os.getenv("S004_CHAIN_SNAPSHOT_STRIKES_EACH_SIDE", "12"))))
    except ValueError:
        return 12


def retention_days() -> int:
    try:
        return max(1, min(730, int(os.getenv("S004_CHAIN_SNAPSHOT_RETENTION_DAYS", "30"))))
    except ValueError:
        return 30


def _compact_leg(leg: dict[str, Any] | None) -> dict[str, Any]:
    if not leg or not isinstance(leg, dict):
        return {}
    return {
        "ltp": leg.get("ltp"),
        "oi": leg.get("oi"),
        "iv": leg.get("iv"),
        "delta": leg.get("delta"),
    }


def trim_chain_around_strike(chain: list[dict[str, Any]], center: int, each_side: int) -> list[dict[str, Any]]:
    if not chain:
        return []
    strikes: list[int] = []
    for r in chain:
        try:
            strikes.append(int(r["strike"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not strikes:
        return chain[: 2 * each_side + 1]
    strikes_sorted = sorted(set(strikes))
    best_i = min(range(len(strikes_sorted)), key=lambda i: abs(strikes_sorted[i] - center))
    lo = max(0, best_i - each_side)
    hi = min(len(strikes_sorted), best_i + each_side + 1)
    allowed = set(strikes_sorted[lo:hi])
    return [r for r in chain if int(r.get("strike", 0)) in allowed]


def _center_strike(symbol: str, spot: float, chain: list[dict[str, Any]]) -> int:
    p = parse_compact_option_symbol(symbol)
    if p and p.get("strike"):
        return int(p["strike"])
    if spot > 0 and chain:
        best = None
        best_d = 1e18
        for r in chain:
            try:
                st = int(r["strike"])
            except (KeyError, TypeError, ValueError):
                continue
            d = abs(float(st) - spot)
            if d < best_d:
                best_d = d
                best = st
        if best is not None:
            return best
    return 0


def build_compact_chain_payload(full: dict[str, Any], symbol: str, each_side: int) -> dict[str, Any]:
    chain = full.get("chain") or []
    if not isinstance(chain, list):
        chain = []
    spot = float(full.get("spot") or 0)
    center = _center_strike(symbol, spot, chain)
    trimmed = trim_chain_around_strike(chain, center if center > 0 else spot, each_side)
    rows: list[dict[str, Any]] = []
    for r in trimmed:
        if not isinstance(r, dict):
            continue
        try:
            st = int(r["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "strike": st,
                "call": _compact_leg(r.get("call") if isinstance(r.get("call"), dict) else None),
                "put": _compact_leg(r.get("put") if isinstance(r.get("put"), dict) else None),
            }
        )
    return {
        "spot": full.get("spot"),
        "spotChgPct": full.get("spotChgPct"),
        "vix": full.get("vix"),
        "pcr": full.get("pcr"),
        "pcrVol": full.get("pcrVol"),
        "tradeStrike": center,
        "strikesWindowEachSide": each_side,
        "updated": full.get("updated"),
        "chain": rows,
    }


async def _resolve_kite(user_id: int, mode: str):
    from app.services.trades_service import _get_kite_for_any_user, get_kite_for_quotes

    k = await get_kite_for_quotes(user_id)
    if k:
        return k
    if str(mode or "").upper() == "PAPER":
        return await _get_kite_for_any_user()
    return None


async def capture_trade_chain_snapshot(
    *,
    trade_ref: str,
    user_id: int,
    recommendation_id: str,
    strategy_id: str,
    strategy_version: str,
    mode: str,
    symbol: str,
    instrument: str,
    expiry: str,
    phase: str,
) -> None:
    if not chain_snapshots_enabled():
        return
    phase = phase.strip().lower()
    if phase not in ("entry", "sample", "exit"):
        return
    inst = (instrument or "").strip().upper()
    exp = (expiry or "").strip()
    if not inst or not exp:
        return
    each = strikes_each_side()
    kite = await _resolve_kite(user_id, mode)
    if not kite:
        logger.debug("chain snapshot skipped (no kite) trade_ref=%s phase=%s", trade_ref, phase)
        return
    try:
        full = await asyncio.to_thread(
            fetch_option_chain_sync,
            kite,
            inst,
            exp,
            each,
            each,
            3,
            None,
        )
    except Exception:
        logger.warning(
            "chain snapshot fetch failed trade_ref=%s phase=%s instrument=%s",
            trade_ref,
            phase,
            inst,
            exc_info=True,
        )
        return
    payload = build_compact_chain_payload(full, symbol, each)
    try:
        await execute(
            """
            INSERT INTO s004_trade_chain_snapshots (
                trade_ref, recommendation_id, user_id, strategy_id, strategy_version, mode, phase, payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            trade_ref,
            recommendation_id,
            user_id,
            strategy_id,
            strategy_version,
            str(mode or "").upper(),
            phase,
            json.dumps(payload),
        )
    except Exception:
        logger.warning("chain snapshot insert failed trade_ref=%s phase=%s", trade_ref, phase, exc_info=True)


def schedule_entry_chain_snapshot(
    *,
    trade_ref: str,
    user_id: int,
    recommendation_id: str,
    strategy_id: str,
    strategy_version: str,
    mode: str,
    symbol: str,
    instrument: str,
    expiry: str,
) -> None:
    if not chain_snapshots_enabled():
        return

    async def _run() -> None:
        await capture_trade_chain_snapshot(
            trade_ref=trade_ref,
            user_id=user_id,
            recommendation_id=recommendation_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            mode=mode,
            symbol=symbol,
            instrument=instrument,
            expiry=expiry,
            phase="entry",
        )

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        asyncio.get_event_loop().create_task(_run())


async def schedule_exit_chain_snapshot(trade_ref: str, user_id: int) -> None:
    if not chain_snapshots_enabled():
        return
    row = await fetchrow(
        """
        SELECT t.trade_ref, t.user_id, t.recommendation_id, t.strategy_id, t.strategy_version,
               t.mode, t.symbol, r.instrument, r.expiry
        FROM s004_live_trades t
        JOIN s004_trade_recommendations r
          ON r.recommendation_id = t.recommendation_id AND r.user_id = t.user_id
        WHERE t.trade_ref = $1 AND t.user_id = $2
        """,
        trade_ref,
        user_id,
    )
    if not row:
        return
    await capture_trade_chain_snapshot(
        trade_ref=str(row["trade_ref"]),
        user_id=int(row["user_id"]),
        recommendation_id=str(row["recommendation_id"]),
        strategy_id=str(row["strategy_id"]),
        strategy_version=str(row["strategy_version"]),
        mode=str(row["mode"]),
        symbol=str(row["symbol"]),
        instrument=str(row["instrument"]),
        expiry=str(row["expiry"]),
        phase="exit",
    )


def fire_and_forget_exit_snapshot(trade_ref: str, user_id: int) -> None:
    if not chain_snapshots_enabled():
        return

    async def _run() -> None:
        try:
            await schedule_exit_chain_snapshot(trade_ref, user_id)
        except Exception:
            logger.debug("exit chain snapshot task failed", exc_info=True)

    try:
        asyncio.create_task(_run())
    except RuntimeError:
        asyncio.get_event_loop().create_task(_run())


async def purge_expired_chain_snapshots() -> None:
    days = retention_days()
    await execute(
        """
        DELETE FROM s004_trade_chain_snapshots
        WHERE captured_at < NOW() - $1::interval
        """,
        f"{int(days)} days",
    )


async def maybe_purge_chain_snapshots() -> None:
    global _LAST_PURGE_IST_DATE
    if not chain_snapshots_enabled():
        return
    today = datetime.now(_IST).date()
    async with _PURGE_LOCK:
        if _LAST_PURGE_IST_DATE == today:
            return
        try:
            await purge_expired_chain_snapshots()
        except Exception:
            logger.warning("chain snapshot purge failed", exc_info=True)
            return
        _LAST_PURGE_IST_DATE = today


async def run_chain_snapshot_sample_cycle() -> None:
    if not chain_snapshots_enabled():
        return
    rows = await fetch(
        """
        SELECT t.trade_ref, t.user_id, t.recommendation_id, t.strategy_id, t.strategy_version,
               t.mode, t.symbol, r.instrument, r.expiry
        FROM s004_live_trades t
        JOIN s004_trade_recommendations r
          ON r.recommendation_id = t.recommendation_id AND r.user_id = t.user_id
        WHERE t.current_state <> 'EXIT'
        """
    )
    if not rows:
        return
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        inst = str(r.get("instrument") or "").strip().upper()
        exp = str(r.get("expiry") or "").strip()
        if not inst or not exp:
            continue
        groups[(inst, exp)].append(dict(r))

    each = strikes_each_side()
    for (inst, exp), trades in groups.items():
        kite = None
        for t in trades:
            kite = await _resolve_kite(int(t["user_id"]), str(t.get("mode") or "PAPER"))
            if kite:
                break
        if not kite:
            continue
        try:
            full = await asyncio.to_thread(
                fetch_option_chain_sync,
                kite,
                inst,
                exp,
                each,
                each,
                3,
                None,
            )
        except Exception:
            logger.debug(
                "sample chain fetch failed instrument=%s expiry=%s",
                inst,
                exp,
                exc_info=True,
            )
            continue
        for t in trades:
            payload = build_compact_chain_payload(full, str(t.get("symbol") or ""), each)
            try:
                await execute(
                    """
                    INSERT INTO s004_trade_chain_snapshots (
                        trade_ref, recommendation_id, user_id, strategy_id, strategy_version, mode, phase, payload
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, 'sample', $7::jsonb)
                    """,
                    str(t["trade_ref"]),
                    str(t["recommendation_id"]),
                    int(t["user_id"]),
                    str(t["strategy_id"]),
                    str(t["strategy_version"]),
                    str(t.get("mode") or "PAPER").upper(),
                    json.dumps(payload),
                )
            except Exception:
                logger.debug(
                    "sample chain insert failed trade_ref=%s",
                    t.get("trade_ref"),
                    exc_info=True,
                )


def schedule_chain_snapshot_sample_cycle() -> None:
    """Run sample snapshot cycle in the background; skip if a previous run is still in progress.

    Avoids blocking the auto-execute loop for many minutes when many (instrument, expiry) groups exist.
    """
    global _sample_cycle_bg_task
    if not chain_snapshots_enabled():
        return
    if _sample_cycle_bg_task is not None and not _sample_cycle_bg_task.done():
        return

    async def _run() -> None:
        try:
            await run_chain_snapshot_sample_cycle()
        except Exception:
            logger.debug("chain snapshot sample cycle failed", exc_info=True)

    try:
        _sample_cycle_bg_task = asyncio.create_task(_run())
    except RuntimeError:
        _sample_cycle_bg_task = asyncio.get_event_loop().create_task(_run())
