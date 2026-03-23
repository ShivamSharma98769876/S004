"""Prometheus scrape endpoint (no auth; protect at network / reverse proxy in production)."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.metrics.prometheus_metrics import metrics_payload

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    data, ctype = metrics_payload()
    return Response(content=data, media_type=ctype)
