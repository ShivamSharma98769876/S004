from __future__ import annotations

import asyncio
from datetime import datetime, time
import json
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from kiteconnect import KiteConnect
from pydantic import BaseModel

from app.db_client import ensure_user, execute, fetch, fetchrow
from app.api.auth_context import get_user_id
from app.api.schemas import SettingsPayload, SettingsResponse

router = APIRouter(prefix="/settings", tags=["settings"])


def _parse_time(val: str | None) -> time:
    """Parse 'HH:MM' or 'H:MM' string to datetime.time for asyncpg TIME columns."""
    if isinstance(val, time):
        return val
    s = str(val or "09:00").strip()
    parts = s.split(":")
    h = int(parts[0]) if parts and parts[0].isdigit() else 9
    m = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return time(max(0, min(23, h)), max(0, min(59, m)))

# Fallback only when strategy_details not in catalog; preferred path is strategy JSON from s004_strategy_catalog.
DEFAULT_STRATEGY_DETAILS = {
    "displayName": "TrendSnap Momentum",
    "description": "Four-factor option read on the latest candle: close above VWAP (required gate), EMA9 above EMA21, RSI >= 50 (no upper cap), volume above 1.0x average. Signal when at least three of four pass. Exits use SL, target, and breakeven from Settings.",
    "includeEmaCrossoverInScore": False,
    "strictBullishComparisons": True,
    "longPremiumVwapMarginPct": 2.0,
    "longPremiumEmaMarginPct": 1.0,
    "indicators": {
        "ema": {"fast": 9, "slow": 21, "description": "EMA9 strictly above EMA21 adds one point."},
        "emaCrossover": {"bonus": 0, "maxCandlesSinceCross": 10, "description": "Not counted in score; metadata only."},
        "rsi": {"period": 14, "min": 50, "max": 100, "description": "RSI at or above 50 adds one point (upper band relaxed)."},
        "vwap": {"description": "Latest candle close strictly above VWAP is the primary gate and first point."},
        "volumeSpike": {"minRatio": 1.0, "description": "Volume strictly above 1.0x recent average adds one point."},
        "ivr": {"maxThreshold": 20, "bonus": 0, "description": "IVR for reference; no score bonus."},
    },
    "strikeSelection": {
        "minOi": 5000,
        "minVolume": 300,
        "maxOtmSteps": 3,
        "deltaPreferredCE": 0.45,
        "deltaPreferredPE": -0.45,
        "description": "Liquidity: min OI 5k, min volume 300. Max 3 steps OTM. Prefer delta near 0.35 CE / -0.35 PE.",
    },
    "scoreThreshold": 3,
    "scoreMax": 4,
    "autoTradeScoreThreshold": 4,
    "positionIntent": "long_premium",
    "scoreDescription": "Primary: close must be above VWAP. Score 0-4: VWAP, EMA9>EMA21, RSI >= 50, volume>1.0x avg. No crossover or IVR points. BUY CE/PE when score >= 3.",
}


def _deep_merge_strategy_details(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_strategy_details(out[k], v)  # type: ignore[index]
        else:
            out[k] = v
    return out


def _parse_strategy_details(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


class ZerodhaConnectPayload(BaseModel):
    apiKey: str | None = None
    apiSecret: str | None = None
    requestToken: str | None = None
    accessToken: str | None = None


def _credentials_from_row(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


@router.get("/strategy-options")
async def get_strategy_options(user_id: int = Depends(get_user_id)) -> list[dict[str, Any]]:
    """Return strategies the user has ACTIVE subscriptions to (for Settings strategy dropdown)."""
    await ensure_user(user_id)
    rows = await fetch(
        """
        SELECT s.strategy_id, s.strategy_version AS version, c.display_name
        FROM s004_strategy_subscriptions s
        JOIN s004_strategy_catalog c ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
        WHERE s.user_id = $1 AND s.status = 'ACTIVE'
        ORDER BY c.display_name
        """,
        user_id,
    )
    return [
        {"strategy_id": r["strategy_id"], "version": r["version"], "display_name": r["display_name"]}
        for r in rows
    ]


@router.get("")
async def get_settings(
    user_id: int = Depends(get_user_id),
    strategy_id: str | None = Query(None, description="Load settings for this strategy"),
    strategy_version: str | None = Query(None, description="Strategy version (default 1.0.0)"),
) -> SettingsResponse:
    await ensure_user(user_id)

    master = await fetchrow(
        """
        SELECT * FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )

    sid = strategy_id or None
    ver = (strategy_version or "1.0.0") if sid else None

    if sid:
        strategy = await fetchrow(
            """
            SELECT * FROM s004_user_strategy_settings
            WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
            """,
            user_id,
            sid,
            ver,
        )
        if strategy is None:
            cfg = await fetchrow(
                """
                SELECT config_json FROM s004_strategy_config_versions
                WHERE strategy_id = $1 AND strategy_version = $2 AND active = TRUE
                ORDER BY config_version DESC LIMIT 1
                """,
                sid,
                ver,
            )
            raw = cfg.get("config_json") if cfg else None
            default_json = raw if isinstance(raw, dict) else {}
            def_val = lambda k, d: default_json.get(k, d)
            await execute(
                """
                INSERT INTO s004_user_strategy_settings (
                    user_id, strategy_id, strategy_version, lots, lot_size, banknifty_lot_size, max_strike_distance_atm,
                    max_premium, min_premium, min_entry_strength_pct, sl_type, sl_points,
                    breakeven_trigger_pct, target_points, trailing_sl_points, timeframe,
                    trade_start, trade_end, enabled_indices, auto_pause_after_losses, updated_at
                )
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::time,$18::time,$19,$20,NOW())
                ON CONFLICT (user_id, strategy_id, strategy_version) DO NOTHING
                """,
                user_id,
                sid,
                ver,
                int(def_val("lots", 1)),
                int(def_val("lot_size", 65)),
                int(def_val("banknifty_lot_size", 30)),
                int(def_val("max_strike_distance_atm", 5)),
                float(def_val("max_premium", 200)),
                float(def_val("min_premium", 30)),
                float(def_val("min_entry_strength_pct", 0)),
                str(def_val("sl_type", "Fixed Points")),
                float(def_val("sl_points", 15)),
                float(def_val("breakeven_trigger_pct", 50)),
                float(def_val("target_points", 10)),
                float(def_val("trailing_sl_points", 20)),
                str(def_val("timeframe", "3-min")),
                "09:15",
                "15:00",
                ["NIFTY"],
                int(def_val("auto_pause_after_losses", 3)),
            )
            strategy = await fetchrow(
                """
                SELECT * FROM s004_user_strategy_settings
                WHERE user_id = $1 AND strategy_id = $2 AND strategy_version = $3
                """,
                user_id,
                sid,
                ver,
            )
    else:
        strategy = await fetchrow(
            """
            SELECT s.* FROM s004_user_strategy_settings s
            JOIN s004_strategy_subscriptions sub
                ON sub.user_id = s.user_id AND sub.strategy_id = s.strategy_id AND sub.strategy_version = s.strategy_version
            WHERE s.user_id = $1 AND sub.status = 'ACTIVE'
            ORDER BY s.updated_at DESC
            LIMIT 1
            """,
            user_id,
        )
        if strategy is None:
            strategy = await fetchrow(
                """
                SELECT * FROM s004_user_strategy_settings
                WHERE user_id = $1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                user_id,
            )

    if master is None:
        raise HTTPException(status_code=404, detail="Master settings not found.")
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy settings not found.")

    cred = _credentials_from_row(master["credentials_json"])

    # Effective strategy_details = default + catalog + user override (user wins).
    catalog_details: dict[str, Any] = {}
    user_details: dict[str, Any] = {}
    catalog_row = await fetchrow(
        """
        SELECT strategy_details_json FROM s004_strategy_catalog
        WHERE strategy_id = $1 AND version = $2
        """,
        strategy["strategy_id"],
        strategy["strategy_version"],
    )
    if catalog_row:
        catalog_details = _parse_strategy_details(catalog_row.get("strategy_details_json"))
    user_details = _parse_strategy_details(strategy.get("strategy_details_json"))
    strategy_details = _deep_merge_strategy_details(DEFAULT_STRATEGY_DETAILS, catalog_details)
    strategy_details = _deep_merge_strategy_details(strategy_details, user_details)

    # Non-admins cannot keep LIVE in DB without approved_live (avoids UI/API mismatch).
    appr = await fetchrow(
        "SELECT approved_paper, approved_live, role FROM s004_users WHERE id = $1",
        user_id,
    )
    stored_mode = str(master["mode"] or "PAPER").upper()
    effective_mode = stored_mode
    if appr and str(appr.get("role", "")).upper() != "ADMIN":
        if effective_mode == "LIVE" and not appr.get("approved_live"):
            effective_mode = "PAPER"
    if effective_mode != stored_mode:
        await execute(
            """
            UPDATE s004_user_master_settings
            SET mode = $2, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            effective_mode,
        )

    from app.services.broker_accounts import get_active_broker_code

    active_broker = await get_active_broker_code(user_id)
    return SettingsResponse(
        master={
            "goLive": master["go_live"],
            "engineRunning": master["engine_running"],
            "brokerConnected": master["broker_connected"],
            "sharedApiConnected": master["shared_api_connected"],
            "platformApiOnline": master["platform_api_online"],
            "mode": effective_mode,
            "maxTrades": master["max_parallel_trades"],
            "dailyLossLimit": float(master["max_loss_day"]),
            "activeBroker": active_broker,
        },
        credentials={
            "apiKey": cred.get("apiKey", ""),
            "apiSecret": cred.get("apiSecret", ""),
            "userId": cred.get("userId", ""),
            "password": cred.get("password", ""),
            "totpSecret": cred.get("totpSecret", ""),
            "requestToken": cred.get("requestToken", ""),
            "accessToken": cred.get("accessToken", ""),
        },
        capitalRisk={
            "initialCapital": float(master["initial_capital"]),
            "maxInvestmentPerTrade": float(master["max_investment_per_trade"]),
            "maxProfitDay": float(master["max_profit_day"]),
            "maxLossDay": float(master["max_loss_day"]),
            "maxTradesDay": int(master["max_trades_day"]),
            "maxParallelTrades": int(master["max_parallel_trades"]),
            "chargesPerTrade": float(master.get("charges_per_trade") or 20),
        },
        tradingParameters={
            "lots": int(strategy["lots"]),
            "lotSize": int(strategy["lot_size"]),
            "bankniftyLotSize": int(strategy.get("banknifty_lot_size") or 30),
            "maxStrikeDistanceFromAtm": int(strategy["max_strike_distance_atm"]),
            "maxPremium": float(strategy["max_premium"]),
            "minPremium": float(strategy["min_premium"]),
            "minEntryStrengthPct": float(strategy["min_entry_strength_pct"]),
            "slType": strategy["sl_type"],
            "slPoints": float(strategy["sl_points"]),
            "breakevenTriggerPct": float(strategy["breakeven_trigger_pct"]),
            "targetPoints": float(strategy["target_points"]),
            "trailingSlPoints": float(strategy["trailing_sl_points"]),
        },
        strategy={
            "strategyName": strategy["strategy_id"],
            "strategyVersion": strategy["strategy_version"],
            "timeframe": strategy["timeframe"],
            "indices": {
                "NIFTY": "NIFTY" in strategy["enabled_indices"],
                "BANKNIFTY": "BANKNIFTY" in strategy["enabled_indices"],
                "FINNIFTY": "FINNIFTY" in strategy["enabled_indices"],
                "MIDCPNIFTY": "MIDCPNIFTY" in strategy["enabled_indices"],
            },
            "tradeStart": str(strategy["trade_start"])[:5],
            "tradeEnd": str(strategy["trade_end"])[:5],
            "autoPauseAfterLosses": int(strategy["auto_pause_after_losses"]),
            "details": strategy_details,
            "fromSettings": {
                "timeframe": strategy["timeframe"],
                "targetPoints": float(strategy["target_points"]),
                "slPoints": float(strategy["sl_points"]),
                "trailingSlPoints": float(strategy["trailing_sl_points"]),
            },
        },
        updatedAt=datetime.utcnow().isoformat() + "Z",
    )


@router.post("/zerodha/connect")
async def connect_zerodha(
    payload: ZerodhaConnectPayload,
    request: Request,
    user_id: int = Depends(get_user_id),
) -> dict[str, Any]:
    await ensure_user(user_id)
    master = await fetchrow(
        """
        SELECT credentials_json FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    if master is None:
        raise HTTPException(status_code=404, detail="Master settings not found.")

    cred = _credentials_from_row(master["credentials_json"])
    api_key = (payload.apiKey or str(cred.get("apiKey", ""))).strip()
    api_secret = (payload.apiSecret or str(cred.get("apiSecret", ""))).strip()
    request_token = (payload.requestToken or "").strip() or str(cred.get("requestToken", "")).strip()
    access_token = (payload.accessToken or "").strip()

    if not api_key or not api_secret:
        raise HTTPException(status_code=400, detail="API Key and API Secret are required.")
    if not request_token and not access_token:
        raise HTTPException(status_code=400, detail="Provide request token or access token.")

    kite = KiteConnect(api_key=api_key)
    generated = False
    try:
        if not access_token:
            session_data = await asyncio.to_thread(kite.generate_session, request_token, api_secret=api_secret)
            access_token = str(session_data.get("access_token", "")).strip()
            generated = True
        kite.set_access_token(access_token)
        profile = await asyncio.to_thread(kite.profile)
    except Exception as exc:
        await execute(
            """
            UPDATE s004_user_master_settings
            SET broker_connected = FALSE, updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
        )
        err_msg = str(exc)
        if "api_key" in err_msg.lower() or "access_token" in err_msg.lower():
            hint = (
                "Access tokens expire daily. If using Access Token: get a fresh one from Kite login. "
                "If using Request Token: ensure it is new (one-time use) and from the same Kite app as your API key."
            )
            raise HTTPException(status_code=400, detail=f"Kite connect failed: {exc}. {hint}")
        raise HTTPException(status_code=400, detail=f"Kite connect failed: {exc}")

    cred["apiKey"] = api_key
    cred["apiSecret"] = api_secret
    cred["requestToken"] = request_token
    cred["accessToken"] = access_token
    from app.services import broker_accounts as ba

    client_ip = request.client.host if request.client else None
    await ba.log_broker_audit(
        actor_user_id=user_id,
        subject_user_id=user_id,
        broker_code=ba.BROKER_ZERODHA,
        action="CONNECT",
        client_ip=client_ip,
        meta={"generated_access_token": generated},
    )
    if ba.fernet_key_configured():
        vault = await ba.load_merged_vault(user_id)
        vault[ba.BROKER_ZERODHA] = {
            "apiKey": api_key,
            "apiSecret": api_secret,
            "accessToken": access_token,
        }
        await ba.save_user_vault(
            user_id,
            vault,
            active_broker=ba.BROKER_ZERODHA,
            broker_connected=True,
            sync_credentials_json=True,
        )
    else:
        await execute(
            """
            UPDATE s004_user_master_settings
            SET broker_connected = TRUE,
                credentials_json = $2::jsonb,
                active_broker_code = 'zerodha',
                updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            json.dumps(cred),
        )
    return {
        "status": "connected",
        "brokerConnected": True,
        "generatedAccessToken": generated,
        "accessToken": access_token,
        "profile": profile,
    }


@router.post("/zerodha/disconnect")
async def disconnect_zerodha(
    request: Request,
    user_id: int = Depends(get_user_id),
) -> dict[str, Any]:
    await ensure_user(user_id)
    master = await fetchrow(
        """
        SELECT credentials_json FROM s004_user_master_settings
        WHERE user_id = $1
        """,
        user_id,
    )
    if master is None:
        raise HTTPException(status_code=404, detail="Master settings not found.")

    from app.services import broker_accounts as ba

    cred = _credentials_from_row(master["credentials_json"])
    cred["accessToken"] = ""
    row_prev = await fetchrow(
        "SELECT active_broker_code FROM s004_user_master_settings WHERE user_id = $1",
        user_id,
    )
    prev_active = str(row_prev.get("active_broker_code") or "").strip().lower() if row_prev else ""
    vault = await ba.load_merged_vault(user_id)
    if ba.BROKER_ZERODHA in vault:
        vault[ba.BROKER_ZERODHA] = {"apiKey": "", "apiSecret": "", "accessToken": ""}
    f = vault.get(ba.BROKER_FYERS) if isinstance(vault.get(ba.BROKER_FYERS), dict) else {}
    has_f = bool(str(f.get("accessToken") or "").strip())
    if prev_active == ba.BROKER_ZERODHA:
        next_active = ba.BROKER_FYERS if has_f else None
    else:
        next_active = prev_active or None
    client_ip = request.client.host if request.client else None
    await ba.log_broker_audit(
        actor_user_id=user_id,
        subject_user_id=user_id,
        broker_code=ba.BROKER_ZERODHA,
        action="DISCONNECT",
        client_ip=client_ip,
        meta={},
    )
    if ba.fernet_key_configured():
        await ba.save_user_vault(
            user_id,
            vault,
            active_broker=next_active,
            broker_connected=has_f,
            sync_credentials_json=True,
        )
    else:
        await execute(
            """
            UPDATE s004_user_master_settings
            SET broker_connected = $3,
                credentials_json = $2::jsonb,
                active_broker_code = $4,
                updated_at = NOW()
            WHERE user_id = $1
            """,
            user_id,
            json.dumps(cred),
            has_f,
            next_active,
        )
    return {"status": "disconnected", "brokerConnected": has_f, "activeBroker": next_active}


@router.put("")
async def upsert_settings(payload: SettingsPayload, user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    await ensure_user(user_id)
    from app.api.auth_context import check_mode_approval
    await check_mode_approval(user_id, str(payload.master.get("mode", "PAPER")))

    raw = payload.strategy.get("indices", payload.strategy.get("enabled_indices", {}))
    if isinstance(raw, dict):
        indices = [k for k, v in raw.items() if v]
    elif isinstance(raw, (list, tuple)):
        indices = [str(x) for x in raw]
    elif isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            indices = [str(x) for x in parsed] if isinstance(parsed, (list, tuple)) else []
        except json.JSONDecodeError:
            indices = []
    else:
        indices = []
    if not indices:
        indices = ["NIFTY"]

    cr = payload.capitalRisk if isinstance(payload.capitalRisk, dict) else {}
    try:
        row_master = await fetchrow(
            """
            SELECT max_parallel_trades, max_trades_day, max_profit_day, max_loss_day,
                   initial_capital, max_investment_per_trade,
                   COALESCE(charges_per_trade, 20) AS charges_per_trade
            FROM s004_user_master_settings
            WHERE user_id = $1
            """,
            user_id,
        )
    except asyncpg.UndefinedColumnError:
        row_master = await fetchrow(
            """
            SELECT max_parallel_trades, max_trades_day, max_profit_day, max_loss_day,
                   initial_capital, max_investment_per_trade
            FROM s004_user_master_settings
            WHERE user_id = $1
            """,
            user_id,
        )

    def _cap_int(key: str, col: str, default: int) -> int:
        if key in cr and cr[key] is not None:
            return int(cr[key])
        if row_master and row_master.get(col) is not None:
            return int(row_master[col])
        return default

    def _cap_float(key: str, col: str, default: float) -> float:
        if key in cr and cr[key] is not None:
            return float(cr[key])
        if row_master and row_master.get(col) is not None:
            return float(row_master[col])
        return default

    max_parallel_trades = _cap_int(
        "maxParallelTrades",
        "max_parallel_trades",
        int(payload.master.get("maxTrades", 3)),
    )
    max_trades_day = _cap_int("maxTradesDay", "max_trades_day", 4)
    max_profit_day = _cap_float("maxProfitDay", "max_profit_day", 5000.0)
    max_loss_day = _cap_float(
        "maxLossDay",
        "max_loss_day",
        float(payload.master.get("dailyLossLimit", 2000)),
    )
    initial_capital = _cap_float("initialCapital", "initial_capital", 100000.0)
    max_investment_per_trade = _cap_float("maxInvestmentPerTrade", "max_investment_per_trade", 50000.0)
    charges_per_trade = _cap_float("chargesPerTrade", "charges_per_trade", 20.0)

    try:
        await execute(
            """
            INSERT INTO s004_user_master_settings (
                user_id, go_live, engine_running, broker_connected, shared_api_connected, platform_api_online,
                mode, max_parallel_trades, max_trades_day, max_profit_day, max_loss_day, initial_capital,
                max_investment_per_trade, charges_per_trade, credentials_json, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                go_live = EXCLUDED.go_live,
                engine_running = EXCLUDED.engine_running,
                broker_connected = EXCLUDED.broker_connected,
                shared_api_connected = EXCLUDED.shared_api_connected,
                platform_api_online = EXCLUDED.platform_api_online,
                mode = EXCLUDED.mode,
                max_parallel_trades = EXCLUDED.max_parallel_trades,
                max_trades_day = EXCLUDED.max_trades_day,
                max_profit_day = EXCLUDED.max_profit_day,
                max_loss_day = EXCLUDED.max_loss_day,
                initial_capital = EXCLUDED.initial_capital,
                max_investment_per_trade = EXCLUDED.max_investment_per_trade,
                charges_per_trade = EXCLUDED.charges_per_trade,
                credentials_json = EXCLUDED.credentials_json,
                updated_at = NOW()
            """,
            user_id,
            bool(payload.master.get("goLive", False)),
            bool(payload.master.get("engineRunning", False)),
            bool(payload.master.get("brokerConnected", False)),
            bool(payload.master.get("sharedApiConnected", True)),
            bool(payload.master.get("platformApiOnline", True)),
            str(payload.master.get("mode", "PAPER")),
            max_parallel_trades,
            max_trades_day,
            max_profit_day,
            max_loss_day,
            initial_capital,
            max_investment_per_trade,
            charges_per_trade,
            json.dumps(payload.credentials),
        )
    except asyncpg.UndefinedColumnError:
        await execute(
            """
            INSERT INTO s004_user_master_settings (
                user_id, go_live, engine_running, broker_connected, shared_api_connected, platform_api_online,
                mode, max_parallel_trades, max_trades_day, max_profit_day, max_loss_day, initial_capital,
                max_investment_per_trade, credentials_json, updated_at
            )
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                go_live = EXCLUDED.go_live,
                engine_running = EXCLUDED.engine_running,
                broker_connected = EXCLUDED.broker_connected,
                shared_api_connected = EXCLUDED.shared_api_connected,
                platform_api_online = EXCLUDED.platform_api_online,
                mode = EXCLUDED.mode,
                max_parallel_trades = EXCLUDED.max_parallel_trades,
                max_trades_day = EXCLUDED.max_trades_day,
                max_profit_day = EXCLUDED.max_profit_day,
                max_loss_day = EXCLUDED.max_loss_day,
                initial_capital = EXCLUDED.initial_capital,
                max_investment_per_trade = EXCLUDED.max_investment_per_trade,
                credentials_json = EXCLUDED.credentials_json,
                updated_at = NOW()
            """,
            user_id,
            bool(payload.master.get("goLive", False)),
            bool(payload.master.get("engineRunning", False)),
            bool(payload.master.get("brokerConnected", False)),
            bool(payload.master.get("sharedApiConnected", True)),
            bool(payload.master.get("platformApiOnline", True)),
            str(payload.master.get("mode", "PAPER")),
            max_parallel_trades,
            max_trades_day,
            max_profit_day,
            max_loss_day,
            initial_capital,
            max_investment_per_trade,
            json.dumps(payload.credentials),
        )

    await execute(
        """
        INSERT INTO s004_user_strategy_settings (
            user_id, strategy_id, strategy_version, lots, lot_size, banknifty_lot_size, max_strike_distance_atm,
            max_premium, min_premium, min_entry_strength_pct, sl_type, sl_points,
            breakeven_trigger_pct, target_points, trailing_sl_points, timeframe,
            trade_start, trade_end, enabled_indices, auto_pause_after_losses, updated_at
        )
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17::time,$18::time,$19,$20,NOW())
        ON CONFLICT (user_id, strategy_id, strategy_version) DO UPDATE SET
            lots = EXCLUDED.lots,
            lot_size = EXCLUDED.lot_size,
            banknifty_lot_size = EXCLUDED.banknifty_lot_size,
            max_strike_distance_atm = EXCLUDED.max_strike_distance_atm,
            max_premium = EXCLUDED.max_premium,
            min_premium = EXCLUDED.min_premium,
            min_entry_strength_pct = EXCLUDED.min_entry_strength_pct,
            sl_type = EXCLUDED.sl_type,
            sl_points = EXCLUDED.sl_points,
            breakeven_trigger_pct = EXCLUDED.breakeven_trigger_pct,
            target_points = EXCLUDED.target_points,
            trailing_sl_points = EXCLUDED.trailing_sl_points,
            timeframe = EXCLUDED.timeframe,
            trade_start = EXCLUDED.trade_start,
            trade_end = EXCLUDED.trade_end,
            enabled_indices = EXCLUDED.enabled_indices,
            auto_pause_after_losses = EXCLUDED.auto_pause_after_losses,
            updated_at = NOW()
        """,
        user_id,
        str(payload.strategy.get("strategyName", "strat-trendsnap-momentum")),
        str(payload.strategy.get("strategyVersion", "1.0.0")),
        int(payload.tradingParameters.get("lots", 1)),
        int(payload.tradingParameters.get("lotSize", 65)),
        int(payload.tradingParameters.get("bankniftyLotSize", 30)),
        int(payload.tradingParameters.get("maxStrikeDistanceFromAtm", 5)),
        float(payload.tradingParameters.get("maxPremium", 200)),
        float(payload.tradingParameters.get("minPremium", 30)),
        float(payload.tradingParameters.get("minEntryStrengthPct", 0)),
        str(payload.tradingParameters.get("slType", "Fixed Points")),
        float(payload.tradingParameters.get("slPoints", 15)),
        float(payload.tradingParameters.get("breakevenTriggerPct", 50)),
        float(payload.tradingParameters.get("targetPoints", 10)),
        float(payload.tradingParameters.get("trailingSlPoints", 20)),
        str(payload.strategy.get("timeframe", "3-min")),
        _parse_time(payload.strategy.get("tradeStart", "09:15")),
        _parse_time(payload.strategy.get("tradeEnd", "15:00")),
        indices,
        int(payload.strategy.get("autoPauseAfterLosses", 3)),
    )

    details = payload.strategy.get("details")
    if details is not None and isinstance(details, dict):
        try:
            await execute(
                """
                UPDATE s004_user_strategy_settings
                SET strategy_details_json = $1::jsonb, updated_at = NOW()
                WHERE user_id = $2 AND strategy_id = $3 AND strategy_version = $4
                """,
                json.dumps(details),
                user_id,
                str(payload.strategy.get("strategyName", "strat-trendsnap-momentum")),
                str(payload.strategy.get("strategyVersion", "1.0.0")),
            )
        except asyncpg.UndefinedColumnError:
            pass

    from app.services.trades_service import invalidate_recommendation_cache
    invalidate_recommendation_cache(user_id)

    return {"status": "ok"}
