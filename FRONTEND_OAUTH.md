# Frontend OAuth Integration Guide

Step-by-step guide to integrating Google and GitHub OAuth sign-in with this backend. Includes setup, code examples, token handling, and edge cases.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Backend Endpoints](#backend-endpoints)
- [Google Sign-In Integration](#google-sign-in-integration)
  - [Google Cloud Setup](#1-google-cloud-setup)
  - [Install Dependencies](#2-install-dependencies)
  - [Implementation](#3-implementation)
- [GitHub Sign-In Integration](#github-sign-in-integration)
  - [GitHub App Setup](#1-github-app-setup)
  - [Redirect the User](#2-redirect-the-user)
  - [Handle the Callback](#3-handle-the-callback)
- [After OAuth — Storing Tokens](#after-oauth--storing-tokens)
- [Making Authenticated Requests](#making-authenticated-requests)
- [Token Refresh Flow](#token-refresh-flow)
- [Logout](#logout)
- [User Object Reference](#user-object-reference)
- [Error Handling](#error-handling)
- [Environment Variables](#environment-variables)

---

## How It Works

```
┌─────────────┐        id_token / code        ┌──────────────────┐
│   Frontend  │ ─────────────────────────────▶ │  Your Backend    │
│             │                                │  /auth/oauth/*   │
│             │ ◀───────────────────────────── │                  │
│             │    access_token + refresh_token │  ↕ verifies with │
└─────────────┘    + user object               │  Google / GitHub │
                                               └──────────────────┘
```

- Your frontend **never talks directly to Google/GitHub APIs** after getting the initial credential/code.
- The backend handles all verification, user creation, and JWT issuance.
- Both providers return the **same response shape** — identical frontend token handling.

---

## Backend Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/oauth/google` | Verify Google `id_token`, return JWT tokens |
| `POST` | `/auth/oauth/github` | Exchange GitHub `code`, return JWT tokens |

**Base URL:**
```
Production:  https://your-railway-or-render-url.com
Local:       http://localhost:8000
```

---

## Google Sign-In Integration

### 1. Google Cloud Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Navigate to **APIs & Services → Credentials**
4. Click **Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Web application**
6. Add your frontend URLs to **Authorized JavaScript origins**:
   ```
   http://localhost:3000
   https://your-frontend-domain.com
   ```
7. Copy the **Client ID** — you'll need it in both frontend and backend `.env`

---

### 2. Install Dependencies

```bash
npm install @react-oauth/google
```

---

### 3. Implementation

**`main.jsx` / `_app.jsx` — Wrap your app**
```jsx
import { GoogleOAuthProvider } from '@react-oauth/google';

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID; // or process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID

export default function App() {
  return (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <YourApp />
    </GoogleOAuthProvider>
  );
}
```

**`GoogleLoginButton.jsx` — Sign-in button**
```jsx
import { useGoogleLogin } from '@react-oauth/google';

const API_BASE = import.meta.env.VITE_API_URL; // e.g. http://localhost:8000

export default function GoogleLoginButton({ onSuccess, onError }) {
  const login = useGoogleLogin({
    onSuccess: async (tokenResponse) => {
      try {
        // tokenResponse.credential contains the id_token
        const res = await fetch(`${API_BASE}/auth/oauth/google`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id_token: tokenResponse.credential }),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'Google sign-in failed');
        }

        const data = await res.json();
        onSuccess(data); // { access_token, refresh_token, user }
      } catch (err) {
        onError(err.message);
      }
    },
    onError: () => onError('Google sign-in was cancelled or failed'),
    flow: 'implicit', // returns id_token directly
  });

  return (
    <button onClick={() => login()} className="btn-google">
      Sign in with Google
    </button>
  );
}
```

**Alternative — using `GoogleLogin` component (one-tap / button UI)**
```jsx
import { GoogleLogin } from '@react-oauth/google';

export default function GoogleLoginButton({ onSuccess, onError }) {
  const handleCredential = async (credentialResponse) => {
    try {
      const res = await fetch(`${API_BASE}/auth/oauth/google`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id_token: credentialResponse.credential }),
      });

      if (!res.ok) throw new Error('Google sign-in failed');
      const data = await res.json();
      onSuccess(data);
    } catch (err) {
      onError(err.message);
    }
  };

  return (
    <GoogleLogin
      onSuccess={handleCredential}
      onError={() => onError('Google sign-in failed')}
    />
  );
}
```

---

## GitHub Sign-In Integration

### 1. GitHub App Setup

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **New OAuth App**
3. Fill in:
   - **Application name:** Your app name
   - **Homepage URL:** `https://your-frontend-domain.com`
   - **Authorization callback URL:**
     ```
     http://localhost:3000/auth/callback/github     ← local
     https://your-frontend-domain.com/auth/callback/github  ← production
     ```
4. Click **Register application**
5. Copy the **Client ID** for your frontend `.env`
6. Generate a **Client Secret** for your backend `.env`

> ⚠️ **Never expose the Client Secret on the frontend.** It belongs only in the backend `.env`.

---

### 2. Redirect the User

When the user clicks "Sign in with GitHub", redirect them to GitHub's authorization URL:

```jsx
const GITHUB_CLIENT_ID = import.meta.env.VITE_GITHUB_CLIENT_ID;

// Build the GitHub OAuth URL
const getGithubOAuthUrl = () => {
  const params = new URLSearchParams({
    client_id: GITHUB_CLIENT_ID,
    scope: 'user:email',           // request email access
    redirect_uri: `${window.location.origin}/auth/callback/github`,
  });
  return `https://github.com/login/oauth/authorize?${params}`;
};

export default function GitHubLoginButton() {
  const handleClick = () => {
    window.location.href = getGithubOAuthUrl();
  };

  return (
    <button onClick={handleClick} className="btn-github">
      Sign in with GitHub
    </button>
  );
}
```

---

### 3. Handle the Callback

Create a page/component at the callback route (`/auth/callback/github`) that:
1. Reads the `code` from the URL
2. POSTs it to the backend
3. Stores the tokens and redirects

```jsx
// pages/auth/callback/github.jsx  (Next.js)
// or  src/pages/GithubCallback.jsx  (React Router / Vite)

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

const API_BASE = import.meta.env.VITE_API_URL;

export default function GitHubCallback() {
  const navigate = useNavigate();
  const [error, setError] = useState(null);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get('code');

    if (!code) {
      setError('No authorization code received from GitHub');
      return;
    }

    (async () => {
      try {
        const res = await fetch(`${API_BASE}/auth/oauth/github`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code }),
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || 'GitHub sign-in failed');
        }

        const data = await res.json();

        // Store tokens
        localStorage.setItem('access_token', data.access_token);
        localStorage.setItem('refresh_token', data.refresh_token);
        localStorage.setItem('user', JSON.stringify(data.user));

        // Redirect to dashboard
        navigate('/dashboard');
      } catch (err) {
        setError(err.message);
      }
    })();
  }, []);

  if (error) return <div className="error">Sign-in failed: {error}</div>;
  return <div>Signing you in...</div>;
}
```

---

## After OAuth — Storing Tokens

Both Google and GitHub return the same response. Store tokens consistently:

```js
function handleAuthSuccess(data) {
  // data = { access_token, refresh_token, token_type, user }

  localStorage.setItem('access_token', data.access_token);
  localStorage.setItem('refresh_token', data.refresh_token);
  localStorage.setItem('user', JSON.stringify(data.user));

  // Optional: store expiry time for proactive refresh
  const expiresAt = Date.now() + 30 * 60 * 1000; // 30 minutes
  localStorage.setItem('token_expires_at', expiresAt);
}
```

> **Security tip:** For higher-security apps, store tokens in `httpOnly` cookies instead of localStorage to prevent XSS access. With this API, send tokens as `Authorization: Bearer` headers from a cookie-reading layer.

---

## Making Authenticated Requests

Include the access token in every API call:

```js
// utils/api.js — reusable fetch wrapper

const API_BASE = import.meta.env.VITE_API_URL;

export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('access_token');

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });

  // Auto-refresh on 401
  if (res.status === 401) {
    const refreshed = await tryRefreshToken();
    if (refreshed) {
      // Retry original request with new token
      return apiFetch(path, options);
    } else {
      // Refresh failed — logout
      clearTokens();
      window.location.href = '/login';
    }
  }

  return res;
}

async function tryRefreshToken() {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) return false;

  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) return false;

    const data = await res.json();
    localStorage.setItem('access_token', data.access_token);
    localStorage.setItem('refresh_token', data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

function clearTokens() {
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');
}
```

**Usage:**
```js
// Upload resume (protected)
const res = await apiFetch('/upload-resume', {
  method: 'POST',
  body: formData,
  headers: {}, // Content-Type omitted — browser sets multipart boundary
});
```

---

## Token Refresh Flow

```
API call → 401 Unauthorized
    ↓
POST /auth/refresh  { refresh_token }
    ↓
Success?
  ✅ Yes → store new tokens → retry original request
  ❌ No  → clear tokens → redirect to /login
```

The backend uses **refresh token rotation** — every refresh issues a **new refresh token** and invalidates the old one. Always store the latest pair.

---

## Logout

```js
async function logout() {
  const token = localStorage.getItem('access_token');

  // Blacklist the token on the backend
  if (token) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
  }

  // Clear local storage
  localStorage.removeItem('access_token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('user');

  window.location.href = '/login';
}
```

---

## User Object Reference

Both OAuth and local login return the same user shape:

```ts
interface User {
  id: string;          // UUID
  email: string;
  name: string;
  role: string;        // "user" | "admin"
  provider: string;    // "local" | "google" | "github"
  avatar_url: string | null;
  created_at?: string; // ISO 8601 — only returned from /auth/me
}
```

**Show avatar:**
```jsx
{user.avatar_url ? (
  <img src={user.avatar_url} alt={user.name} className="avatar" />
) : (
  <div className="avatar-placeholder">{user.name[0]}</div>
)}
```

**Show provider badge:**
```jsx
const providerLabel = {
  local: '📧 Email',
  google: '🔵 Google',
  github: '⚫ GitHub',
};

<span className="provider-badge">{providerLabel[user.provider]}</span>
```

---

## Error Handling

| HTTP Status | When it happens | What to show |
|---|---|---|
| `400` | Bad/expired code (GitHub), missing email | "Sign-in failed. Please try again." |
| `401` | Invalid / expired token | Redirect to login |
| `403` | Account deactivated | "Your account has been deactivated. Contact support." |
| `501` | OAuth provider not configured on backend | "This sign-in method is not available right now." |
| `500` | Backend / DB error | "Something went wrong. Please try again later." |

```js
async function handleOAuthResponse(res) {
  if (res.ok) return res.json();

  const err = await res.json().catch(() => ({}));
  const detail = err.detail || 'Sign-in failed';

  if (res.status === 401) throw new Error('Session expired. Please sign in again.');
  if (res.status === 403) throw new Error('Account deactivated. Contact support.');
  if (res.status === 501) throw new Error('This sign-in method is not available.');
  throw new Error(detail);
}
```

---

## Environment Variables

Create a `.env.local` (Next.js) or `.env` (Vite) file in your frontend project:

```env
# Vite
VITE_API_URL=http://localhost:8000
VITE_GOOGLE_CLIENT_ID=your-google-client-id-here
VITE_GITHUB_CLIENT_ID=your-github-client-id-here

# Next.js
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_GOOGLE_CLIENT_ID=your-google-client-id-here
NEXT_PUBLIC_GITHUB_CLIENT_ID=your-github-client-id-here
```

> ✅ `GOOGLE_CLIENT_ID` and `GITHUB_CLIENT_ID` are **safe to expose** on the frontend.
> ❌ `GITHUB_CLIENT_SECRET` is **backend only** — never put it in the frontend.
