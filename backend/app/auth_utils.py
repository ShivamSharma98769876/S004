"""Password hashing utilities."""

from passlib.context import CryptContext
from passlib.exc import UnknownHashError

_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    return _ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    hashed = (hashed or "").strip()
    if not hashed or not hashed.startswith("$2"):
        return False
    try:
        return _ctx.verify(plain, hashed)
    except UnknownHashError:
        return False
