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

### Cache 1 — Stored data

The result of `get_user_subscription(user_email)`, which queries:

- `users` table (trial status, trial expiry)
- `user_subscriptions` table (active plan, start date)
- `subscription_plans` table (plan limits, price)
- `ai_usage_log` table (`COUNT(*)` of tokens used this billing period)

### Cache 1 — Reason

`check_ai_limit()` is called at the start of **every** compute request — NL query,
analytics run, forecast, anomaly detection, report. Without caching, that is 5+
DB queries on every button click. With a 60-second cache, the first request in any
minute hits the DB; all subsequent requests in that minute are served from memory.

### Cache 1 — Acceptable lag

If a user hits their token limit, they can continue making requests for up to
`SUB_CACHE_TTL` seconds before being blocked. Default is 60 seconds. Lower this
value if stricter enforcement is needed (minimum recommended: 30s).

### Cache 1 — Invalidation

Explicitly busted (entry deleted from `_sub_cache`) when:

- User subscribes to or changes a plan → `subscribe_to_plan()`
- User starts a free trial → `start_trial()`

Not busted when tokens are charged — this is intentional (the lag is the TTL).

### Cache 1 — Code location

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

### Cache 2 — Stored data

The complete result of every Analytics Hub template query. Templates include:
`revenue_trend`, `top_products`, `category_performance`, `customer_analysis`,
`payment_breakdown`, `hourly_heatmap`, `shop_comparison`, `inventory_risk`.

### Cache 2 — Reason

Before the `sp_*` shared table migration, these queries took 507–680 seconds due
to BNL joins on JSON views. After the migration they take 0.035–0.5 seconds.
Even at that speed, caching is valuable: repeated clicks on the same template
(e.g. a user refreshing the dashboard) return instantly with zero DB hit.

### Cache 2 — Invalidation

Explicitly busted for a specific tenant when a sync completes:

```python
# called in integrations.py after _run_sync_inner() succeeds
from providers.salesplay.analytics import cache_bust
cache_bust(tenant_id)
```

This deletes all cached entries for that tenant so the next click fetches
fresh post-sync data.

### Cache 2 — Code location

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

### Cache 3 — Stored data

The result of `get_integration(user_email, provider_id)`, which does a single
`SELECT` on `user_integrations`. This row is needed on every NL query and
analytics request to look up the user's `table_prefix` (their tenant ID in
the shared `sp_*` tables).

### Cache 3 — Reason

`get_integration()` is called by `get_user_connections()`, which is called by
the NL query endpoint, analytics endpoint, forecast endpoint, and anomaly
detection endpoint. Before caching this was a DB round-trip on every request
for every integration user.

### Cache 3 — Invalidation

Explicitly busted (entry deleted) when the integration row changes:

- User connects a new provider → `connect_integration()`
- User disconnects a provider → `disconnect_integration()`
- A sync completes or fails → `_run_sync_inner()`

### Cache 3 — Code location

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

### Cache 4 — Stored data

- `run_forecast(rows, periods)` — Prophet ML model: fit + predict
- `run_anomaly_detection(rows, has_date)` — IsolationForest: fit + score

### Cache 4 — Reason

Training Prophet takes 2–8 seconds. Training IsolationForest takes 0.5–3 seconds.
These are called on every forecast or anomaly detection request. If a user clicks
the same chart twice (or two users look at the same data), the model is retrained
from scratch each time — wasteful.

### Cache 4 — Invalidation

**No explicit bust needed.** The cache key is an MD5 hash of the actual input rows.
When new data arrives after a sync, `rows` changes → different hash → cache miss →
model retrained on fresh data. Stale entries expire naturally after `MODEL_CACHE_TTL`.

### Cache 4 — Code location

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

### Cache 5 — Stored data

Which session variable name to use when setting a per-query timeout before
executing user-facing SQL. MySQL and MariaDB use different variable names.

### Cache 5 — Reason

`_set_query_timeout()` is called before every NL query execution. Without
caching, it would probe the server on every query to find the right variable
name. The server type never changes while the process is running, so probing
once and caching the result is correct.

### Cache 5 — Invalidation

Never busted. Set once on the first NL query, used for the lifetime of the
process. Resets on server restart.

### Cache 5 — Code location

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

```text
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

## Known Limitations & End-User Impact

### Limitation 1 — Per-process cache (not shared across workers)

**Technical cause:**
Each uvicorn worker is a separate OS process with its own private memory.
`UVICORN_WORKERS=4` means 4 completely independent copies of every cache dict.
A cache bust triggered in Worker 1 has no effect on Workers 2, 3, or 4.
Those workers continue serving their stale cached values until the TTL expires.

**Does this affect you now?**
No. Your `.env` sets `UVICORN_WORKERS=1`. With a single worker there is no
split — one cache, one process, busts always take effect immediately.
This limitation only applies when you scale to multiple workers in production.

---

#### Impact on Cache 1 — Billing (multi-worker)

**Scenario:** A user's token balance hits zero. The request that exhausts the
tokens lands on Worker 1, which busts its own billing cache. The user's next
request lands on Worker 2. Worker 2 still has the old "tokens available" result
in its cache. Worker 2 allows the request through.

**What the user experiences:**
A user who should be blocked by their token limit can continue making AI
requests for up to 60 more seconds (the TTL), provided those requests are
routed to a different worker.

**How bad is it:**
Minor at current scale. With low user counts, the probability of hitting a
different worker on the very next request is low. At high concurrency (many
simultaneous requests) the probability increases. A determined user could
intentionally spam requests to extract extra usage.

**Mitigation options:**

- Lower `SUB_CACHE_TTL` to `30` — halves the maximum overage window with
  minimal DB cost increase (one extra query per user per 30s vs per 60s).
- Set `SUB_CACHE_TTL=0` to disable billing cache entirely — correct behaviour
  but adds 5+ DB queries to every compute request; only do this if the DB
  can handle the load.
- Migrate to Redis — billing cache is shared across all workers; bust in
  Worker 1 is instantly visible to all other workers. Correct at any scale.

---

#### Impact on Cache 2 — Analytics results (multi-worker)

**Scenario:** A sync completes. `cache_bust(tenant_id)` is called on whichever
worker ran the sync job. The user refreshes the Analytics Hub. Their request
lands on a different worker that still has the pre-sync result cached.

**What the user experiences:**
After a sync completes, the user sees old analytics data for up to 5 minutes
depending on which worker serves their requests. The data is not wrong — it
is simply from before the latest sync. The user may think the sync did not work.

**How bad is it:**
Mildly confusing. Not a data correctness issue — the user is only looking at
their own data, just an older snapshot of it. After the TTL expires, all
workers return fresh results automatically.

**Mitigation options:**

- Lower `ANALYTICS_CACHE_TTL` to `60` — stale window reduced to 1 minute
  at the cost of more frequent SQL queries on `sp_*` tables.
- Migrate to Redis — bust is shared across all workers instantly after sync.

---

#### Impact on Cache 3 — Integration metadata (multi-worker)

**Scenario:** A user disconnects a provider in settings. The disconnect is
processed by Worker 1, which busts that user's integration cache entry.
The user's next NL query or analytics request lands on Worker 2, which
still has the old "connected" integration row cached with the old `table_prefix`.

**What the user experiences:**
For up to 5 minutes after disconnecting, requests may still be processed as
if the provider is connected — using the old `table_prefix` to query `sp_*`
tables. The user may see data from their old connection even after disconnecting.

**How bad is it:**
Rare in practice (users rarely disconnect mid-session). Not a security issue —
it is still the same user's own data. The stale state self-corrects when the TTL
expires. The scenario where this matters most is if a user disconnects and
immediately reconnects with a different account — there could be a 5-minute
window of wrong-account data.

**Mitigation options:**

- Lower `INTEGRATION_CACHE_TTL` to `60`.
- Migrate to Redis.

---

#### Impact on Cache 4 — ML model results (multi-worker)

**No meaningful multi-worker issue.**

The cache key is an MD5 hash of the actual input rows. Every worker independently
computes the same hash for the same data and stores the same result. There is no
explicit bust — new data after a sync produces a different hash automatically,
causing every worker to miss its own cache and retrain. All workers converge to
the correct result on their own without any coordination.

**What the user experiences:** Nothing unusual. First request after new data
takes 2–8 seconds (model training). Subsequent requests return instantly.
This behaviour is identical regardless of how many workers are running.

---

#### Impact on Cache 5 — SQL timeout variable (multi-worker)

**No issue at all.**

Each worker probes the DB server once on its first NL query and caches the
result forever. The DB server type (MySQL or MariaDB) never changes while the
process is running. All workers independently determine and cache the same
correct variable name. No coordination needed.

---

### Limitation 2 — Lost on server restart

**Technical cause:**
All cache dicts are in RAM. When the server process stops, they are gone.

**What the user experiences:**
Immediately after a server restart, every request is a cache miss. This causes
a brief spike in DB query volume while caches warm back up. For most users,
individual requests are slightly slower for the first 1–5 minutes post-restart.
After that, caches are warm and performance returns to normal.

**How bad is it:**
Not a correctness issue — DB results are always correct. Only a brief
performance concern. Warm-up happens organically as users make requests;
no action needed.

---

### Limitation 3 — No cross-server sharing (multiple physical servers)

**Technical cause:**
If DataMind is ever deployed on multiple physical servers (e.g. behind an AWS
ALB or nginx load balancer), each server has its own set of in-process caches.
This is a more severe version of the multi-worker problem — a billing bust on
Server A is completely invisible to Server B.

**What the user experiences:**
Same issues as multi-worker, but with longer potential stale windows since
requests may always be routed to different servers by the load balancer.
In the billing case, a user who hits their limit could be blocked on Server A
but continue making requests on Server B for the full TTL duration indefinitely.

**How bad is it:**
Significant for billing correctness at multi-server scale. Acceptable for
analytics staleness. The billing issue in particular becomes a real revenue
and fairness concern.

**This is a post-MVP concern.** DataMind currently runs on a single server.

**Fix:** Migrate all caches to Redis. One Redis instance shared by all
servers and all workers. All busts are instantly visible everywhere.

---

## Summary — Impact by Deployment Mode

| Limitation | Dev (1 worker) | Prod (multiple workers) | Multi-server |
|---|---|---|---|
| Billing overage window | None | ≤ SUB_CACHE_TTL (60s) | ≤ SUB_CACHE_TTL per server |
| Stale analytics after sync | None | ≤ ANALYTICS_CACHE_TTL (300s) | ≤ ANALYTICS_CACHE_TTL |
| Stale integration metadata | None | ≤ INTEGRATION_CACHE_TTL (300s) | ≤ INTEGRATION_CACHE_TTL |
| ML model staleness | None | None | None |
| Performance after restart | Brief spike | Brief spike | Brief spike |

---

## Fix Roadmap

| Priority | Fix | When |
|---|---|---|
| Low | Lower `SUB_CACHE_TTL` to `30` if billing overage is a concern | Before scaling workers |
| Medium | Migrate billing cache to Redis | Before high-traffic production |
| Medium | Migrate analytics + integration caches to Redis | Before multi-worker production |
| Low | Migrate ML cache to Redis | Only if model training becomes a shared bottleneck |
| None | Cache 5 (timeout variable) | No action ever needed |

Redis migration is a post-MVP infrastructure task. Until then, running
`UVICORN_WORKERS=1` eliminates all multi-worker cache issues entirely.
