from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query

from app.db_client import fetchrow


async def get_user_id(
    uid: int | None = Query(default=None, ge=1),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> int:
    if x_user_id:
        try:
            parsed = int(x_user_id)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    if uid is not None:
        return uid
    raise HTTPException(
        status_code=401,
        detail="Missing or invalid X-User-Id. Sign in again from the app login page.",
    )


async def require_admin(user_id: int = Depends(get_user_id)) -> int:
    """Ensure the current user has ADMIN role. Raises 403 if not."""
    row = await fetchrow(
        "SELECT role FROM s004_users WHERE id = $1",
        user_id,
    )
    if not row or str(row.get("role", "")).upper() != "ADMIN":
        raise HTTPException(status_code=403, detail="This feature is restricted to Admin users only.")
    return user_id


async def check_mode_approval(user_id: int, mode: str) -> None:
    """Raise 403 if user is not approved for the given trade mode (PAPER/LIVE)."""
    mode = (mode or "PAPER").upper()
    if mode not in ("PAPER", "LIVE"):
        return
    row = await fetchrow(
        "SELECT approved_paper, approved_live, role FROM s004_users WHERE id = $1",
        user_id,
    )
    if not row:
        raise HTTPException(status_code=403, detail="User not found.")
    if str(row.get("role", "")).upper() == "ADMIN":
        return  # Admins bypass approval
    if mode == "PAPER" and not row.get("approved_paper"):
        raise HTTPException(status_code=403, detail="You are not approved for Paper trading. Contact admin.")
    if mode == "LIVE" and not row.get("approved_live"):
        raise HTTPException(status_code=403, detail="You are not approved for Live trading. Contact admin.")
