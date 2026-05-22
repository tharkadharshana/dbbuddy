# Caching Architecture

## Overview

DataMind uses **server-side, in-process memory caching** (Python dicts with TTL).
There is no Redis, no Memcached, no disk cache, and no shared cache between processes.

Every cache is a plain `dict` protected by a `threading.Lock`, stored in the memory
of the uvicorn worker process that handled the request.

---

## Why Caching Exists

Without caching, every API request triggers multiple expensive operations:

| Operation | Cost without cache |
|---|---|
| Billing check on every NL query | 5+ DB queries including `COUNT(*)` on large tables |
| Analytics Hub template click | SQL query on `sp_*` tables |
| Integration metadata lookup | 1 DB query per request |
| Forecast / anomaly request | 2–10s ML model training (Prophet / IsolationForest) |
| SQL timeout variable probe | 1 DB round-trip per query |

Caching eliminates the repeated cost after the first request.

---

## Cache 1 — Subscription / Billing State

| Property | Value |
|---|---|
| **File** | `datamind/backend/billing.py` |
| **Variable** | `_sub_cache: dict` |
| **Lock** | `_sub_cache_lock: threading.Lock` |
| **TTL env var** | `SUB_CACHE_TTL` |
| **Default TTL** | 60 seconds |
| **Cache key** | `user_email` (string) |
| **Cache value** | Full subscription dict — plan name, token usage, limits, trial status, expiry |

### What it caches

The result of `get_user_subscription(user_email)`, which queries:
- `users` table (trial status, trial expiry)
- `user_subscriptions` table (active plan, start date)
- `subscription_plans` table (plan limits, price)
- `ai_usage_log` table (`COUNT(*)` of tokens used this billing period)

### Why it exists

`check_ai_limit()` is called at the start of **every** compute request — NL query,
analytics run, forecast, anomaly detection, report. Without caching, that is 5+
DB queries on every button click. With a 60-second cache, the first request in any
minute hits the DB; all subsequent requests in that minute are served from memory.

### Acceptable lag

If a user hits their token limit, they can continue making requests for up to
`SUB_CACHE_TTL` seconds before being blocked. Default is 60 seconds. Lower this
value if stricter enforcement is needed (minimum recommended: 30s).

### Cache invalidation

Explicitly busted (entry deleted from `_sub_cache`) when:
- User subscribes to or changes a plan → `subscribe_to_plan()`
- User starts a free trial → `start_trial()`

Not busted when tokens are charged — this is intentional (the lag is the TTL).

### Code location

```python
# billing.py
_sub_cache: dict = {}
_sub_cache_lock = _threading.Lock()
_SUB_CACHE_TTL = int(os.getenv("SUB_CACHE_TTL", "60"))

def _sub_cache_get(email: str): ...
def _sub_cache_set(email: str, result: dict): ...
def invalidate_sub_cache(email: str): ...
```

---

## Cache 2 — Analytics Hub Query Results

| Property | Value |
|---|---|
| **File** | `datamind/backend/providers/salesplay/analytics.py` |
| **Variable** | `_cache: dict` |
| **Lock** | `_cache_lock: threading.Lock` |
| **TTL env var** | `ANALYTICS_CACHE_TTL` |
| **Default TTL** | 300 seconds (5 minutes) |
| **Cache key** | `(tenant_id, template_id)` tuple |
| **Cache value** | Full query result dict — columns, rows, metadata |

### What it caches

The complete result of every Analytics Hub template query. Templates include:
`revenue_trend`, `top_products`, `category_performance`, `customer_analysis`,
`payment_breakdown`, `hourly_heatmap`, `shop_comparison`, `inventory_risk`.

### Why it exists

Before the `sp_*` shared table migration, these queries took 507–680 seconds due
to BNL joins on JSON views. After the migration they take 0.035–0.5 seconds.
Even at that speed, caching is valuable: repeated clicks on the same template
(e.g. a user refreshing the dashboard) return instantly with zero DB hit.

### Cache invalidation

Explicitly busted for a specific tenant when a sync completes:
```python
# called in integrations.py after _run_sync_inner() succeeds
from providers.salesplay.analytics import cache_bust
cache_bust(tenant_id)
```

This deletes all cached entries for that tenant so the next click fetches
fresh post-sync data.

### Code location

```python
# providers/salesplay/analytics.py
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = int(os.getenv("ANALYTICS_CACHE_TTL", "300"))

def _cache_get(tenant_id, template_id): ...
def _cache_set(tenant_id, template_id, result): ...
def cache_bust(tenant_id): ...   # called after sync
```

---

## Cache 3 — Integration Metadata

| Property | Value |
|---|---|
| **File** | `datamind/backend/integrations.py` |
| **Variable** | `_integration_cache: dict` |
| **Lock** | `_integration_cache_lock: threading.Lock` |
| **TTL env var** | `INTEGRATION_CACHE_TTL` |
| **Default TTL** | 300 seconds (5 minutes) |
| **Cache key** | `(user_email, provider_id)` tuple |
| **Cache value** | Full `user_integrations` DB row — `table_prefix`, `status`, `last_sync_at`, credentials (encrypted), etc. |

### What it caches

The result of `get_integration(user_email, provider_id)`, which does a single
`SELECT` on `user_integrations`. This row is needed on every NL query and
analytics request to look up the user's `table_prefix` (their tenant ID in
the shared `sp_*` tables).

### Why it exists

`get_integration()` is called by `get_user_connections()`, which is called by
the NL query endpoint, analytics endpoint, forecast endpoint, and anomaly
detection endpoint. Before caching this was a DB round-trip on every request
for every integration user.

### Cache invalidation

Explicitly busted (entry deleted) when the integration row changes:
- User connects a new provider → `connect_integration()`
- User disconnects a provider → `disconnect_integration()`
- A sync completes or fails → `_run_sync_inner()`

### Code location

```python
# integrations.py
_integration_cache: dict = {}
_integration_cache_lock = threading.Lock()
_INTEGRATION_CACHE_TTL = int(os.getenv("INTEGRATION_CACHE_TTL", "300"))

def _invalidate_integration_cache(user_email: str, provider_id: str): ...
# get_integration() checks cache first, falls back to DB, then writes to cache
```

---

## Cache 4 — ML Model Results (Forecast & Anomaly Detection)

| Property | Value |
|---|---|
| **File** | `datamind/backend/analytics.py` |
| **Variable** | `_model_cache: dict` |
| **Lock** | `_model_cache_lock: threading.Lock` |
| **TTL env var** | `MODEL_CACHE_TTL` |
| **Default TTL** | 600 seconds (10 minutes) |
| **Cache key** | `MD5(str(rows) + str(params))` — a hash of the input data and parameters |
| **Cache value** | Full result dict — historical series, forecast points, anomaly list, summary stats |

### What it caches

- `run_forecast(rows, periods)` — Prophet ML model: fit + predict
- `run_anomaly_detection(rows, has_date)` — IsolationForest: fit + score

### Why it exists

Training Prophet takes 2–8 seconds. Training IsolationForest takes 0.5–3 seconds.
These are called on every forecast or anomaly detection request. If a user clicks
the same chart twice (or two users look at the same data), the model is retrained
from scratch each time — wasteful.

### Cache invalidation

**No explicit bust needed.** The cache key is an MD5 hash of the actual input rows.
When new data arrives after a sync, `rows` changes → different hash → cache miss →
model retrained on fresh data. Stale entries expire naturally after `MODEL_CACHE_TTL`.

### Code location

```python
# analytics.py
_model_cache: dict = {}
_model_cache_lock = threading.Lock()
_MODEL_CACHE_TTL = int(os.getenv("MODEL_CACHE_TTL", "600"))

def _rows_hash(rows, *extra) -> str: ...   # MD5 of inputs
def _mcache_get(key: str): ...
def _mcache_set(key: str, result): ...
```

---

## Cache 5 — SQL Timeout Variable Detection

| Property | Value |
|---|---|
| **File** | `datamind/backend/main.py` |
| **Variable** | `_sql_timeout_var: str \| None` |
| **Lock** | `_sql_timeout_lock: threading.Lock` |
| **TTL** | Process lifetime (never expires) |
| **Cache key** | Global singleton — one per process |
| **Cache value** | `"max_execution_time"` (MySQL 5.7.8+) or `"max_statement_time"` (MariaDB) or `None` (unsupported) |

### What it caches

Which session variable name to use when setting a per-query timeout before
executing user-facing SQL. MySQL and MariaDB use different variable names.

### Why it exists

`_set_query_timeout()` is called before every NL query execution. Without
caching, it would probe the server on every query to find the right variable
name. The server type never changes while the process is running, so probing
once and caching the result is correct.

### Cache invalidation

Never busted. Set once on the first NL query, used for the lifetime of the
process. Resets on server restart.

### Code location

```python
# main.py
_sql_timeout_var: str | None = "unknown"   # "unknown" triggers first-use probe
_sql_timeout_lock = threading.Lock()

def _set_query_timeout(cursor) -> None:
    # Probes server on first call, caches result, uses cached value thereafter
```

---

## All Environment Variables

All TTL values are in **seconds** and are read once at process startup.
Changing them requires a server restart.

```env
# How long to cache a user's subscription / billing state.
# Lower = stricter token enforcement. Range: 30–300. Default: 60.
SUB_CACHE_TTL=60

# How long to cache Analytics Hub query results per tenant per template.
# Busted automatically after each sync. Range: 60–600. Default: 300.
ANALYTICS_CACHE_TTL=300

# How long to cache each user's integration metadata row.
# Busted on connect / disconnect / sync. Range: 60–600. Default: 300.
INTEGRATION_CACHE_TTL=300

# How long to cache Prophet / IsolationForest training results.
# New data after sync auto-bypasses via hash key. Range: 300–3600. Default: 600.
MODEL_CACHE_TTL=600
```

---

## Request Flow — Where Each Cache Is Hit

```
User clicks "Top Products" in Analytics Hub
│
├─ check_ai_limit()
│   └─ Cache 1 (SUB_CACHE_TTL=60s)
│       HIT  → 0 DB queries, ~0ms
│       MISS → 5 DB queries, ~15ms, result stored
│
├─ get_integration()
│   └─ Cache 3 (INTEGRATION_CACHE_TTL=300s)
│       HIT  → 0 DB queries, ~0ms
│       MISS → 1 DB query, ~5ms, result stored
│
└─ run_salesplay_analytics("top_products")
    └─ Cache 2 (ANALYTICS_CACHE_TTL=300s)
        HIT  → 0 DB queries, ~0ms  ← returns immediately
        MISS → 1 SQL query on sp_* tables, ~50ms, result stored


User clicks "Forecast" chart
│
├─ check_ai_limit()         → Cache 1
├─ get_integration()        → Cache 3
└─ run_forecast(rows)
    └─ Cache 4 (MODEL_CACHE_TTL=600s)
        HIT  → 0ms  (same data as last time)
        MISS → 2–8s Prophet training, result stored
```

---

## Important Limitations

### Per-process — not shared across workers

Each uvicorn worker is a separate Python process with its own memory.
If `UVICORN_WORKERS=4`, there are 4 independent copies of every cache.

Consequence: Worker 1 busting the analytics cache after a sync does not
affect Workers 2, 3, or 4. Those workers will serve stale cached results
until their own TTL expires.

**In practice:** With the default TTL values (60–600s) and typical sync
intervals (every few hours), this is acceptable. The maximum stale window
is equal to the TTL of the affected cache.

**Fix if this becomes a problem:** Move caches to Redis. All workers share
one Redis instance. Cache busts are immediately visible to all workers.
This is a post-MVP infrastructure concern.

### Lost on server restart

All caches are plain Python dicts. A server restart wipes them completely.
The first request after restart always hits the DB for every cache.
This is expected and correct — startup is fast, caches warm up in seconds.

### No persistence, no replication

Caches exist only in RAM. If the server crashes and restarts, the first
wave of requests after restart will see slightly higher DB load until
caches warm up again. This is not a correctness concern — only a
brief performance concern.

### Current storage: single server only

This caching design is correct for the current single-server deployment.
If DataMind ever runs on **multiple servers** behind a load balancer
(not just multiple workers on one server), the in-process caches will
cause correctness issues: a billing bust on Server A won't reach Server B.
At that point, migrate to Redis.
