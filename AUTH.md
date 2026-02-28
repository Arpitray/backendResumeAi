# Authentication & Authorization

Complete reference for all authentication flows supported by this API — local email/password and OAuth 2.0 (Google & GitHub).

---

## Table of Contents

- [Overview](#overview)
- [Token Model](#token-model)
- [Local Authentication](#local-authentication)
  - [Register](#post-authregister)
  - [Login](#post-authlogin)
  - [Refresh Token](#post-authrefresh)
  - [Get Current User](#get-authme)
  - [Logout](#post-authlogout)
- [OAuth 2.0](#oauth-20)
  - [Google Sign-In](#post-authoauthgoogle)
  - [GitHub Sign-In](#post-authoauthgithub)
- [Using Access Tokens](#using-access-tokens)
- [Password Policy](#password-policy)
- [Error Reference](#error-reference)
- [Environment Variables](#environment-variables)

---

## Overview

This API provides two authentication strategies that coexist seamlessly:

| Strategy | Provider Field | Use Case |
|---|---|---|
| **Local** | `local` | Email + password sign-up/sign-in |
| **Google OAuth** | `google` | One-click Google Sign-In |
| **GitHub OAuth** | `github` | One-click GitHub Sign-In |

All strategies return the same **JWT token pair** (access + refresh) and the same **user object**, making them interchangeable on the frontend.

---

## Token Model

| Token | Lifetime | Purpose |
|---|---|---|
| `access_token` | 30 minutes (configurable) | Authenticate API requests via `Authorization: Bearer` |
| `refresh_token` | 7 days (configurable) | Obtain a new token pair without re-login |

- Tokens are signed **HS256 JWTs**.
- Each token contains a unique `jti` (JWT ID) enabling precise revocation.
- Refresh tokens are **single-use** — a new one is issued on every refresh (rotation).
- Logged-out tokens are blacklisted in **Redis** for their remaining TTL.

---

## Local Authentication

### POST /auth/register

Create a new account with email and password.

**Request**
```json
{
  "email": "user@example.com",
  "password": "Str0ng!Pass",
  "name": "Jane Doe"
}
```

**Response** `201 Created`
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "Jane Doe",
    "role": "user",
    "provider": "local",
    "avatar_url": null,
    "created_at": "2026-02-28T12:00:00"
  }
}
```

**Errors**
| Status | Detail |
|---|---|
| `400` | Password does not meet policy requirements |
| `400` | An account with this email already exists |

---

### POST /auth/login

Authenticate with email and password.

**Request**
```json
{
  "email": "user@example.com",
  "password": "Str0ng!Pass"
}
```

**Response** `200 OK` — same shape as `/auth/register`.

**Notes**
- Returns a generic `"Invalid email or password"` error for both wrong email and wrong password to prevent user enumeration.
- Returns `403` if the account has been deactivated.

---

### POST /auth/refresh

Exchange a valid refresh token for a new access + refresh token pair.

**Request**
```json
{
  "refresh_token": "<jwt>"
}
```

**Response** `200 OK`
```json
{
  "access_token": "<new jwt>",
  "refresh_token": "<new jwt>",
  "token_type": "bearer"
}
```

**Notes**
- The old refresh token is **immediately blacklisted** — it cannot be reused.
- If a stolen token is used before the legitimate user refreshes, the legitimate next call will receive `401`.

---

### GET /auth/me

Returns the profile of the currently authenticated user.

**Headers**
```
Authorization: Bearer <access_token>
```

**Response** `200 OK`
```json
{
  "id": "uuid",
  "email": "user@example.com",
  "name": "Jane Doe",
  "role": "user",
  "provider": "local",
  "avatar_url": null,
  "created_at": "2026-02-28T12:00:00"
}
```

---

### POST /auth/logout

Blacklist the current access token so it cannot be reused.

**Headers**
```
Authorization: Bearer <access_token>
```

**Response** `200 OK`
```json
{
  "detail": "Successfully logged out"
}
```

**Notes**
- The client should also discard both stored tokens locally.
- The token is expired in Redis with a TTL matching its remaining validity.

---

## OAuth 2.0

OAuth users are stored in the same `users` table with `provider` set to `"google"` or `"github"`. No password is stored for OAuth-only accounts.

**Account linking rules (applied in order):**
1. Match by `(provider, provider_id)` → return existing user (refresh name/avatar).
2. Match by `email` → link OAuth provider to existing local account.
3. No match → create a new user automatically.

---

### POST /auth/oauth/google

Verify a Google `id_token` from the frontend Google Sign-In SDK and return JWT tokens.

**Frontend flow**
```
User clicks "Sign in with Google"
  → Google SDK returns id_token (client-side)
  → POST { id_token } to /auth/oauth/google
  → Receive JWT tokens
```

**Request**
```json
{
  "id_token": "<google id_token>"
}
```

**Response** `200 OK`
```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@gmail.com",
    "name": "Jane Doe",
    "role": "user",
    "provider": "google",
    "avatar_url": "https://lh3.googleusercontent.com/..."
  }
}
```

**Errors**
| Status | Detail |
|---|---|
| `501` | Google OAuth is not configured (`GOOGLE_CLIENT_ID` missing) |
| `401` | Invalid or expired Google token |
| `401` | Google token audience mismatch |
| `400` | Incomplete Google profile (missing sub or email) |

**Required env var:** `GOOGLE_CLIENT_ID`

---

### POST /auth/oauth/github

Exchange a GitHub OAuth authorization `code` for JWT tokens.

**Frontend flow**
```
User clicks "Sign in with GitHub"
  → GitHub redirects to your callback URL with ?code=...
  → Frontend POSTs { code } to /auth/oauth/github
  → Receive JWT tokens
```

**Request**
```json
{
  "code": "<github_oauth_code>"
}
```

**Response** `200 OK` — same shape as Google OAuth response, with `"provider": "github"`.

**Errors**
| Status | Detail |
|---|---|
| `501` | GitHub OAuth is not configured (`GITHUB_CLIENT_ID` or `GITHUB_CLIENT_SECRET` missing) |
| `400` | Failed to exchange GitHub code |
| `401` | GitHub OAuth returned an error |
| `400` | Could not retrieve a verified email from GitHub account |

**Notes**
- If the user's GitHub email is set to private, the API automatically fetches the primary verified email from `/user/emails`.
- If no verified email is found, the request fails with a descriptive message asking the user to verify their email on GitHub.

**Required env vars:** `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`

---

## Using Access Tokens

Include the access token in the `Authorization` header for all protected endpoints:

```http
Authorization: Bearer <access_token>
```

**Example (JavaScript)**
```js
const response = await fetch('/api/upload-resume', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${accessToken}`,
  },
  body: formData,
});
```

**Token refresh flow (recommended)**
```
Request fails with 401
  → Call POST /auth/refresh with stored refresh_token
  → Store new access_token and refresh_token
  → Retry original request
  → If refresh also fails → redirect to login
```

---

## Password Policy

Passwords for local accounts must satisfy **all** of the following:

| Rule | Requirement |
|---|---|
| Minimum length | 8 characters |
| Uppercase letter | At least 1 |
| Lowercase letter | At least 1 |
| Digit | At least 1 |
| Special character | At least 1 (`!@#$%^&*` etc.) |

OAuth accounts (Google, GitHub) have **no password** and are not subject to this policy.

---

## Error Reference

| Status | Meaning |
|---|---|
| `400` | Bad request — validation error or duplicate email |
| `401` | Unauthorized — missing, invalid, or expired token |
| `403` | Forbidden — deactivated account or insufficient role |
| `404` | Not found |
| `501` | OAuth provider not configured (missing env vars) |

All error responses follow the format:
```json
{
  "detail": "Human-readable error message"
}
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `JWT_SECRET` | ✅ | Secret key for signing JWTs. Use a long random string in production. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | ✅ | Access token lifetime in minutes. Default: `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | ✅ | Refresh token lifetime in days. Default: `7` |
| `REDIS_URL` | ✅ | Redis connection URL for token blacklisting. Default: `redis://localhost:6379` |
| `DATABASE_URL` | ✅ | PostgreSQL async connection string |
| `GOOGLE_CLIENT_ID` | OAuth only | Your Google OAuth 2.0 Client ID |
| `GITHUB_CLIENT_ID` | OAuth only | Your GitHub OAuth App Client ID |
| `GITHUB_CLIENT_SECRET` | OAuth only | Your GitHub OAuth App Client Secret |

> **Security note:** Never commit real values to version control. Use `.env` locally and environment secrets in production (Railway, Render, etc.).
