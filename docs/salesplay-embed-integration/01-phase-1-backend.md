# Phase 1 — Backend Foundation

## Goal

Add the two new backend endpoints and the `embed_partners` table that the iframe needs. Update CORS. No changes to any existing endpoint.

**When complete:** You can visit `http://localhost:8000/embed/context?pk=sp_live_test` and get a valid JSON response. The `/embed/init` endpoint registers a user and connects their Salesplay account in one call.

---

## Step 1.1 — Create `datamind/backend/embed.py`

Create a new file. Do not put any of this in `main.py`.

```python
# datamind/backend/embed.py

import os
from datetime import datetime
from typing import Optional

import mysql.connector
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from logger import get_logger
from auth import create_user, authenticate_user, create_token, get_user
from billing import start_trial
from integrations import connect_provider, connect_integration

log = get_logger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])


# ── Internal DB connection (same pattern as integrations.py) ──────────────────

def _get_conn():
    return mysql.connector.connect(
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap_embed_tables():
    """Create embed_partners table. Safe to call on every startup."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embed_partners (
            partner_key     VARCHAR(64) PRIMARY KEY,
            partner_name    VARCHAR(128) NOT NULL,
            provider_id     VARCHAR(50) NOT NULL,
            allowed_origins TEXT NOT NULL,
            active          TINYINT(1) DEFAULT 1,
            created_at      DATETIME DEFAULT NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    cursor.close()
    conn.close()
    log.info("Embed tables bootstrapped")


def _get_partner(partner_key: str) -> Optional[dict]:
    """Look up a partner by key. Returns None if not found or inactive."""
    conn = _get_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM embed_partners WHERE partner_key=%s AND active=1",
        (partner_key,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/context")
def get_embed_context(pk: str):
    """
    Called by the iframe on load to validate the partner key and
    get context (partner name, provider, allowed origin).
    If the key is invalid the iframe shows an error screen.
    """
    partner = _get_partner(pk)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    log.info("Embed context requested", partner=partner["partner_name"])
    return {
        "partner_name": partner["partner_name"],
        "provider_id":  partner["provider_id"],
        "partner_key":  pk,
    }


class EmbedInitRequest(BaseModel):
    partner_key:  str
    api_token:    str        # Salesplay API token
    name:         str        # User's full name for DataMind account
    email:        str
    password:     str


@router.post("/init")
def embed_init(req: EmbedInitRequest):
    """
    One-shot endpoint for first-time embed users.
    Does in a single call:
      1. Validates the partner key
      2. Creates DataMind account (or returns error if email exists)
      3. Starts free trial
      4. Connects the Salesplay integration (validates API token + kicks off sync)
      5. Returns JWT token

    The iframe calls this once during onboarding. All subsequent calls
    use the standard /auth/login + existing API endpoints.
    """
    # 1. Validate partner
    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid partner key.")

    # 2. Create user account
    try:
        user = create_user(req.name, req.email, req.password)
        log.info("Embed: user created", email=req.email, partner=partner["partner_name"])
    except HTTPException as e:
        if "already registered" in str(e.detail):
            # Email already exists — try to authenticate instead
            # This handles the case where someone started onboarding but stopped
            try:
                user = authenticate_user(req.email, req.password)
                log.info("Embed: existing user re-authenticated", email=req.email)
            except HTTPException:
                raise HTTPException(
                    status_code=400,
                    detail="An account with this email already exists. Please use a different email or log in via datamind.ai."
                )
        else:
            raise

    # 3. Start trial (silently skip if already active)
    try:
        start_trial(req.email)
    except Exception as _te:
        log.warning("Embed: trial start skipped", email=req.email, error=str(_te))

    # 4. Connect provider (validates Salesplay API token + triggers background sync)
    try:
        connect_provider(
            user_email  = req.email,
            provider_id = partner["provider_id"],
            credentials = {"api_token": req.api_token},
        )
        log.info("Embed: provider connected", email=req.email, provider=partner["provider_id"])
    except ValueError as e:
        # connect_provider raises ValueError if credential validation fails
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.error("Embed: provider connect failed", email=req.email, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to connect Salesplay: {e}")

    # 5. Return JWT
    token = create_token(req.email)
    return {
        "token":       token,
        "user":        {"name": req.name, "email": req.email},
        "provider_id": partner["provider_id"],
        "sync":        "started",
    }
```

---

## Step 1.2 — Register the Router in `main.py`

Open `datamind/backend/main.py`. Make two changes:

**Change 1** — Import and bootstrap at the top, after the existing imports (around line 55):

```python
# Add after the billing imports, before the `log = get_logger` line:
from embed import router as embed_router, bootstrap_embed_tables
```

**Change 2** — Register the router after the app is created (after line 68, where `app = FastAPI(...)` is):

```python
app.include_router(embed_router)
```

**Change 3** — Call bootstrap inside the `startup_event` function (around line 77), after the existing bootstrap calls:

```python
@app.on_event("startup")
def startup_event():
    try:
        init_users_table()
    except Exception as _be:
        log.warning("Users table bootstrap skipped", error=str(_be))
    try:
        bootstrap_integration_tables()
    except Exception as _be:
        log.warning("Integration bootstrap skipped in startup", error=str(_be))
    try:
        bootstrap_billing_tables()
    except Exception as _be:
        log.warning("Billing bootstrap skipped", error=str(_be))
    # ADD THIS:
    try:
        bootstrap_embed_tables()
    except Exception as _be:
        log.warning("Embed bootstrap skipped", error=str(_be))
    start_scheduler()
    log.info("DataMind backend started")
```

---

## Step 1.3 — Update CORS in `main.py`

Current code (line 69–75):

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Replace with:

```python
# Read allowed embed origins from env var (comma-separated)
# e.g. EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://salesplay.com
_embed_origins_raw = os.getenv("EMBED_ALLOWED_ORIGINS", "")
_embed_origins = [o.strip() for o in _embed_origins_raw.split(",") if o.strip()]

# Always include localhost for development
_all_origins = ["http://localhost:5173", "http://localhost:3000"] + _embed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_origins if _embed_origins else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
)
```

**Why:** When `EMBED_ALLOWED_ORIGINS` is not set (local dev / current deployment), behaviour is unchanged (`"*"`). When you deploy for Salesplay, you set the env var and the origins lock down automatically.

Also add these response headers on the embed routes so the iframe can load:

```python
# Add this middleware AFTER the CORSMiddleware block:
@app.middleware("http")
async def embed_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/embed"):
        # Allow only registered partner origins to frame this page
        origins = os.getenv("EMBED_ALLOWED_ORIGINS", "*")
        response.headers["Content-Security-Policy"] = f"frame-ancestors {origins}"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response
```

Place this middleware immediately after the `log_requests` middleware that already exists (around line 256).

---

## Step 1.4 — Seed the Salesplay Partner Row

After the server starts and `bootstrap_embed_tables()` runs, insert the Salesplay partner into the database. You can do this manually in MySQL, or write a one-time script.

**Manual SQL (run once in production):**

```sql
INSERT INTO embed_partners (partner_key, partner_name, provider_id, allowed_origins)
VALUES (
  'sp_live_abc123',
  'Salesplay',
  'salesplay',
  'https://app.salesplay.io,https://backoffice.salesplay.io'
);
```

**For local development, use:**

```sql
INSERT INTO embed_partners (partner_key, partner_name, provider_id, allowed_origins)
VALUES (
  'sp_dev_test',
  'Salesplay (Dev)',
  'salesplay',
  'http://localhost:5173'
);
```

Replace `sp_live_abc123` with a real random key when going to production. Generate one with:

```python
import secrets
print(secrets.token_urlsafe(24))  # e.g. "sp_live_" + this
```

---

## Step 1.5 — Add `.env` Variable

Open your `.env` file (or however you manage env vars) and add:

```
EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://backoffice.salesplay.io
```

Leave this blank or unset during local development — the CORS will stay as `"*"`.

---

## How to Test Phase 1

Start your backend server normally:

```
cd datamind/backend
uvicorn main:app --reload --port 8000
```

**Test 1 — Context endpoint:**

```
GET http://localhost:8000/embed/context?pk=sp_dev_test
```

Expected response:
```json
{
  "partner_name": "Salesplay (Dev)",
  "provider_id": "salesplay",
  "partner_key": "sp_dev_test"
}
```

**Test 2 — Invalid key returns 404:**

```
GET http://localhost:8000/embed/context?pk=fake_key
```

Expected: `404 {"detail": "Invalid or inactive partner key."}`

**Test 3 — Init endpoint:**

```
POST http://localhost:8000/embed/init
Content-Type: application/json

{
  "partner_key": "sp_dev_test",
  "api_token": "YOUR_REAL_SALESPLAY_TOKEN",
  "name": "Test User",
  "email": "embedtest@example.com",
  "password": "testpass123"
}
```

Expected response:
```json
{
  "token": "eyJ...",
  "user": {"name": "Test User", "email": "embedtest@example.com"},
  "provider_id": "salesplay",
  "sync": "started"
}
```

**Test 4 — Verify the sync started:**

Use the token from Test 3:

```
GET http://localhost:8000/providers/salesplay/status
Authorization: Bearer eyJ...
```

Expected: `{"status": "syncing", ...}` — confirms the background sync kicked off.

---

## What Phase 1 Does NOT Do

- Does not build the iframe UI — that is Phase 2
- Does not change the existing `/providers/connect` flow that existing users use
- Does not change billing, auth, or sync logic
- Does not require any frontend changes

---

## Common Mistakes to Avoid

**Do not put the embed router in `main.py` directly.** Keep it in `embed.py`. `main.py` is already large (1,863 lines). The import + `include_router` pattern is the correct FastAPI approach.

**Do not skip `bootstrap_embed_tables()` in startup.** If the table doesn't exist when the server starts, every `/embed/context` call will throw a 500 error before you even seed the row.

**The `connect_provider()` function in `integrations.py` (line 557) returns a string connection_id, not a dict.** You don't need to use its return value in `embed_init` — it either succeeds (integration + sync started) or raises an exception.

**The `create_user()` function in `auth.py` (line 136) raises `HTTPException(400)` if the email is already registered.** The `embed_init` endpoint handles this gracefully — if the user exists it tries to authenticate with the provided password instead.
