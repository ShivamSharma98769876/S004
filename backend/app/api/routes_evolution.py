"""Admin Evolution API: daily metrics, recommendations, approve → new catalog version + changelog."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.auth_context import require_admin
from app.db_client import execute, fetchrow
from app.services.evolution_service import (
    fetch_daily_metrics_series,
    fetch_strategy_evaluation_summary,
    generate_rule_based_recommendations,
    list_catalog_strategy_ids,
    list_catalog_versions,
    list_changelog,
    list_recommendations,
    recompute_daily_metrics,
    shallow_merge_details,
    suggest_next_catalog_version,
)
from app.services.strategy_details_validator import validate_strategy_details
from app.services.trades_service import invalidate_recommendation_cache_for_strategy

router = APIRouter(prefix="/admin/evolution", tags=["evolution"])


class RecomputePayload(BaseModel):
    strategy_id: str | None = None
    from_date: date | None = None
    to_date: date | None = None


class ApproveRecommendationPayload(BaseModel):
    new_version: str | None = Field(
        default=None,
        description="Catalog version for the new row; defaults to semver patch bump when possible.",
    )
    changelog_md: str | None = None


@router.get("/strategies")
async def evolution_strategies(_admin_id: int = Depends(require_admin)) -> dict[str, Any]:
    ids = await list_catalog_strategy_ids()
    return {"strategy_ids": ids}


@router.get("/strategies/{strategy_id}/versions")
async def evolution_versions(
    strategy_id: str,
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    versions = await list_catalog_versions(strategy_id)
    return {"strategy_id": strategy_id, "versions": versions}


@router.get("/evaluation-summary")
async def evolution_evaluation_summary(
    strategy_id: str = Query(...),
    strategy_version: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    """Daily rollups + aggregates for finetuning thresholds (uses s004_strategy_daily_metrics)."""
    return await fetch_strategy_evaluation_summary(strategy_id, strategy_version, days=days)


@router.get("/daily-metrics")
async def evolution_daily_metrics(
    strategy_id: str = Query(...),
    strategy_version: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    series = await fetch_daily_metrics_series(strategy_id, strategy_version, from_date, to_date)
    cumulative = 0.0
    enriched: list[dict[str, Any]] = []
    for row in series:
        pnl = float(row.get("realized_pnl") or 0)
        cumulative += pnl
        r = dict(row)
        r["cumulative_realized_pnl"] = round(cumulative, 4)
        if isinstance(r.get("trade_date_ist"), date):
            r["trade_date_ist"] = r["trade_date_ist"].isoformat()
        if r.get("computed_at"):
            r["computed_at"] = r["computed_at"].isoformat()
        enriched.append(r)
    return {"strategy_id": strategy_id, "series": enriched}


@router.post("/recompute-daily-metrics")
async def evolution_recompute(
    payload: RecomputePayload,
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    n = await recompute_daily_metrics(payload.strategy_id, payload.from_date, payload.to_date)
    return {"status": "ok", "rows_touched": n}


@router.get("/recommendations")
async def evolution_list_recommendations(
    strategy_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    rows = await list_recommendations(strategy_id, status, limit)
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
        if r.get("updated_at"):
            r["updated_at"] = r["updated_at"].isoformat()
        if r.get("approved_at"):
            r["approved_at"] = r["approved_at"].isoformat()
    return {"recommendations": rows}


@router.post("/recommendations/generate")
async def evolution_generate_recommendations(
    strategy_id: str | None = Query(default=None),
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    new_ids = await generate_rule_based_recommendations(strategy_id)
    return {"status": "ok", "new_recommendation_ids": new_ids}


@router.get("/changelog")
async def evolution_changelog(
    strategy_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    rows = await list_changelog(strategy_id, limit)
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    return {"changelog": rows}


@router.post("/recommendations/{recommendation_id}/reject")
async def evolution_reject_recommendation(
    recommendation_id: int,
    _admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    row = await fetchrow(
        """
        UPDATE s004_strategy_evolution_recommendations
        SET status = 'REJECTED', updated_at = NOW()
        WHERE id = $1 AND status = 'PENDING_REVIEW'
        RETURNING id
        """,
        recommendation_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Recommendation not found or not pending.")
    return {"status": "ok", "id": recommendation_id}


@router.post("/recommendations/{recommendation_id}/approve")
async def evolution_approve_recommendation(
    recommendation_id: int,
    payload: ApproveRecommendationPayload,
    admin_id: int = Depends(require_admin),
) -> dict[str, Any]:
    rec = await fetchrow(
        """
        SELECT id, strategy_id, from_version, proposed_details_patch, proposed_title, recommendation_code, status
        FROM s004_strategy_evolution_recommendations
        WHERE id = $1
        """,
        recommendation_id,
    )
    if rec is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    if rec["status"] != "PENDING_REVIEW":
        raise HTTPException(status_code=400, detail="Only PENDING_REVIEW recommendations can be approved.")

    cat = await fetchrow(
        """
        SELECT strategy_id, version, display_name, description, risk_profile, owner_type, publish_status,
               execution_modes, supported_segments, performance_snapshot, strategy_details_json, created_by
        FROM s004_strategy_catalog
        WHERE strategy_id = $1 AND version = $2
        """,
        rec["strategy_id"],
        rec["from_version"],
    )
    if cat is None:
        raise HTTPException(status_code=404, detail="Source catalog version not found.")

    base_details: dict[str, Any] = {}
    raw_details = cat.get("strategy_details_json")
    if isinstance(raw_details, str):
        try:
            base_details = json.loads(raw_details)
        except json.JSONDecodeError:
            base_details = {}
    elif isinstance(raw_details, dict):
        base_details = dict(raw_details)

    patch = rec.get("proposed_details_patch") or {}
    if isinstance(patch, str):
        try:
            patch = json.loads(patch)
        except json.JSONDecodeError:
            patch = {}
    if not isinstance(patch, dict):
        patch = {}

    merged = shallow_merge_details(base_details, patch)
    errors = validate_strategy_details(merged)
    if errors:
        raise HTTPException(
            status_code=400,
            detail="Merged strategy details failed validation: " + "; ".join(errors),
        )

    new_ver = (payload.new_version or "").strip() or suggest_next_catalog_version(cat["version"])
    exists = await fetchrow(
        "SELECT 1 FROM s004_strategy_catalog WHERE strategy_id = $1 AND version = $2",
        cat["strategy_id"],
        new_ver,
    )
    if exists:
        raise HTTPException(status_code=409, detail=f"Version {new_ver} already exists for this strategy.")

    snap = cat.get("performance_snapshot")
    if isinstance(snap, dict):
        snap_json = json.dumps(snap)
    elif isinstance(snap, str):
        snap_json = snap
    else:
        snap_json = "{}"

    exec_modes = cat.get("execution_modes")
    sup_seg = cat.get("supported_segments")
    if exec_modes is None:
        exec_modes = ["PAPER", "LIVE"]
    if sup_seg is None:
        sup_seg = ["NIFTY", "BANKNIFTY", "FINNIFTY"]

    await execute(
        """
        INSERT INTO s004_strategy_catalog (
            strategy_id, version, display_name, description, risk_profile,
            owner_type, publish_status, execution_modes, supported_segments,
            performance_snapshot, strategy_details_json, created_by
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, 'DRAFT', $7::text[], $8::text[],
            $9::jsonb, $10::jsonb, $11
        )
        """,
        cat["strategy_id"],
        new_ver,
        cat["display_name"],
        cat["description"],
        cat["risk_profile"],
        cat["owner_type"],
        exec_modes,
        sup_seg,
        snap_json,
        json.dumps(merged),
        admin_id,
    )

    summary = f"Evolution approve: {rec['recommendation_code']} — {rec['proposed_title']}"
    changelog_body = payload.changelog_md or summary

    await execute(
        """
        INSERT INTO s004_strategy_version_changelog (
            strategy_id, from_version, to_version, summary, changelog_md, recommendation_id, created_by
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        cat["strategy_id"],
        cat["version"],
        new_ver,
        summary,
        changelog_body,
        recommendation_id,
        admin_id,
    )

    await execute(
        """
        UPDATE s004_strategy_evolution_recommendations
        SET status = 'IMPLEMENTED',
            updated_at = NOW(),
            approved_by = $2,
            approved_at = NOW(),
            implemented_version = $3
        WHERE id = $1
        """,
        recommendation_id,
        admin_id,
        new_ver,
    )

    await invalidate_recommendation_cache_for_strategy(cat["strategy_id"], new_ver)

    return {
        "status": "ok",
        "strategy_id": cat["strategy_id"],
        "from_version": cat["version"],
        "new_version": new_ver,
        "publish_status": "DRAFT",
    }
