# Salesplay Integration — Configuration Guide

> **Branch:** system-enhancements-01  
> **Last updated:** 2026-06-03

---

## Overview

The Salesplay integration spans several sub-systems:

| Sub-system | What it does |
|---|---|
| **Sync** (`providers/salesplay/sync.py`) | Background job that pulls Salesplay data into DataMind tables |
| **Embed proxy** (`embed.py`) | Server-side proxy so the iframe can call the Salesplay API without CORS errors |
| **Embed security** (`embed.py`, `main.py`) | Origin allow-listing for the partner iframe at HTTP and postMessage level |
| **Seed script** (`scripts/seed_embed_partners.py`) | One-time DB seeder that registers partner keys and allowed origins |

All configurable values are now controlled through environment variables. No magic numbers remain hardcoded in source files.

---

## Environment Variables Reference

### Salesplay API Base

| Variable | Default | Description |
|---|---|---|
| `SALESPLAY_BASE_URL` | `https://api.salesplaypos.com/v1.0` | REST API base for the sync job. Override to point at the dev/staging server. |

### Sync Behaviour

| Variable | Default | Description |
|---|---|---|
| `SALESPLAY_PAGE_SIZE` | `250` | Items fetched per paginated API call. Salesplay maximum is 250. |
| `SALESPLAY_RATE_SLEEP` | `1.1` | Seconds to wait between consecutive paginated requests. Prevents 429s. |
| `SALESPLAY_DEFAULT_LOOKBACK_DAYS` | `90` | How many days back a delta sync looks when there is no recorded last-sync timestamp. |
| `SALESPLAY_HTTP_TIMEOUT` | `30` | Per-request HTTP timeout in seconds. |
| `SALESPLAY_RETRY_ATTEMPTS` | `3` | Number of retries on connection or timeout errors before giving up. |
| `SALESPLAY_VERIFY_SSL` | `false` | Set to `false` only in dev environments that use the `spdeveloperapi.nvision.lk` staging server (self-signed cert). **Must be `true` in production.** |

### Embed Proxy

The iframe runs inside a partner's page. It cannot call the Salesplay API directly because Salesplay enforces CORS. These settings control the thin server-side proxy that forwards requests.

| Variable | Default | Description |
|---|---|---|
| `SALESPLAY_EMBED_PROXY_BASE` | `https://api.salesplaypos.com/v2.0/public/app` | Salesplay public app API base URL. The `/profile` and `/integrations/access_tokens` endpoints are called relative to this. |
| `SALESPLAY_EMBED_PROXY_TIMEOUT` | `10` | Timeout in seconds for each proxy HTTP call. |
| `SALESPLAY_EMBED_RATE_LIMIT` | `5` | Maximum number of `/embed/init` calls allowed per IP address per window. |
| `SALESPLAY_EMBED_RATE_WINDOW` | `60` | Rolling window in seconds for the rate limiter above. |

### Embed Partner Origins

Three things control which domains are allowed to host the DataMind iframe. Each layer guards a different attack surface — **do not remove any of them** (see the security section below).

| Variable | Default | Description |
|---|---|---|
| `EMBED_ALLOWED_ORIGINS` | _(empty)_ | Comma-separated origins added to FastAPI CORSMiddleware and `Content-Security-Policy: frame-ancestors`. Guards the HTTP layer. Leave empty in local dev (falls back to localhost). |
| `SALESPLAY_EMBED_ORIGINS` | `https://app.salesplay.io,https://backoffice.salesplay.io` | Origins written to the `embed_partners` DB row when running the seed script. Guard the iframe postMessage layer. |
| `LOYVERSE_EMBED_ORIGINS` | `https://r.loyverse.com,https://loyverse.com` | Same as above for the Loyverse partner. |

---

## Why Allowed Origins Appear in Three Places

This is the most commonly asked question about this integration. The short answer is **each layer guards a different attack surface**. Removing any one of them would open a real security hole.

### Layer 1 — CORS / CSP HTTP headers (env var `EMBED_ALLOWED_ORIGINS`)

**Where:** `main.py` → FastAPI `CORSMiddleware` + `Content-Security-Policy: frame-ancestors` response header.

**What it stops:** The browser from making cross-origin HTTP requests to the `/embed/*` API at all. If a malicious site tries to embed the iframe or call the embed API, the browser will reject the response before JavaScript ever sees it.

**Scope:** Global — applies to every request hitting any `/embed/*` endpoint.

### Layer 2 — Database row `embed_partners.allowed_origins`

**Where:** `embed.py` → returned in `/embed/context` response → used by iframe JS (`EmbedApp.jsx`) for:
- `window.parent.postMessage(msg, origin)` — restricts where outgoing messages are sent
- `if (!allowedOrigins.includes(event.origin)) return` — filters incoming messages

**What it stops:** A malicious parent page from injecting commands into the iframe via `postMessage`, or the iframe leaking user data to an unexpected parent.

**Scope:** Per-partner — each partner row has its own list.

### Layer 3 — Hardcoded localhost fallback

**Where:** `main.py` lines 352-355.

**What it stops:** Nothing malicious — it ensures developers can run the iframe locally without configuring anything.

### Priority Table

| Layer | Source | Scope | Used for |
|---|---|---|---|
| 1 (HTTP gate) | `.env` `EMBED_ALLOWED_ORIGINS` | All `/embed/*` routes | CORS + CSP headers |
| 2 (iframe gate) | DB `embed_partners.allowed_origins` | Per-partner iframe | postMessage validation |
| 3 (dev fallback) | Hardcoded `localhost:5173`, `localhost:3000` | CORS only | Local development |

In production, layers 1 and 2 will both contain the Salesplay domains. This is intentional — they are not duplicates, they are independent security gates applied at different points in the request lifecycle.

---

## SSL Verification (`SALESPLAY_VERIFY_SSL`)

The Salesplay dev/staging server (`spdeveloperapi.nvision.lk`) uses a self-signed certificate. In the old code, `verify=False` was hardcoded, which silently disabled SSL verification everywhere — including in production if the env was misconfigured.

The new behaviour:

```
SALESPLAY_VERIFY_SSL=false   →  verify=False  (dev, staging)
SALESPLAY_VERIFY_SSL=true    →  verify=True   (production — use this)
SALESPLAY_VERIFY_SSL=<unset> →  verify=False  (safe default for dev; change in prod)
```

**Action required for production:** Set `SALESPLAY_VERIFY_SSL=true` in your production `.env`.

---

## Files Changed

| File | What changed |
|---|---|
| [backend/embed.py](../backend/embed.py) | `_SALESPLAY_BASE`, `_PROXY_TIMEOUT`, `_RATE_LIMIT`, `_RATE_WINDOW` now read from env |
| [backend/providers/salesplay/sync.py](../backend/providers/salesplay/sync.py) | `PAGE_SIZE`, `RATE_SLEEP`, `DEFAULT_DAYS`, `timeout`, `verify`, retry count now from env |
| [backend/scripts/check_salesplay_sync.py](../backend/scripts/check_salesplay_sync.py) | Same sync constants now from env |
| [backend/scripts/seed_embed_partners.py](../backend/scripts/seed_embed_partners.py) | Hardcoded allowed_origins replaced with `SALESPLAY_EMBED_ORIGINS` / `LOYVERSE_EMBED_ORIGINS` env vars |
| [backend/.env](../backend/.env) | All new variables added with dev defaults |
| [backend/.env.example](../backend/.env.example) | All new variables documented with explanations |

---

## Checklist for Production Deployment

- [ ] Set `SALESPLAY_BASE_URL` to `https://api.salesplaypos.com/v1.0`
- [ ] Set `SALESPLAY_EMBED_PROXY_BASE` to `https://api.salesplaypos.com/v2.0/public/app`
- [ ] Set `SALESPLAY_VERIFY_SSL=true`
- [ ] Set `EMBED_ALLOWED_ORIGINS` to the Salesplay production domains
- [ ] Confirm `SALESPLAY_EMBED_ORIGINS` and `LOYVERSE_EMBED_ORIGINS` match your live partner domains
- [ ] Run `python scripts/seed_embed_partners.py` to write origins into the DB
- [ ] Restart the backend so new env vars are loaded
