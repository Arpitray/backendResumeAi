"""
routes/auth_routes.py
---------------------
Local email + password authentication.

Endpoints
─────────
  POST  /auth/register    Create a new account
  POST  /auth/login       Login and receive tokens
  POST  /auth/refresh     Exchange a refresh token for a new access token
  GET   /auth/me          Return the current user's profile
  POST  /auth/logout      Blacklist the current access token
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import (
    blacklist_token,
    create_access_token,
    create_refresh_token,
    get_current_user,
    hash_password,
    is_token_blacklisted,
    oauth2_scheme,
    validate_password_strength,
    verify_password,
    verify_token,
)
from database import get_db
from models import User

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    provider: str
    avatar_url: str | None
    created_at: str

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_to_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        email=user.email,
        name=user.name,
        role=user.role,
        provider=user.provider,
        avatar_url=user.avatar_url,
        created_at=user.created_at.isoformat(),
    )


def _make_tokens(user: User) -> dict:
    return {
        "access_token": create_access_token(str(user.id), user.email, user.role),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
    }


# ── POST /auth/register ───────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """
    Create a new local account.
    Steps:
      1. Validate password strength
      2. Check email uniqueness
      3. Hash password (bcrypt)
      4. Insert User row (provider = "local")
      5. Return access + refresh tokens + user profile
    """
    # 1. Password policy
    validate_password_strength(body.password)

    # 2. Email uniqueness
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists",
        )

    # 3. Hash
    hashed = hash_password(body.password)

    # 4. Create user
    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hashed,
        provider="local",
    )
    db.add(user)
    await db.flush()   # get auto-generated id before commit
    await db.refresh(user)

    # 5. Return
    tokens = _make_tokens(user)
    return AuthResponse(**tokens, user=_user_to_out(user))


# ── POST /auth/login ──────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """
    Login with email and password.
    Generic error message prevents user enumeration.
    """
    _invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
    )

    result = await db.execute(select(User).where(User.email == body.email))
    user: User | None = result.scalar_one_or_none()

    # User not found — same error as wrong password
    if user is None or user.hashed_password is None:
        raise _invalid

    # Wrong password
    if not verify_password(body.password, user.hashed_password):
        raise _invalid

    # Deactivated account
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact support.",
        )

    tokens = _make_tokens(user)
    return AuthResponse(**tokens, user=_user_to_out(user))


# ── POST /auth/refresh ────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """
    Rotate tokens:
      1. Validate the incoming refresh token
      2. Blacklist it (one-time use)
      3. Load user from DB
      4. Issue a new access token + new refresh token
    A stolen refresh token can only be used once — legitimate next use will fail.
    """
    payload = verify_token(body.refresh_token, expected_type="refresh")

    # Rotation: blacklist the old refresh token
    remaining = payload.exp - int(datetime.now(timezone.utc).timestamp())
    if remaining > 0:
        blacklist_token(payload.jti, remaining)

    result = await db.execute(select(User).where(User.id == payload.sub))
    user: User | None = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.email, user.role),
        refresh_token=create_refresh_token(str(user.id)),
    )


# ── GET /auth/me ──────────────────────────────────────────────────────────────

@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_user)):
    """
    Returns the profile of the currently authenticated user.
    """
    return _user_to_out(current_user)


# ── POST /auth/logout ─────────────────────────────────────────────────────────

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(token: str = Depends(oauth2_scheme)):
    """
    Blacklists the current access token so it cannot be reused.
    Client should also discard stored tokens.
    """
    payload = verify_token(token, expected_type="access")
    remaining = payload.exp - int(datetime.now(timezone.utc).timestamp())
    if remaining > 0:
        blacklist_token(payload.jti, remaining)
    return {"detail": "Successfully logged out"}
