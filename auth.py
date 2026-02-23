"""
auth.py
-------
All authentication and authorization utilities.

Exports used across the app
────────────────────────────
  create_access_token(user_id, email, role)  → str
  create_refresh_token(user_id)              → str
  verify_token(token, expected_type)         → TokenPayload
  get_current_user                           → FastAPI Depends (returns User)
  require_role(role)                         → FastAPI Depends (returns User)
  hash_password(plain)                       → str
  verify_password(plain, hashed)             → bool
  verify_ownership(user_id, resume_id, db)   → None (raises 403 if not owner)
  blacklist_token(jti, ttl_seconds)          → None
  is_token_blacklisted(jti)                  → bool
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import redis as redis_lib
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import User, UserResume

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

JWT_SECRET = os.getenv("JWT_SECRET", "changeme")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# ── Password hashing (bcrypt) ─────────────────────────────────────────────────

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Returns bcrypt hash of plaintext password."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Returns True if plaintext matches the bcrypt hash."""
    return _pwd_context.verify(plain, hashed)


# ── Token blacklist (Redis) ───────────────────────────────────────────────────

try:
    _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _redis.ping()
    _REDIS_OK = True
except Exception:
    _redis = None
    _REDIS_OK = False


def blacklist_token(jti: str, ttl_seconds: int) -> None:
    """
    Stores a token's JTI in Redis with a TTL equal to its remaining validity.
    After TTL the key auto-expires, so the blacklist never grows unboundedly.
    """
    if _REDIS_OK and _redis:
        _redis.setex(f"blacklist:{jti}", ttl_seconds, "1")


def is_token_blacklisted(jti: str) -> bool:
    """Returns True if the JTI has been blacklisted (logout / rotation)."""
    if _REDIS_OK and _redis:
        return bool(_redis.exists(f"blacklist:{jti}"))
    return False  # fail open if Redis is down (degrade gracefully)


# ── Token Payload schema ──────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    sub: str               # user UUID as string
    email: str
    role: str
    jti: str               # unique token ID for blacklisting
    exp: int               # UNIX timestamp
    type: Literal["access", "refresh"]


# ── Token creation ────────────────────────────────────────────────────────────

def create_access_token(user_id: str, email: str, role: str) -> str:
    """
    Creates a signed JWT access token.
    Expires in ACCESS_TOKEN_EXPIRE_MINUTES (default 30 min).
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "jti": str(uuid.uuid4()),
        "exp": int(expire.timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """
    Creates a signed JWT refresh token.
    Expires in REFRESH_TOKEN_EXPIRE_DAYS (default 7 days).
    Contains minimal claims — only used to issue new access tokens.
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "email": "",       # not included in refresh token
        "role": "",
        "jti": str(uuid.uuid4()),
        "exp": int(expire.timestamp()),
        "type": "refresh",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# ── Token verification ────────────────────────────────────────────────────────

def verify_token(
    token: str,
    expected_type: Literal["access", "refresh"] = "access",
) -> TokenPayload:
    """
    Decodes and validates a JWT.
    Raises HTTP 401 for any failure (expired, tampered, wrong type, blacklisted).
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        raw = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError:
        raise credentials_exception

    if raw.get("type") != expected_type:
        raise credentials_exception

    payload = TokenPayload(**raw)

    # Check token blacklist (logout / rotation)
    if is_token_blacklisted(payload.jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# ── FastAPI OAuth2 scheme ─────────────────────────────────────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ── get_current_user dependency ───────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    FastAPI dependency — extracts and validates the Bearer token,
    then returns the authenticated User from the database.

    Usage:
        @app.get("/protected")
        async def endpoint(current_user: User = Depends(get_current_user)):
            ...
    """
    payload = verify_token(token, expected_type="access")

    result = await db.execute(select(User).where(User.id == payload.sub))
    user: Optional[User] = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    return user


# ── require_role dependency ───────────────────────────────────────────────────

def require_role(role: str):
    """
    FastAPI dependency factory — ensures the current user has a specific role.

    Usage:
        @app.delete("/admin/user/{id}")
        async def delete_user(current_user: User = Depends(require_role("admin"))):
            ...
    """
    async def _check_role(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role != role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {role}",
            )
        return current_user
    return _check_role


# ── Resource ownership ────────────────────────────────────────────────────────

async def verify_ownership(
    user_id: str,
    resume_id: str,
    db: AsyncSession,
) -> None:
    """
    Raises HTTP 403 if `user_id` does not own `resume_id`.
    Call this before any endpoint that operates on a user's resume.

    Usage:
        await verify_ownership(str(current_user.id), resume_id, db)
    """
    result = await db.execute(
        select(UserResume).where(
            UserResume.user_id == user_id,
            UserResume.resume_id == resume_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this resource",
        )


# ── Password policy ───────────────────────────────────────────────────────────

def validate_password_strength(password: str) -> None:
    """
    Raises HTTP 422 if the password does not meet policy requirements:
      - 8–72 characters (bcrypt hard limit is 72 bytes)
      - At least 1 uppercase letter
      - At least 1 digit
    """
    errors = []
    # bcrypt silently truncates at 72 bytes — we reject early with a clear message
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Password must be 72 characters or fewer",
        )
    if len(password) < 8:
        errors.append("at least 8 characters")
    if not any(c.isupper() for c in password):
        errors.append("at least 1 uppercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("at least 1 digit")
    if errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must contain: {', '.join(errors)}",
        )
