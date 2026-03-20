"""Auth and user info endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr

from app.auth_utils import hash_password, verify_password
from app.db_client import execute, fetchrow
from app.api.auth_context import get_user_id, require_admin

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginPayload(BaseModel):
    """Supports email+password (preferred) or username (legacy)."""

    email: str | None = None
    password: str | None = None
    username: str | None = None


class RegisterPayload(BaseModel):
    email: EmailStr
    password: str
    full_name: str = ""


@router.post("/login")
async def login(payload: LoginPayload) -> dict:
    """Login with email+password, or username (legacy, no password)."""
    email = (payload.email or "").strip() if payload.email else ""
    password = payload.password or ""
    username = (payload.username or "").strip() if payload.username else ""

    # Legacy: username-only (for users without password_hash)
    if username and not email:
        row = await fetchrow(
            "SELECT id, username, role, status, password_hash, approved_paper, approved_live FROM s004_users WHERE username = $1",
            username,
        )
        if not row:
            raise HTTPException(status_code=401, detail="Invalid username.")
        if row.get("password_hash"):
            raise HTTPException(status_code=400, detail="Use email and password to login.")
        status = str(row.get("status", "")).upper()
        if status != "ACTIVE":
            raise HTTPException(status_code=401, detail="User account is not active.")
        return {
            "user_id": int(row["id"]),
            "username": str(row["username"]),
            "email": str(row.get("email") or ""),
            "role": str(row.get("role", "USER")).upper(),
            "approved_paper": bool(row.get("approved_paper")),
            "approved_live": bool(row.get("approved_live")),
        }

    # Email + password
    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password are required.")

    row = await fetchrow(
        """
        SELECT id, username, email, role, status, password_hash, approved_paper, approved_live
        FROM s004_users WHERE LOWER(email) = LOWER($1)
        """,
        email,
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not verify_password(password, str(row.get("password_hash") or "")):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    status = str(row.get("status", "")).upper()
    if status != "ACTIVE":
        raise HTTPException(status_code=401, detail="User account is not active.")

    return {
        "user_id": int(row["id"]),
        "username": str(row.get("username") or row.get("email") or ""),
        "email": str(row.get("email") or ""),
        "role": str(row.get("role", "USER")).upper(),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }


@router.get("/me")
async def get_current_user(user_id: int = Depends(get_user_id)) -> dict:
    """Return current user id, role, and approval status."""
    row = await fetchrow(
        """
        SELECT id, username, email, role, status, approved_paper, approved_live
        FROM s004_users WHERE id = $1
        """,
        user_id,
    )
    if not row:
        return {
            "user_id": user_id,
            "role": "USER",
            "approved_paper": False,
            "approved_live": False,
        }
    return {
        "user_id": int(row["id"]),
        "username": str(row.get("username") or ""),
        "email": str(row.get("email") or ""),
        "role": str(row.get("role", "USER")).upper(),
        "approved_paper": bool(row.get("approved_paper")),
        "approved_live": bool(row.get("approved_live")),
    }
