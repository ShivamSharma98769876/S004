"""Admin user management endpoints."""

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


@router.get("/users")
async def list_users(admin_id: int = Depends(require_admin)) -> list[dict]:
    """List all users (admin only)."""
    rows = await fetch(
        """
        SELECT id, username, email, full_name, role, status, approved_paper, approved_live, created_at
        FROM s004_users
        ORDER BY created_at DESC
        """
    )
    return [
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
        }
        for r in rows
    ]


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
