from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.db_client import ensure_user, execute, fetchrow
from app.api.auth_context import get_user_id, require_admin
from app.services.strategy_details_validator import validate_strategy_details
from app.services.trades_service import invalidate_recommendation_cache_for_strategy
from app.api.schemas import (
    CreateStrategyPayload,
    StrategyDetailsPayload,
    StrategyItemOut,
    SubscriptionPayload,
    SubscriptionResponse,
)
from app.services.marketplace_service import list_strategies_for_user, upsert_subscription

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class StrategyStatusPayload(BaseModel):
    publish_status: str


@router.get("/strategies")
async def list_strategies(
    user_id: int = Depends(get_user_id),
    risk: str | None = Query(default=None),
    status: str | None = Query(default=None),
    sort_by: str = Query(default="updated_at"),
    sort_dir: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[StrategyItemOut]:
    await ensure_user(user_id)
    rows = await list_strategies_for_user(
        user_id=user_id,
        risk=risk,
        status=status,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )
    return [StrategyItemOut(**r) for r in rows]


@router.post("/subscriptions")
async def update_subscription(
    payload: SubscriptionPayload,
    user_id: int = Depends(get_user_id),
) -> SubscriptionResponse:
    await ensure_user(user_id)
    action = payload.action.upper()
    mode = payload.mode.upper()
    if action not in {"SUBSCRIBE", "PAUSE", "RESUME", "STOP"}:
        raise HTTPException(status_code=400, detail="Invalid action.")
    if mode not in {"PAPER", "LIVE"}:
        raise HTTPException(status_code=400, detail="Invalid mode.")

    target_status = {
        "SUBSCRIBE": "ACTIVE",
        "PAUSE": "PAUSED",
        "RESUME": "ACTIVE",
        "STOP": "STOPPED",
    }[action]

    await upsert_subscription(
        user_id=user_id,
        strategy_id=payload.strategy_id,
        strategy_version=payload.strategy_version,
        mode=mode,
        status=target_status,
    )
    return SubscriptionResponse(status="ok", subscription_status=target_status)


@router.get("/strategies/{strategy_id}/{version}/details")
async def get_strategy_details(
    strategy_id: str,
    version: str,
    user_id: int = Depends(get_user_id),
) -> dict:
    await ensure_user(user_id)
    row = await fetchrow(
        """
        SELECT strategy_details_json FROM s004_strategy_catalog
        WHERE strategy_id = $1 AND version = $2
        """,
        strategy_id,
        version,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    raw = row.get("strategy_details_json")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
    return {}


@router.put("/strategies/{strategy_id}/{version}/details")
async def update_strategy_details(
    strategy_id: str,
    version: str,
    payload: StrategyDetailsPayload,
    user_id: int = Depends(require_admin),
) -> dict:
    await ensure_user(user_id)
    validation_errors = validate_strategy_details(payload.details)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail="Validation failed: " + "; ".join(validation_errors),
        )
    row = await fetchrow(
        """
        UPDATE s004_strategy_catalog
        SET strategy_details_json = $1::jsonb, updated_at = NOW()
        WHERE strategy_id = $2 AND version = $3
        RETURNING strategy_id, version
        """,
        json.dumps(payload.details),
        strategy_id,
        version,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    await invalidate_recommendation_cache_for_strategy(strategy_id, version)
    return {"status": "ok", "strategy_id": row["strategy_id"], "version": row["version"]}


@router.post("/strategies")
async def create_strategy(
    payload: CreateStrategyPayload,
    user_id: int = Depends(require_admin),
) -> dict:
    await ensure_user(user_id)
    details = payload.details or {}
    validation_errors = validate_strategy_details(details)
    if validation_errors:
        raise HTTPException(
            status_code=400,
            detail="Validation failed: " + "; ".join(validation_errors),
        )
    try:
        await execute(
            """
            INSERT INTO s004_strategy_catalog (
                strategy_id, version, display_name, description, risk_profile,
                owner_type, publish_status, execution_modes, supported_segments,
                performance_snapshot, strategy_details_json, created_by
            )
            SELECT $1, $2, $3, $4, $5, 'ADMIN', 'DRAFT',
                ARRAY['PAPER', 'LIVE'], ARRAY['NIFTY', 'BANKNIFTY', 'FINNIFTY'],
                '{}'::jsonb, $6::jsonb,
                COALESCE((SELECT id FROM s004_users WHERE role = 'ADMIN' LIMIT 1), $7)
            ON CONFLICT (strategy_id, version) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                description = EXCLUDED.description,
                risk_profile = EXCLUDED.risk_profile,
                strategy_details_json = COALESCE(EXCLUDED.strategy_details_json, s004_strategy_catalog.strategy_details_json),
                updated_at = NOW()
            """,
            payload.strategy_id,
            payload.version,
            payload.display_name,
            payload.description,
            payload.risk_profile,
            json.dumps(payload.details or {}),
            user_id,
        )
        return {"status": "ok", "strategy_id": payload.strategy_id, "version": payload.version}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/strategies/{strategy_id}/{version}/status")
async def set_strategy_publish_status(
    strategy_id: str,
    version: str,
    payload: StrategyStatusPayload,
    user_id: int = Depends(require_admin),
) -> dict:
    status = payload.publish_status.upper()
    if status not in {"DRAFT", "PUBLISHED", "ARCHIVED"}:
        raise HTTPException(status_code=400, detail="Invalid publish_status.")
    row = await fetchrow(
        """
        UPDATE s004_strategy_catalog
        SET publish_status = $1, updated_at = NOW()
        WHERE strategy_id = $2 AND version = $3
        RETURNING strategy_id, version, publish_status
        """,
        status,
        strategy_id,
        version,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return dict(row)

