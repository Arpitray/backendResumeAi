# Frontend API Reference — AI Resume Backend

Base URL (local): `http://localhost:8000`

---

## Authentication Overview

This API uses **JWT Bearer tokens**.

- After login/register, you receive an `access_token` and a `refresh_token`.
- Send the `access_token` in every protected request:
  ```
  Authorization: Bearer <access_token>
  ```
- Access tokens expire in **30 minutes**. Use the refresh endpoint to get a new pair.
- Refresh tokens expire in **7 days**.

---

## 1. Auth Endpoints

### `POST /auth/register`
Create a new local account.

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "Arpit123",
  "name": "Arpit Ray"
}
```

**Password rules:** min 8 chars · at least 1 uppercase · at least 1 digit · max 72 bytes (UTF-8)

**Response `201`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid-string",
    "email": "user@example.com",
    "name": "Arpit Ray",
    "role": "user",
    "provider": "local",
    "avatar_url": null,
    "created_at": "2026-02-19T12:00:00"
  }
}
```

**Error responses:**
| Status | Meaning |
|--------|---------|
| `400` | Email already registered or password too weak |

---

### `POST /auth/login`
Login with email and password.

**Request body:**
```json
{
  "email": "user@example.com",
  "password": "Arpit123"
}
```

**Response `200`:** Same shape as `/auth/register` response (access_token, refresh_token, user).

**Error responses:**
| Status | Meaning |
|--------|---------|
| `401` | Invalid email or password (intentionally generic to prevent enumeration) |
| `403` | Account is deactivated |

---

### `POST /auth/refresh`
Exchange a refresh token for a new access + refresh token pair (rotation — old refresh token is invalidated).

**Request body:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

**Error responses:**
| Status | Meaning |
|--------|---------|
| `401` | Refresh token invalid, expired, or already used |

---

### `GET /auth/me`
Returns the current user's profile.

**Headers:** `Authorization: Bearer <access_token>`

**Response `200`:**
```json
{
  "id": "uuid-string",
  "email": "user@example.com",
  "name": "Arpit Ray",
  "role": "user",
  "provider": "local",
  "avatar_url": null,
  "created_at": "2026-02-19T12:00:00"
}
```

---

### `POST /auth/logout`
Blacklists the current access token (server-side invalidation).

**Headers:** `Authorization: Bearer <access_token>`

**Response `200`:**
```json
{ "detail": "Successfully logged out" }
```

> After calling this, discard both tokens on the client side.

---

## 2. OAuth Endpoints

### `POST /auth/oauth/google`
Sign in with Google. Frontend obtains `id_token` from Google SDK, then calls this endpoint.

**Google flow:**
1. User clicks "Sign in with Google" → Google SDK gives you an `id_token`
2. POST it here → receive JWT tokens

**Request body:**
```json
{
  "id_token": "<google-id-token>"
}
```

**Response `200`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid-string",
    "email": "user@gmail.com",
    "name": "Arpit Ray",
    "role": "user",
    "provider": "google",
    "avatar_url": "https://..."
  }
}
```

**Error responses:**
| Status | Meaning |
|--------|---------|
| `401` | Invalid or expired Google token |
| `501` | Google OAuth not configured on server (env vars missing) |

---

### `POST /auth/oauth/github`
Sign in with GitHub. Frontend gets `code` from GitHub OAuth redirect, then calls this endpoint.

**GitHub flow:**
1. Redirect user to: `https://github.com/login/oauth/authorize?client_id=<GITHUB_CLIENT_ID>&scope=user:email`
2. GitHub redirects back with `?code=...`
3. POST the `code` here → receive JWT tokens

**Request body:**
```json
{
  "code": "<github-oauth-code>"
}
```

**Response `200`:** Same shape as Google OAuth response (provider will be `"github"`).

**Error responses:**
| Status | Meaning |
|--------|---------|
| `400` | Code exchange failed or no verified email on GitHub account |
| `401` | GitHub rejected the code |
| `501` | GitHub OAuth not configured on server (env vars missing) |

---

## 3. Resume Endpoints (all protected)

All resume endpoints require `Authorization: Bearer <access_token>`.

---

### `POST /upload-resume`
Upload a PDF resume. Returns a `resume_id` used in all other endpoints.

**Request:** `multipart/form-data` with field `file` (PDF)

**Response `200`:**
```json
{
  "status": "uploaded",
  "stored_as": "uuid.pdf",
  "chunks": 18
}
```

> Save `stored_as` as your `resume_id` — you will need it for every subsequent call.

---

### `GET /auth/resumes`
List all resumes uploaded by the current user.

**Response `200`:**
```json
[
  {
    "resume_id": "uuid.pdf",
    "filename": "myresume.pdf",
    "uploaded_at": "2026-02-19T12:00:00"
  }
]
```

---

### `POST /process-resume/{file_id}`
Re-process and preview a previously uploaded resume.

**Response `200`:**
```json
{
  "status": "processed",
  "total_chunks": 18,
  "previews": [
    { "id": 0, "preview": "First 150 chars of chunk..." }
  ]
}
```

---

### `POST /index-resume/{file_id}`
Re-index a resume into the vector store.

**Response `200`:**
```json
{ "status": "indexed", "resume_id": "uuid.pdf", "chunks": 18 }
```

---

## 4. Job Endpoints (protected)

### `POST /upload-job`
Store a job description and get back a `job_id`.

**Request body:**
```json
{
  "description": "We are looking for a backend engineer with Python, FastAPI..."
}
```

**Response `200`:**
```json
{
  "status": "job stored",
  "job_id": "uuid-string",
  "chunks": 6
}
```

---

## 5. Match / Analysis Endpoints (protected)

All require `resume_id` and `job_id` from the upload steps above.

---

### `POST /match`
Match a resume against a job description. Returns similarity score and top matching sections.

**Request body:**
```json
{
  "resume_id": "uuid.pdf",
  "job_id": "uuid-string"
}
```

**Response `200`:**
```json
{
  "match_score_percent": 78.4,
  "top_matches": [
    {
      "resume_chunk": "...",
      "job_match": "...",
      "score": 0.91
    }
  ]
}
```

---

### `POST /match/coach`
AI-generated resume improvement feedback.

**Request body:** Same as `/match`

**Response `200`:**
```json
{ "ai_feedback": "Your resume is strong in X but lacks Y..." }
```

---

### `POST /match/roadmap`
AI-generated skill gap + learning path roadmap.

**Request body:** Same as `/match`

**Response `200`:**
```json
{
  "skill_gaps": ["Docker", "Kubernetes"],
  "learning_path": [...]
}
```

---

### `POST /match/full-analysis`
Runs AI Coach + Roadmap **in parallel** (fastest option — single call returns everything).

**Request body:** Same as `/match`

**Response `200`:**
```json
{
  "match_score": 78.4,
  "top_matches": [...],
  "ai_feedback": "...",
  "learning_path": {...}
}
```

---

## 6. Interview Endpoints (protected)

### `POST /interview/start`
Start a mock interview session for a resume (optionally targeted at a job).

**Request body:**
```json
{
  "resume_id": "uuid.pdf",
  "job_id": "uuid-string"   // optional — omit for general interview
}
```

**Response `200`:**
```json
{
  "session_id": "uuid-string",
  "question": "Tell me about your experience with FastAPI...",
  "mode": "targeted"
}
```

> Save `session_id` — required for all follow-up calls.

---

### `POST /interview/answer`
Submit an answer to the current interview question. Returns feedback + next question.

**Request body:**
```json
{
  "session_id": "uuid-string",
  "question": "Tell me about your experience with FastAPI...",
  "answer": "I have built several REST APIs with FastAPI..."
}
```

**Response `200`:**
```json
{
  "feedback": "Good answer. You clearly explained your experience...",
  "scores": {
    "relevance": 8,
    "clarity": 7,
    "depth": 6
  },
  "next_question": "Can you walk me through a challenging bug you fixed?",
  "difficulty": "medium"
}
```

---

### `GET /interview/report/{session_id}`
Get the final interview report for a completed session.

**Response `200`:**
```json
{
  "final_score": 75,
  "strengths": ["System design", "Python"],
  "weaknesses": ["SQL depth"],
  "summary": "Candidate shows strong full-stack fundamentals..."
}
```

---

## 7. Q&A Endpoint (protected)

### `POST /ask`
Ask a free-form question about a specific resume.

**Request body:**
```json
{
  "resume_id": "uuid.pdf",
  "question": "What are the candidate's strongest technical skills?"
}
```

**Response `200`:**
```json
{
  "query": "What are the candidate's strongest technical skills?",
  "result": "Based on the resume, the strongest skills are...",
  "citations": ["Chunk 2: Proficient in Python, FastAPI..."]
}
```

---

## 8. Public Endpoints (no auth needed)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Health check — returns `{ "status": "AI Career Agent running" }` |
| `GET` | `/info` | Version info |

---

## Token Storage Recommendation

```
Access token  → memory (React state / Zustand store) — NOT localStorage
Refresh token → httpOnly cookie OR localStorage (if no XSS risk)
```

## Error Shape

All errors follow FastAPI's standard format:
```json
{ "detail": "Human-readable error message" }
```

## Auth Flow Summary (Quick Reference)

```
Register / Login
  → store access_token + refresh_token

Every API call
  → Header: Authorization: Bearer <access_token>

On 401 response
  → POST /auth/refresh with refresh_token
  → store new access_token + refresh_token
  → retry original request

On logout
  → POST /auth/logout
  → clear both tokens from client
```
