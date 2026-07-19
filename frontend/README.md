# AI Video Studio — client frontend

React + Vite SPA for the AI video service backend. Create a generation job
(subject, Short/Long, topic), watch the pipeline progress live, download the
finished mp4, and publish it to YouTube via Google OAuth.

## Run

```bash
# backend (repo root) — stub mode needs no OpenAI key
USE_STUB_PIPELINE=true uvicorn app.main:app --port 8000

# frontend
cd frontend
npm install
npm run dev        # http://localhost:5173
```

The dev server proxies `/api` to `http://localhost:8000`. If the backend runs
on another port, either set `VITE_API_PROXY=http://localhost:<port>` for the
proxy, or `VITE_API_BASE=http://localhost:<port>` to bypass the proxy and call
the backend directly (CORS is enabled server-side via `CORS_ORIGINS`).

## Admin login

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the backend `.env` to protect the
videos API (create/list/status/download). The SPA redirects to `/login` on the
first 401 and stores the 24h bearer token in localStorage. With both vars
unset, auth is disabled (startup warning) and the login screen never appears.
The YouTube routes and Google OAuth stay open by design.

## YouTube upload

Requires `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` in the backend `.env` and
the redirect URI registered in Google Cloud Console. "Connect Google" starts
the consent flow with `?mode=web`; the backend callback redirects back to
`FRONTEND_OAUTH_REDIRECT` (default `http://localhost:5173/oauth/callback`)
with the tokens in the URL fragment. The token lives in localStorage — the
backend stores nothing.

## Notes

- Short = `orientation: vertical` (45–90s), Long = `orientation: horizontal`
  (5–10 min). The API has no separate short/long field.
- Jobs and uploads are kept in backend memory and vanish on server restart.
