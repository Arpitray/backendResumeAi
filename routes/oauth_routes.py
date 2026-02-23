"""
routes/oauth_routes.py
----------------------
OAuth 2.0 sign-in via Google and GitHub.

Endpoints
─────────
  POST  /auth/oauth/google   Verify Google id_token, upsert user, return JWT tokens
  POST  /auth/oauth/github   Exchange GitHub OAuth code, upsert user, return JWT tokens

Frontend flow
─────────────
  Google:
    1. User clicks "Sign in with Google" → Google SDK returns id_token
    2. Frontend POSTs { id_token } to /auth/oauth/google
    3. Backend verifies with Google → returns JWT tokens

  GitHub:
    1. User clicks "Sign in with GitHub" → GitHub redirects with ?code=...
    2. Frontend POSTs { code } to /auth/oauth/github
    3. Backend exchanges code for GitHub access token → fetches profile → returns JWT tokens
"""

import os
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import create_access_token, create_refresh_token
from database import get_db
from models import User

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class GoogleRequest(BaseModel):
    id_token: str


class GitHubRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


# ── Shared upsert logic ───────────────────────────────────────────────────────

async def _upsert_oauth_user(
    db: AsyncSession,
    *,
    provider: str,
    provider_id: str,
    email: str,
    name: str,
    avatar_url: Optional[str] = None,
) -> User:
    """
    Upsert logic for all OAuth providers:
      1. Find by (provider, provider_id)                → return existing user
      2. Not found: find by email                       → link provider to existing account
      3. Not found at all                               → create new user
    """
    # 1. Find by provider + provider_id
    result = await db.execute(
        select(User).where(
            User.provider == provider,
            User.provider_id == provider_id,
        )
    )
    user: Optional[User] = result.scalar_one_or_none()

    if user:
        # Refresh name/avatar in case they changed
        user.name = name
        user.avatar_url = avatar_url
        await db.flush()
        return user

    # 2. Find by email (link to existing local account)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user:
        # Link OAuth provider to the existing account
        user.provider = provider
        user.provider_id = provider_id
        user.avatar_url = user.avatar_url or avatar_url
        await db.flush()
        return user

    # 3. Create brand new user
    user = User(
        email=email,
        name=name,
        provider=provider,
        provider_id=provider_id,
        avatar_url=avatar_url,
        hashed_password=None,       # no password for OAuth-only accounts
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


def _token_response(user: User) -> dict:
    return {
        "access_token": create_access_token(str(user.id), user.email, user.role),
        "refresh_token": create_refresh_token(str(user.id)),
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "role": user.role,
            "provider": user.provider,
            "avatar_url": user.avatar_url,
        },
    }


# ── POST /auth/oauth/google ───────────────────────────────────────────────────

@router.post("/google", response_model=TokenResponse)
async def google_oauth(body: GoogleRequest, db: AsyncSession = Depends(get_db)):
    """
    Verify a Google id_token issued by the frontend Google Sign-In SDK.
    Steps:
      1. Call Google tokeninfo endpoint to verify the token
      2. Validate audience matches GOOGLE_CLIENT_ID
      3. Extract profile fields
      4. Upsert user in DB
      5. Return JWT access + refresh tokens
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google OAuth is not configured")

    # 1. Verify with Google
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": body.id_token},
        )

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token",
        )

    data = resp.json()

    # 2. Validate audience
    if data.get("aud") != GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token audience mismatch",
        )

    # 3. Extract fields
    provider_id = data.get("sub")
    email = data.get("email")
    name = data.get("name") or data.get("email", "").split("@")[0]
    avatar_url = data.get("picture")

    if not provider_id or not email:
        raise HTTPException(status_code=400, detail="Incomplete Google profile")

    # 4. Upsert
    user = await _upsert_oauth_user(
        db,
        provider="google",
        provider_id=provider_id,
        email=email,
        name=name,
        avatar_url=avatar_url,
    )

    # 5. Return tokens
    return _token_response(user)


# ── POST /auth/oauth/github ───────────────────────────────────────────────────

@router.post("/github", response_model=TokenResponse)
async def github_oauth(body: GitHubRequest, db: AsyncSession = Depends(get_db)):
    """
    Exchange a GitHub OAuth authorization code for JWT tokens.
    Steps:
      1. Exchange code → GitHub access token
      2. Fetch GitHub user profile
      3. Fetch primary verified email if profile email is null
      4. Upsert user in DB
      5. Return JWT access + refresh tokens
    """
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=501, detail="GitHub OAuth is not configured")

    async with httpx.AsyncClient(timeout=10) as client:

        # 1. Exchange code for access token
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": body.code,
            },
            headers={"Accept": "application/json"},
        )

        if token_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to exchange GitHub code")

        token_data = token_resp.json()
        gh_access_token = token_data.get("access_token")

        if not gh_access_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"GitHub OAuth failed: {token_data.get('error_description', 'unknown error')}",
            )

        # 2. Fetch user profile
        profile_resp = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {gh_access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        profile = profile_resp.json()

        email: Optional[str] = profile.get("email")

        # 3. Fetch primary verified email if profile email is null (private accounts)
        if not email:
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={
                    "Authorization": f"Bearer {gh_access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            for entry in emails_resp.json():
                if entry.get("primary") and entry.get("verified"):
                    email = entry["email"]
                    break

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Could not retrieve a verified email from your GitHub account. "
                   "Please make sure your primary email is verified on GitHub.",
        )

    provider_id = str(profile["id"])
    name = profile.get("name") or profile.get("login") or email.split("@")[0]
    avatar_url = profile.get("avatar_url")

    # 4. Upsert
    user = await _upsert_oauth_user(
        db,
        provider="github",
        provider_id=provider_id,
        email=email,
        name=name,
        avatar_url=avatar_url,
    )

    # 5. Return tokens
    return _token_response(user)
