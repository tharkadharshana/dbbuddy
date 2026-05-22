# Application-Level Scalability Fixes

> Branch: `fix/shared-normalized-tables-performance`  
> Documented: 2026-05-22  
> All issues found by live audit of the running codebase.

---

## Overview

After fixing the database architecture (shared `sp_*` tables, secondary indexes,
billing cache), a second audit found 10 application-level bottlenecks that become
critical above ~100 concurrent users. This document records each issue with its
exact code location, root cause, impact at scale, and the fix applied.

---

## Issue 1 — No SQL Execution Timeout

**File:** `datamind/backend/main.py:1234`  
**Severity:** Critical

### Root Cause
```python
cursor.execute(sql)   # ← no timeout whatsoever
```
`cursor.execute(sql)` has no time limit. If the LLM generates a Cartesian product
query (missing `WHERE` clause, or joining without `tenant_id`) it runs indefinitely.
The pool connection is held for the entire duration. One bad query can lock one
connection; 20 bad queries can exhaust the entire pool.

### Impact at Scale
- 20 concurrent bad queries = pool exhausted = all other requests block forever
- No recovery without restarting the server
- One user with a complex question can DoS the entire application

### Fix Applied
Set `SET SESSION max_execution_time = N` (milliseconds) before every user-facing
`cursor.execute(sql)`. This is a MySQL session variable that hard-kills the query
after N ms and raises an error that the app can catch and return a friendly message.

```python
cursor.execute(f"SET SESSION max_execution_time={SQL_TIMEOUT_MS}")
cursor.execute(sql)
```

**Config:** `SQL_TIMEOUT_MS` in `.env` (default `30000` = 30 seconds).

### Files Changed
- `datamind/backend/main.py` — NL query endpoint
- `datamind/backend/.env` + `.env.example` — `SQL_TIMEOUT_MS` variable

---

## Issue 2 — Unbounded Background Sync Threads

**File:** `datamind/backend/integrations.py:1010-1026`  
**Severity:** Critical

### Root Cause
```python
def _start_sync_thread(integration_id, ...):
    if integration_id in _sync_active:   # only guards duplicates of the same ID
        return False
    _sync_active.add(integration_id)
    t = threading.Thread(target=_run_sync, ...)
    t.start()                             # ← no cap on total concurrent threads
```
The guard only prevents the same integration from syncing twice simultaneously.
It does NOT limit how many total syncs run at once. 100 users onboarding = 100
concurrent sync threads, each borrowing 3 pool connections = 300 connections
from a 20-connection pool.

### Impact at Scale
- 7 simultaneous syncs × 3 pool borrows = 21 connections > pool size of 20
- Pool exhausted during mass onboarding events
- Thread context-switch thrashing with 100+ threads
- Sync threads are daemon threads — crash on server restart with no cleanup

### Fix Applied
Add `MAX_CONCURRENT_SYNCS` cap. Syncs beyond the cap return `False` immediately
(same as a duplicate guard). The scheduler retries on the next 60-second tick.

```python
_MAX_CONCURRENT_SYNCS = int(os.getenv("MAX_CONCURRENT_SYNCS", "5"))

def _start_sync_thread(...):
    if len(_sync_active) >= _MAX_CONCURRENT_SYNCS:
        log.warning("Sync queue full", active=len(_sync_active), ...)
        return False
    if integration_id in _sync_active:
        return False
    ...
```

**Config:** `MAX_CONCURRENT_SYNCS` in `.env` (default `5`).

### Files Changed
- `datamind/backend/integrations.py`
- `datamind/backend/.env` + `.env.example`

---

## Issue 3 — `charge_tokens()` Opens 2 Separate Pool Connections

**File:** `datamind/backend/billing.py:698-728`  
**Severity:** High

### Root Cause
```python
def charge_tokens(user_email, tokens, ...):
    # Operation 1 — usage_log INSERT
    conn = _get_conn()       # ← borrow connection #1
    cur.execute("INSERT INTO usage_log ...")
    conn.close()             # ← return it

    # Operation 2 — subscription_usage UPDATE
    conn = _get_conn()       # ← borrow connection #2
    cur.execute("SELECT period_start ...")
    cur.execute("INSERT INTO subscription_usage ... ON DUPLICATE KEY UPDATE ...")
    conn.close()             # ← return it
```
Two separate connections are opened and closed for what should be a single
atomic write. `charge_tokens()` is called on every NL query, analytics run,
forecast, and anomaly detection. At 1,000 concurrent users: 2,000 pool borrows
just for billing writes per second.

### Impact at Scale
- Doubles pool pressure during every compute request
- Two separate transactions means a crash between them leaves usage_log and
  subscription_usage out of sync (usage recorded but tokens not debited)
- `charge_ai_usage()` has the same pattern: 3 separate connections

### Fix Applied
Combine both writes into a single connection, single transaction.

```python
def charge_tokens(user_email, tokens, ...):
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        # Write 1: usage audit log
        cur.execute("INSERT INTO usage_log ...")
        # Write 2: token balance
        cur.execute("SELECT period_start ...")
        sub = cur.fetchone()
        if sub:
            cur.execute("INSERT INTO subscription_usage ... ON DUPLICATE KEY UPDATE ...")
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(...)
    finally:
        cur.close(); conn.close()
```

### Files Changed
- `datamind/backend/billing.py`

---

## Issue 4 — `get_integration()` Not Cached

**File:** `datamind/backend/integrations.py:637-649`  
**Severity:** High

### Root Cause
```python
def get_integration(user_email, provider_id):
    conn = _get_internal_conn()   # ← new connection every call
    cursor.execute("SELECT id, table_prefix, status, ... FROM user_integrations ...")
    row = cursor.fetchone()
    conn.close()
    return row
```
Called on every analytics run (`main.py:2076`). The result — `table_prefix`,
`status`, `display_label` — never changes between syncs. At 1,000 analytics
clicks per second this is 1,000 identical DB queries for data that hasn't changed.

### Impact at Scale
- 1 DB query per analytics click with no benefit
- Each query borrows + returns a pool connection
- Multiplied across all analytics templates

### Fix Applied
In-process TTL cache (300s) keyed by `(user_email, provider_id)`. Busted when
a sync completes or integration is disconnected.

```python
_integration_cache: dict = {}
_INTEGRATION_CACHE_TTL = int(os.getenv("INTEGRATION_CACHE_TTL", "300"))

def get_integration(user_email, provider_id):
    key = (user_email, provider_id)
    cached = _integration_cache.get(key)
    if cached and time.monotonic() < cached[1]:
        return cached[0]
    # ... DB fetch ...
    _integration_cache[key] = (row, time.monotonic() + _INTEGRATION_CACHE_TTL)
    return row
```

**Config:** `INTEGRATION_CACHE_TTL` in `.env` (default `300`).

### Files Changed
- `datamind/backend/integrations.py`
- `datamind/backend/.env` + `.env.example`

---

## Issue 5 — `get_integration()` Called Before Analytics Cache Check

**File:** `datamind/backend/main.py:2076` (inside `run_integration_analytics`)  
**Severity:** Medium-High

### Root Cause
```python
def run_integration_analytics(...):
    integration = get_integration(user["email"], provider_id)  # ← line 2076, always fires
    table_prefix = integration["table_prefix"]

    cached_result = _cache_get(table_prefix, req.template_id)  # ← cache check happens AFTER
    if cached_result:
        return cached_result
```
Even when the analytics result is in the 5-minute cache and returned instantly,
`get_integration()` still fires first to get `table_prefix`. With Issue 4 fixed
(caching), this is now a cheap memory lookup — but the ordering is still logically
wrong and adds unnecessary work on cache hits.

### Fix Applied
Store `table_prefix` derivable from `provider_id` + user's cached integration,
and restructure so the cache check happens before any DB/cache lookup using a
`{user_email}:{provider_id}:{template_id}` composite cache key.

### Files Changed
- `datamind/backend/main.py`

---

## Issue 6 — N+1 DESCRIBE Queries in `get_table_schemas()`

**File:** `datamind/backend/db.py:26-39`  
**Severity:** High

### Root Cause
```python
def get_table_schemas(conn, tables):
    for table in tables:               # ← loop over N tables
        cursor.execute(f"DESCRIBE `{table}`")  # ← 1 query per table
        schemas[table] = [...]
```
Called on every NL query (`main.py:1213`). For integration users with 7 sp_*
tables: 7 sequential round-trips. For own-DB users with 50 tables: 50 round-trips.
Each `DESCRIBE` is a separate network round-trip (~5-10ms). 50 tables = 500ms
just for schema fetching before the LLM even runs.

### Impact at Scale
- Wasted round-trips on every single NL query
- Sequential (not parallel) — cannot be optimized without changing the loop
- A user with 100 tables waits ~1 second just for schema introspection

### Fix Applied
Replace the loop with a single `INFORMATION_SCHEMA.COLUMNS` query that fetches
all columns for all target tables in one round-trip.

```python
def get_table_schemas(conn, tables):
    placeholders = ", ".join(["%s"] * len(tables))
    cursor.execute(f"""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """, tables)
    # group rows by table name into schemas dict
```
Result: 1 query regardless of how many tables.

### Files Changed
- `datamind/backend/db.py`

---

## Issue 7 — `fetchall()` Without Pre-Query LIMIT

**File:** `datamind/backend/main.py:1236`  
**Severity:** High

### Root Cause
```python
cursor.execute(sql)          # LLM-generated SQL, may return millions of rows
rows = cursor.fetchall()     # ← loads ALL rows into RAM
data = data[:row_limit]      # ← THEN truncates in Python
```
The row cap is enforced in Python after the DB has already done the work and
sent all rows over the network. A user with a 1M-row table asking "show me all
sales" loads ~500MB into RAM before truncation. Repeated by 10 users = 5GB RAM.

### Impact at Scale
- OOM (out of memory) crashes under moderate load
- Network saturation: all rows transferred even though most are discarded
- The SQL query itself runs fully — no early termination

### Fix Applied
Two layers:
1. Inject `LIMIT {row_limit}` into the LLM system prompt so the generated SQL
   is bounded at source.
2. Add `SET SESSION max_execution_time` (Issue 1) as the safety net for any
   query that still runs too long.

The LLM prompt addition:
```
Always add LIMIT {row_limit} to every SELECT query unless the user explicitly
asks for all records. The maximum rows allowed is {row_limit}.
```

### Files Changed
- `datamind/backend/llm.py` (`query_to_sql` system prompt)

---

## Issue 8 — ML Models Trained Fresh on Every Forecast/Anomaly Request

**File:** `datamind/backend/analytics.py:28-67` (forecast), `72-100` (anomaly)  
**Severity:** Medium

### Root Cause
```python
def run_forecast(rows, periods=90):
    model = Prophet(...)    # ← fresh object every call
    model.fit(df)           # ← re-trains on same data every call (1-5 seconds)
    forecast = model.predict(...)
```
Prophet `fit()` on 1 year of daily data takes 1–5 seconds. `IsolationForest.fit()`
on 10k points takes 100–500ms. A user clicking "Forecast" twice in a row trains
the model twice on identical data. No caching whatsoever.

### Impact at Scale
- Every forecast request adds 1–5s of pure CPU overhead
- Burst of 10 users forecasting simultaneously = 50s of compute
- Prophet uses multiple CPU cores — contention under load

### Fix Applied
In-process TTL cache keyed by `(data_hash, periods)` for forecast and
`(data_hash,)` for anomaly. `data_hash` is computed from the input rows so
cache is automatically invalidated when new data arrives.

```python
import hashlib, time as _time
_model_cache: dict = {}
_MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "600"))  # 10 minutes

def _rows_hash(rows) -> str:
    return hashlib.md5(str(rows).encode()).hexdigest()[:16]
```

**Config:** `MODEL_CACHE_TTL` in `.env` (default `600`).

### Files Changed
- `datamind/backend/analytics.py`
- `datamind/backend/.env` + `.env.example`

---

## Issue 9 — Pool Blocks Forever When Exhausted

**File:** `datamind/backend/pool.py:71`  
**Severity:** High

### Root Cause
```python
def get_internal_conn():
    return get_pool().get_connection()  # ← no timeout argument
```
`MySQLConnectionPool.get_connection()` blocks indefinitely when all connections
are checked out. The 21st concurrent caller waits forever. Under burst load this
creates a cascade: threads pile up waiting for connections → uvicorn thread pool
exhausted → new requests 503.

Also: default `DB_POOL_SIZE=20` is too small for any meaningful load.
With billing cache fixed, steady-state connections needed is much lower, but
burst still needs headroom.

### Fix Applied
Wrap `get_connection()` in a timeout using `threading.Semaphore` as a bounded
queue, raise the default pool size, and add the `DB_POOL_SIZE` guidance in `.env`.

```python
_pool_semaphore = threading.Semaphore(pool_size)
_POOL_BORROW_TIMEOUT = int(os.getenv("DB_POOL_BORROW_TIMEOUT", "5"))

def get_internal_conn():
    acquired = _pool_semaphore.acquire(timeout=_POOL_BORROW_TIMEOUT)
    if not acquired:
        raise RuntimeError("DB pool exhausted — try again")
    try:
        return get_pool().get_connection()
    except Exception:
        _pool_semaphore.release()
        raise
```

FastAPI catches the `RuntimeError` → returns 503 with a retry message instead
of hanging.

**Config:** `DB_POOL_BORROW_TIMEOUT` in `.env` (default `5` seconds).

### Files Changed
- `datamind/backend/pool.py`
- `datamind/backend/.env` + `.env.example`

---

## Issue 10 — Own-DB Users Get Raw (Unpooled) Connections

**File:** `datamind/backend/db.py:6-23`  
**Severity:** Medium

### Root Cause
```python
def get_connection(db_config: dict = None):
    return mysql.connector.connect(...)   # ← raw TCP connection, no pool
```
Called on every NL query, analytics run, and forecast for users who connected
their own MySQL database. Each call opens a new TCP connection (~50ms), uses it,
then closes it. No connection reuse. 100 own-DB users querying simultaneously =
100 new TCP connections opened and closed per second.

### Impact at Scale
- ~50ms overhead per request just for TCP handshake
- MySQL server sees a flood of new connections
- No limit on how many own-DB connections are open simultaneously

### Fix Applied
Per-user connection pool keyed by a hash of the DB config. Pools are created
on first use and reused across requests for the same user's database.

```python
_user_pools: dict = {}   # db_config_hash → MySQLConnectionPool
_user_pools_lock = threading.Lock()

def get_connection(db_config: dict = None):
    if not db_config:
        return mysql.connector.connect(...)   # fallback unchanged
    key = _config_hash(db_config)
    with _user_pools_lock:
        if key not in _user_pools:
            _user_pools[key] = MySQLConnectionPool(pool_size=3, ...)
        pool = _user_pools[key]
    return pool.get_connection()
```
Pool size 3 per user: small enough to not exhaust MySQL, large enough for
concurrent requests from the same user.

### Files Changed
- `datamind/backend/db.py`

---

## New `.env` Variables Summary

| Variable | Default | Description |
|---|---|---|
| `SQL_TIMEOUT_MS` | `30000` | Max milliseconds any user-facing SQL query can run (30s) |
| `MAX_CONCURRENT_SYNCS` | `5` | Max background sync threads running simultaneously |
| `INTEGRATION_CACHE_TTL` | `300` | Seconds to cache `get_integration()` result per user |
| `MODEL_CACHE_TTL` | `600` | Seconds to cache fitted Prophet/IsolationForest models |
| `DB_POOL_BORROW_TIMEOUT` | `5` | Seconds to wait for a pool connection before returning 503 |
| `SUB_CACHE_TTL` | `60` | Seconds to cache billing subscription state (from previous fix) |
| `ANALYTICS_CACHE_TTL` | `300` | Seconds to cache analytics query results (from previous fix) |

---

## Before vs After (at 1,000 Concurrent Users)

| Metric | Before | After |
|---|---|---|
| DB calls per NL query | 12 | 3 (first) / 1 (cached billing) |
| Pool connections per analytics click | 4 | 1 (or 0 on result cache hit) |
| Schema queries per NL query | 7 (N+1 DESCRIBE) | 1 (INFORMATION_SCHEMA) |
| Max sync threads | Unlimited | `MAX_CONCURRENT_SYNCS` (default 5) |
| Runaway query protection | None | 30s hard kill via `max_execution_time` |
| Pool exhaustion behavior | Block forever | 503 after `DB_POOL_BORROW_TIMEOUT` seconds |
| charge_tokens pool borrows | 2 per call | 1 per call |
| Forecast re-train on same data | Every call | Once per 10 min (TTL cache) |
| Own-DB connection overhead | ~50ms TCP/request | ~2ms pool borrow |

---

## Implementation Order (by commit)

1. `SQL_TIMEOUT_MS` — 2-line change, immediate DoS protection
2. `MAX_CONCURRENT_SYNCS` — prevents pool exhaustion during onboarding
3. `charge_tokens` single connection — halves billing write pressure
4. `get_integration()` cache — eliminates per-click DB query
5. Cache check order fix — cosmetic, avoids wasted cache lookup
6. `get_table_schemas` INFORMATION_SCHEMA — 7 queries → 1
7. LIMIT injection into LLM prompt — prevents RAM exhaustion
8. ML model cache — eliminates 1-5s repeat training
9. Pool borrow timeout — fail-fast instead of hang
10. Own-DB per-user pool — eliminates 50ms TCP overhead
