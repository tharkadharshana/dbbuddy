# Phase 3 — Scale Hardening

## Goal

Prepare the backend to handle thousands of simultaneous users without DB connection exhaustion or worker starvation. This is done in two targeted changes. Everything else about the system stays the same.

**When to do this:** Do this before Salesplay announces the integration to their full user base. It can be done in parallel with Phase 2 — there are no dependencies between them.

---

## What Breaks at Scale Without This

Right now, every single API call — auth, query, settings, sync — opens a brand new `mysql.connector.connect()` and closes it at the end. Look at these three files:

- `datamind/backend/auth.py` line 32: `return mysql.connector.connect(...)`
- `datamind/backend/db.py` line 6: `return mysql.connector.connect(...)`
- `datamind/backend/integrations.py` line 43: `return mysql.connector.connect(...)`
- `datamind/backend/billing.py` line 63: `return mysql.connector.connect(...)`

MySQL's default `max_connections` is 151. That means if 152 requests arrive at the same time, the 152nd request fails with `Too many connections`. At 10,000 concurrent users, this happens continuously.

Connection pooling fixes this: instead of opening a new connection per request, connections are borrowed from a pool of, say, 20 connections and returned when done. 20 pooled connections can serve thousands of requests per second because each connection is held for only a few milliseconds.

---

## Step 3.1 — Add a Connection Pool Module

Create a new file `datamind/backend/pool.py`. This is a single shared connection pool for all of DataMind's internal DB operations.

```python
# datamind/backend/pool.py
"""
Shared MySQL connection pool for DataMind's internal DB.
All internal DB access (auth, integrations, billing, embed) should use
get_internal_conn() from this module instead of creating raw connections.
"""

import os
import mysql.connector.pooling
from logger import get_logger

log = get_logger(__name__)

_pool = None


def _build_pool():
    pool_size = int(os.getenv("DB_POOL_SIZE", "20"))
    return mysql.connector.pooling.MySQLConnectionPool(
        pool_name      = "datamind_pool",
        pool_size      = pool_size,
        pool_reset_session = True,
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout = 10,
    )


def get_pool():
    """Return the shared pool, creating it on first call."""
    global _pool
    if _pool is None:
        _pool = _build_pool()
        log.info("MySQL connection pool created",
                 size=int(os.getenv("DB_POOL_SIZE", "20")))
    return _pool


def get_internal_conn():
    """
    Get a pooled connection to DataMind's internal DB.
    IMPORTANT: Always call conn.close() when done — this returns it to the pool,
    it does NOT close the underlying socket.
    """
    return get_pool().get_connection()
```

---

## Step 3.2 — Replace `_get_internal_conn()` Calls

Now point `integrations.py`, `auth.py`, `billing.py`, and `embed.py` to use the pool.

**In `datamind/backend/integrations.py`:**

At the top of the file, replace the `_get_internal_conn` function (line 38–50) with:

```python
from pool import get_internal_conn as _get_internal_conn
```

Remove (or comment out) the old `_get_internal_conn` function body:

```python
# DELETE THIS ENTIRE FUNCTION — replaced by pool.py
# def _get_internal_conn():
#     return mysql.connector.connect(
#         host=os.getenv("DATAMIND_DB_HOST") or os.getenv("DB_HOST", "localhost"),
#         ...
#     )
```

**In `datamind/backend/auth.py`:**

The `_get_conn` function (line 32–40) is only called by auth operations. Add a pool import and replace it:

```python
# Add this import at the top of auth.py:
from pool import get_internal_conn as _get_conn
```

Then delete the old `_get_conn` function body (lines 32–40).

**In `datamind/backend/billing.py`:**

Same pattern — the `_get_conn` function (line 63–71) should be replaced:

```python
# Add this import at the top of billing.py:
from pool import get_internal_conn as _get_conn
```

Delete the old `_get_conn` function body.

**In `datamind/backend/embed.py`:**

Replace the `_get_conn` function you wrote in Phase 1 with:

```python
from pool import get_internal_conn as _get_conn
```

Delete the `_get_conn` function body from `embed.py`.

---

## Step 3.3 — Initialize the Pool at Startup

Open `datamind/backend/main.py`. In the `startup_event` function, add pool initialization as the very first thing (before the bootstrap calls):

```python
@app.on_event("startup")
def startup_event():
    # Initialize connection pool first — everything else depends on DB access
    try:
        from pool import get_pool
        get_pool()  # creates the pool on first call
    except Exception as _pe:
        log.warning("DB pool init failed", error=str(_pe))

    try:
        init_users_table()
    # ... rest of startup_event unchanged
```

---

## Step 3.4 — Add Pool Size to `.env`

```
# Number of pooled MySQL connections. Each uvicorn worker gets its own pool.
# With 4 workers and pool size 20, you're using up to 80 connections total.
# MySQL's default max_connections is 151 — leave headroom.
DB_POOL_SIZE=20
```

**Sizing guide:**

| Deployment | Workers | Pool Size | Total Connections Used |
|---|---|---|---|
| Single server, light load | 1 | 20 | 20 |
| Single server, moderate | 2 | 20 | 40 |
| Single server, heavy | 4 | 20 | 80 |
| Multiple servers (2× servers) | 4 each | 15 | 120 |

Always leave at least 30 connections free for your MySQL admin, monitoring tools, and manual queries.

---

## Step 3.5 — Run Multiple Uvicorn Workers

Right now you're probably running:

```
uvicorn main:app --reload --port 8000
```

`--reload` is for development only — it runs a single worker and restarts on code changes. For production:

```bash
# Production startup command (run from datamind/backend/)
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --no-access-log
```

Or with Gunicorn (more stable for production):

```bash
pip install gunicorn

gunicorn main:app \
  -k uvicorn.workers.UvicornWorker \
  -w 4 \
  -b 0.0.0.0:8000 \
  --timeout 120 \
  --keep-alive 5
```

`-w 4` means 4 worker processes. Each has its own connection pool of 20 connections = 80 total. For a server with 4 CPU cores, 4 workers is the standard starting point.

**On Windows (your current development OS):**
Gunicorn does not run on Windows. Use:

```
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

In production you will almost certainly be on Linux — use Gunicorn there.

---

## Step 3.6 — Add a Rate Limiter (Protect the Embed Endpoints)

The embed is publicly accessible with just a partner key. Without a rate limiter, someone could hammer `/embed/init` and create thousands of fake accounts, consuming trial credits.

Install the package:

```
pip install slowapi
```

Open `datamind/backend/main.py`. Add at the top after the existing imports:

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

Then open `datamind/backend/embed.py` and add rate limiting to the `embed_init` endpoint:

```python
# In embed.py, add this import at the top:
from fastapi import Request
# Import limiter from main — or pass it in. Easiest approach:
# define a module-level limiter in embed.py itself:
from slowapi import Limiter
from slowapi.util import get_remote_address

_limiter = Limiter(key_func=get_remote_address)

# Then decorate the endpoint:
@router.post("/init")
@_limiter.limit("5/minute")
def embed_init(request: Request, req: EmbedInitRequest):
    # ... rest of the function unchanged
```

**Why 5/minute:** A legitimate user can only meaningfully call `/embed/init` once (it creates their account). 5 per minute from the same IP allows retries if they mistype their API token, while blocking automated abuse.

The `/embed/context` endpoint is read-only and cheap — no rate limiting needed there.

---

## Step 3.7 — Increase MySQL `max_connections`

This is a one-time MySQL server configuration change. Connect to your MySQL server and run:

```sql
SET GLOBAL max_connections = 300;
```

To make it permanent, add to your MySQL config file (`/etc/mysql/my.cnf` or `/etc/mysql/mysql.conf.d/mysqld.cnf`):

```ini
[mysqld]
max_connections = 300
```

Then restart MySQL:

```bash
sudo systemctl restart mysql
```

**Why 300:** With 4 workers × 20 pool size = 80 connections from your app. 300 gives plenty of headroom for multiple app instances, admin tools, monitoring, and future growth.

---

## How to Test Phase 3

**Test 1 — Pool is working:**

Start the backend and check the startup log. You should see:

```
MySQL connection pool created size=20
```

**Test 2 — Concurrent load test:**

Using Python's `requests` library or `locust`:

```python
# quick_load_test.py — run this to verify no "Too many connections" errors
import concurrent.futures, requests, time

TOKEN = "YOUR_JWT_TOKEN"
URL   = "http://localhost:8000/providers/salesplay/status"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def check():
    r = requests.get(URL, headers=HEADERS)
    return r.status_code

start = time.time()
with concurrent.futures.ThreadPoolExecutor(max_workers=50) as ex:
    results = list(ex.map(lambda _: check(), range(200)))

print(f"200 requests in {time.time()-start:.1f}s")
print(f"Success: {results.count(200)}, Errors: {len([r for r in results if r != 200])}")
```

You should see 200 successes with no `Too many connections` errors.

**Test 3 — Verify pool reuse:**

Add a temporary log line to `pool.py`:

```python
def get_internal_conn():
    conn = get_pool().get_connection()
    log.debug("Pool connection borrowed", thread_id=conn.connection_id)
    return conn
```

Run a few API calls and check that the same connection IDs appear — this confirms connections are being reused, not re-created.

Remove the debug log line when done.

---

## What Phase 3 Does NOT Change

- The sync logic: sync still runs in background threads (already correct)
- User data isolation: unchanged — table prefixes still isolate each user's data
- The embed flow: unchanged — this is purely an infrastructure improvement
- The existing API endpoints: all unchanged

---

## Important Warning About `conn.close()` After Pool

With the pool, `conn.close()` does **not** close the TCP connection to MySQL. It returns the connection to the pool for reuse. This is exactly what you want.

However — if code raises an exception before `conn.close()` is called, that connection is leaked (stuck in "borrowed" state). Review all code that uses `_get_internal_conn()` and ensure `conn.close()` is always called, preferably in a `finally` block:

```python
conn = _get_internal_conn()
try:
    cursor = conn.cursor()
    # ... do work
    conn.commit()
finally:
    conn.close()  # always returned to pool
```

The existing code in `integrations.py` already uses `finally: conn.close()` in most places — good. Verify `auth.py` and `billing.py` do the same after your changes.
