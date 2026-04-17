from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.auth_context import get_user_id
from app.services.observability_service import build_observability_snapshot

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/snapshot")
async def observability_snapshot(
    user_id: int = Depends(get_user_id),
    refresh: bool = Query(False, description="Bypass short TTL cache when true."),
) -> dict[str, Any]:
    """Spot + indicator series for each active subscribed strategy (Phase 1). Subscribers only."""
    return await build_observability_snapshot(user_id, use_cache=not refresh)
