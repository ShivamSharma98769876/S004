"""Admin user management endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.auth_utils import hash_password
from app.db_client import execute, fetch, fetchrow
from app.api.auth_context import get_user_id, require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserPayload(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""


class UpdateApprovalPayload(BaseModel):
    approved_paper: bool | None = None
    approved_live: bool | None = None


def _parse_active_strategies(raw: object) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


@router.get("/users")
async def list_users(admin_id: int = Depends(require_admin)) -> list[dict]:
    """List all users (admin only), including ACTIVE marketplace strategy subscriptions per user."""
    rows = await fetch(
        """
        SELECT u.id, u.username, u.email, u.full_name, u.role, u.status, u.approved_paper, u.approved_live, u.created_at,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'strategy_id', s.strategy_id,
                           'strategy_version', s.strategy_version,
                           'display_name', COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version)
                       )
                       ORDER BY COALESCE(c.display_name, s.strategy_id || ' ' || s.strategy_version)
                   ) FILTER (WHERE s.strategy_id IS NOT NULL),
                   '[]'::json
               ) AS active_strategies
        FROM s004_users u
        LEFT JOIN s004_strategy_subscriptions s
            ON s.user_id = u.id AND s.status = 'ACTIVE'
        LEFT JOIN s004_strategy_catalog c
            ON c.strategy_id = s.strategy_id AND c.version = s.strategy_version
        GROUP BY u.id
        ORDER BY u.created_at DESC
        """
    )
    out: list[dict] = []
    for r in rows:
        subs = _parse_active_strategies(r.get("active_strategies"))
        normalized = [
            {
                "strategy_id": str(x.get("strategy_id") or ""),
                "strategy_version": str(x.get("strategy_version") or ""),
                "display_name": str(x.get("display_name") or ""),
            }
            for x in subs
            if isinstance(x, dict)
        ]
        out.append(
            {
                "id": int(r["id"]),
                "username": str(r.get("username") or ""),
                "email": str(r.get("email") or ""),
                "full_name": str(r.get("full_name") or ""),
                "role": str(r.get("role", "USER")),
                "status": str(r.get("status", "ACTIVE")),
                "approved_paper": bool(r.get("approved_paper")),
                "approved_live": bool(r.get("approved_live")),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "active_strategies": normalized,
            }
        )
    return out


@router.post("/users")
async def create_user(
    payload: CreateUserPayload,
    admin_id: int = Depends(require_admin),
) -> dict:
    """Create a new user (admin only)."""
    email = (payload.email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    existing = await fetchrow(
        "SELECT id FROM s004_users WHERE LOWER(email) = $1",
        email,
    )
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered.")

    password_hash = hash_password(payload.password)
    username = email[:80]  # Use email as username (unique)
    full_name = (payload.full_name or "").strip() or email.split("@")[0]

    await execute(
        """
        INSERT INTO s004_users (username, email, full_name, role, status, password_hash, approved_paper, approved_live)
        VALUES ($1, $2, $3, 'USER', 'ACTIVE', $4, FALSE, FALSE)
        """,
        username,
        email,
        full_name,
        password_hash,
    )
    row = await fetchrow(
        "SELECT id, username, email, full_name, role, status, approved_paper, approved_live FROM s004_users WHERE email = $1",
        email,
    )
    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "email": str(row["email"]),
        "full_name": str(row.get("full_name") or ""),
        "role": str(row.get("role", "USER")),
        "status": str(row.get("status", "ACTIVE")),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }


@router.put("/users/{user_id}/approval")
async def update_user_approval(
    user_id: int,
    payload: UpdateApprovalPayload,
    admin_id: int = Depends(require_admin),
) -> dict:
    """Update user's Paper/Live approval (admin only)."""
    updates = []
    params = []
    idx = 1
    if payload.approved_paper is not None:
        updates.append(f"approved_paper = ${idx}")
        params.append(payload.approved_paper)
        idx += 1
    if payload.approved_live is not None:
        updates.append(f"approved_live = ${idx}")
        params.append(payload.approved_live)
        idx += 1
    if not updates:
        raise HTTPException(status_code=400, detail="Provide approved_paper and/or approved_live.")

    params.append(user_id)
    await execute(
        f"""
        UPDATE s004_users
        SET {", ".join(updates)}, updated_at = NOW()
        WHERE id = ${idx}
        """,
        *params,
    )
    row = await fetchrow(
        "SELECT id, username, email, approved_paper, approved_live FROM s004_users WHERE id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="User not found.")
    return {
        "id": int(row["id"]),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }
