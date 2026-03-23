from fastapi import APIRouter, Query

from app.db_client import fetchrow

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(deep: bool = Query(False, description="Include database check and build metadata")) -> dict:
    """Liveness. Use deep=true for readiness-style DB ping (load balancers / k8s)."""
    out: dict = {
        "status": "ok",
        "service": "s004-backend",
        "version": "0.1.0",
    }
    if not deep:
        return out
    try:
        await fetchrow("SELECT 1 AS ok")
        out["database"] = "ok"
    except Exception as e:
        out["status"] = "degraded"
        out["database"] = "error"
        out["database_error"] = str(e)[:300]
    return out

