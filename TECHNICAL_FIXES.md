# Technical Fixes: Performance, Architecture & Onboarding

> Branch: `fix/shared-normalized-tables-performance`  
> Investigated: 2026-05-21 — live data from `livedata@test.com`  
> Fixed and verified: 2026-05-21 — all results measured on live database

---

## 1. Root Cause Analysis (with Proof)

### 1.1 The Database Architecture Problem

The system went through three architecture generations:

| Generation | What it was | Problem |
|---|---|---|
| Gen 1 | Per-user physical tables (`dm_{hash}_salesplay{type}`) | Table explosion: 1,000 users × 7 tables = 7,000 tables |
| Gen 2 | Single JSON blob table (`integration_records`) + SQL VIEWS | **Views cannot be indexed. JOINs = full table scans × full table scans** |
| Gen 3 (this fix) | Shared normalized tables with `tenant_id` partition key | Proper B-tree indexes on all join keys. Industry standard. |

### 1.2 What Happens When You Query Analytics Hub

When you click "Top Products" in Analytics Hub:

```
Frontend → POST /integrations/salesplay/analytics/run {template_id: "top_products"}
         → main.py:2053 run_integration_analytics()
         → analytics.py:187 run_salesplay_analytics(conn, table_prefix, "top_products")
         → Executes this SQL:

SELECT p.product_name, cat.category_name, SUM(li.quantity), SUM(li.total_money)
FROM dm_2180422af798eb91_salesplayreceipt_line_items li  ← THIS IS A VIEW
JOIN dm_2180422af798eb91_salesplayproducts p ...         ← THIS IS A VIEW
JOIN dm_2180422af798eb91_salesplayreceipts r ...         ← THIS IS A VIEW
LEFT JOIN dm_2180422af798eb91_salesplaycategories cat ... ← THIS IS A VIEW
WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
```

MySQL resolves each VIEW to:
```sql
SELECT ... FROM integration_records
WHERE tenant_id='dm_2180422af798eb91_salesplay'
  AND provider_id='salesplay'
  AND record_type='receipt_line_item'   -- 26,694 rows
```

Then joins these 4 full scans together using Block Nested Loop join.

**EXPLAIN output (from live database):**
```
table=integration_records  type=ALL  rows=37848  Using join buffer (flat, BNL join)
table=integration_records  type=ALL  rows=37848  Using join buffer (incremental, BNL join)
table=integration_records  type=ALL  rows=37848  ...
```

### 1.3 Measured Query Times (Real Data, livedata@test.com)

| Template | Time | Row Count | Type |
|---|---|---|---|
| revenue_trend | 0.28s | 79 | Single view (receipts) |
| payment_breakdown | 0.29s | 3 | Single view (receipts) |
| hourly_performance | 0.25s | 18 | Single view (receipts) |
| daily_summary | 0.38s | 31 | Single view (receipts) |
| shop_performance | 0.34s | 10 | Single view (receipts) |
| customer_analysis | **46.8s** | 50 | 2-view JOIN |
| **top_products** | **507s = 8.4 min** | 20 | 4-view JOIN |
| **category_performance** | **680s = 11.3 min** | 1 | 4-view JOIN |

**Total for all 8 templates: 1,235 seconds = 20+ minutes.**

Single-view queries are fine (~300ms). The JOIN queries are catastrophic.

### 1.4 Why JOIN Queries Are Catastrophic

- `join_buffer_size = 256KB` (too small; intermediate rows can't fit in memory)
- `innodb_buffer_pool_size = 16MB` (too small; 37,848-row table doesn't fit in RAM)
- `tmp_table_size = 16MB` (GROUP BY results spill to disk)
- No usable index on JOIN predicates — `product_id` in the view is `JSON_UNQUOTE(JSON_EXTRACT(data, '$.product_id'))`, a computed expression that MySQL cannot index

### 1.5 Why NL Queries Take 5–8 Minutes

When user asks "what are my top selling products?", the LLM generates a JOIN query against the same views. SQL execution = 8+ minutes. The LLM itself (DeepSeek) responds in seconds — the bottleneck is 100% SQL.

---

## 2. Current Code Map (Everything Being Changed)

### 2.1 View Creation

**File:** `datamind/backend/integrations.py`

| Lines | What it does |
|---|---|
| 160–168 | Helper functions `_jstr()` and `_jnum()` for JSON extraction in view DDL |
| 179–308 | `_SALESPLAY_VIEWS` dict — 7 view definitions for SalesPlay |
| 310–467 | `_LOYVERSE_VIEWS` dict — 7 view definitions for Loyverse |
| 469–472 | `_PROVIDER_VIEWS` registry |
| 475–489 | `_create_views_for_integration(conn, table_prefix, provider_id)` — called at connect time |
| 553–556 | In `connect_integration()`: calls `_create_views_for_integration()` |

### 2.2 Data Write Path

**File:** `datamind/backend/providers/upsert.py`

| Lines | What it does |
|---|---|
| 12–63 | `upsert_record()` — writes all data as JSON blob to `integration_records` |
| 66–90 | `lookup_map()` — reads from `integration_records` to build id→name maps during sync |

**File:** `datamind/backend/providers/salesplay/sync.py`

| Lines | What it does |
|---|---|
| 222–251 | `sync_shops()` — calls `upsert_record(..., "shop", ...)` |
| 254–280 | `sync_categories()` — calls `upsert_record(..., "category", ...)` |
| 340–378 | `sync_payment_types()` — calls `upsert_record(..., "payment_type", ...)` |
| 360–412 | `sync_customers()` — calls `upsert_record(..., "customer", ...)` |
| 415–459 | `sync_products()` — calls `upsert_record(..., "product", ...)` |
| 462–594 | `sync_receipts()` — calls `upsert_record(..., "receipt", ...)` and `upsert_record(..., "receipt_line_item", ...)` |

### 2.3 Analytics Query Path

**File:** `datamind/backend/providers/salesplay/analytics.py`

| Lines | What it does |
|---|---|
| 11–176 | `TEMPLATES` dict — 8 SQL templates using `{prefix}tablename` placeholders |
| 31–52 | `top_products` — 4-table JOIN (**507s**) |
| 55–74 | `customer_analysis` — 2-table JOIN (**46s**) |
| 114–135 | `category_performance` — 4-table JOIN (**680s**) |
| 187–214 | `run_salesplay_analytics(conn, table_prefix, template_id)` — executes the SQL |

### 2.4 NL Query Path (Integration Users)

**File:** `datamind/backend/main.py`

| Lines | What it does |
|---|---|
| 1155–1157 | `@v1.post("/query")` endpoint definition |
| 1166–1169 | Own-DB user path (unaffected) |
| 1171–1202 | Integration user path: INFORMATION_SCHEMA lookup for VIEW names → `tables_filter` |
| 1208–1209 | `get_table_schemas(conn, tables_filter)` — DESCRIBE each view |
| 1224–1225 | `query_to_sql(...)` — LLM generates SQL against view schemas |
| 1227–1230 | Execute SQL (this is where the 8-minute wait happens) |

### 2.5 Sync Timing (Data History Gating)

**File:** `datamind/backend/integrations.py`

| Lines | What it does |
|---|---|
| 737–748 | `_run_sync_inner()`: for full sync, reads `get_plan_history_limit()` to set `since` date |

**File:** `datamind/backend/frontend/src/pages/OnboardingWizard.jsx`

| Lines | What it does |
|---|---|
| 40 | `TOTAL_STEPS = 4` |
| 81–94 | Plan selection logic (Step 3 — AFTER sync) |
| 85–87 | `fetchBillingPlans()` called on mount — plans already fetched |
| 89–93 | `handleSelectPlan()` — subscribes after sync completes |

**Problem:** User has no plan when sync starts → `get_plan_history_limit()` falls back to Starter (1 month) even if they bought Pro. The plan must be set BEFORE `connect_integration()` is called.

### 2.6 MySQL Server Config (No Code Change)

**File:** MySQL server `my.cnf` / `my.ini`

```
innodb_buffer_pool_size = 16M  ← should be 256M minimum
tmp_table_size          = 16M  ← should be 64M
max_heap_table_size     = 16M  ← should be 64M
join_buffer_size        = 262144 (256KB) ← should be 4M
sort_buffer_size        = 524288 (512KB) ← should be 4M
```

---

## 3. The Fix: Shared Normalized Tables

### 3.1 Architecture Comparison

**Before (broken):**
```
integration_records (37,848 rows, JSON blobs)
    ↑ 7 SQL VIEWS per user (JSON_EXTRACT on every row)
        ↑ Analytics SQL JOINs views → full table scans × N
```

**After (correct):**
```
sp_receipts          (all tenants, proper columns, indexed on tenant_id + created_at)
sp_receipt_line_items (all tenants, proper columns, indexed on tenant_id + receipt_id)
sp_products          (all tenants, proper columns)
sp_customers         (all tenants, proper columns)
sp_categories        (all tenants, proper columns)
sp_shops             (all tenants, proper columns)
sp_payment_types     (all tenants, proper columns)
    ↑ Analytics SQL JOINs shared tables with WHERE tenant_id = '...'
    ↑ All JOINs use B-tree indexes → milliseconds
```

**Why "shared tables with tenant_id" scales:**
- 1,000 users × 1 provider = still 7 tables (not 7,000)
- MySQL composite primary key `(tenant_id, id)` partitions rows per tenant
- Indexes: `(tenant_id, created_at)` covers 90% of analytics queries
- Adding a new user = insert rows into existing tables (no DDL)

### 3.2 Denormalization Strategy

Instead of JOINing products→categories at query time, we store `category_name` in two places:
1. `sp_products.category_name` — populated when categories are synced
2. `sp_receipt_line_items.category_name` — populated from `sp_products` when line_items are synced

This eliminates the need for category JOINs in analytics queries entirely.

`sp_receipts` already stores `shop_name`, `customer_name`, `payment_type_name` (denormalized during sync by `lookup_map()`). Same pattern extended to line_items.

---

## 4. File-by-File Changes

### 4.1 `datamind/backend/integrations.py`

**Change 1** — Add shared table creation in `_bootstrap_db()` (after line 123):
```
CREATE TABLE IF NOT EXISTS sp_receipts (
    tenant_id VARCHAR(64) NOT NULL,
    id VARCHAR(64) NOT NULL,
    ... all receipt columns with proper types ...,
    PRIMARY KEY (tenant_id, id),
    INDEX idx_date (tenant_id, created_at),
    INDEX idx_customer (tenant_id, customer_id),
    INDEX idx_shop (tenant_id, shop_id),
    INDEX idx_type (tenant_id, receipt_type)
)

CREATE TABLE IF NOT EXISTS sp_receipt_line_items (
    tenant_id VARCHAR(64) NOT NULL,
    id VARCHAR(128) NOT NULL,
    ... all line item columns ...,
    PRIMARY KEY (tenant_id, id),
    INDEX idx_receipt (tenant_id, receipt_id),
    INDEX idx_product (tenant_id, product_id),
    INDEX idx_date (tenant_id, created_at)
)

CREATE TABLE IF NOT EXISTS sp_products (tenant_id, id, product_name, category_id, category_name, ...)
CREATE TABLE IF NOT EXISTS sp_customers (tenant_id, id, customer_name, ...)
CREATE TABLE IF NOT EXISTS sp_categories (tenant_id, id, category_name, ...)
CREATE TABLE IF NOT EXISTS sp_shops (tenant_id, id, shop_name, ...)
CREATE TABLE IF NOT EXISTS sp_payment_types (tenant_id, id, payment_name, ...)
```

**Change 2** — In `connect_integration()` at line 553–556:
Replace:
```python
_create_views_for_integration(conn, table_prefix, provider_id)
```
With:
```python
# Tables are created at bootstrap — no per-user DDL needed
pass
```
(Keep `_create_views_for_integration` function defined so old views still work during rollout,
 but stop calling it for new connections)

### 4.2 `datamind/backend/providers/upsert.py`

**Add** new function `upsert_to_shared(conn, table, tenant_id, record, pk_field="id")`:

```python
def upsert_to_shared(conn, table: str, tenant_id: str, record: dict, pk_field: str = "id"):
    """
    Write a record into a shared normalized table.
    Automatically prepends tenant_id to the record.
    ON DUPLICATE KEY UPDATE handles re-syncs cleanly.
    """
    row  = {"tenant_id": tenant_id, **{k: v for k, v in record.items() if v is not None or k == pk_field}}
    cols = list(row.keys())
    vals = [row[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    col_names    = ", ".join(f"`{c}`" for c in cols)
    updates      = ", ".join(
        f"`{c}` = VALUES(`{c}`)"
        for c in cols if c not in ("tenant_id", pk_field)
    )
    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders})"
        f" ON DUPLICATE KEY UPDATE {updates}, synced_at = NOW()",
        vals,
    )
    cursor.close()
```

### 4.3 `datamind/backend/providers/salesplay/sync.py`

**Import** `upsert_to_shared` at top of file.

**`sync_shops()`** — after `upsert_record(...)` call:
```python
upsert_to_shared(conn, "sp_shops", prefix, record)
```

**`sync_categories()`** — after `upsert_record(...)`:
```python
upsert_to_shared(conn, "sp_categories", prefix, record)
# Also back-fill category_name in sp_products for this category
cursor = conn.cursor()
cursor.execute(
    "UPDATE sp_products SET category_name = %s WHERE tenant_id = %s AND category_id = %s",
    (record["category_name"], prefix, record["id"])
)
cursor.close()
```

**`sync_payment_types()`** — after `upsert_record(...)`:
```python
upsert_to_shared(conn, "sp_payment_types", prefix, record)
```

**`sync_customers()`** — after `upsert_record(...)`:
```python
upsert_to_shared(conn, "sp_customers", prefix, record)
```

**`sync_products()`** — before `upsert_record(...)`, look up category_name; then after:
```python
# Look up category_name from already-synced sp_categories
cursor = conn.cursor()
cursor.execute(
    "SELECT category_name FROM sp_categories WHERE tenant_id=%s AND id=%s",
    (prefix, record.get("category_id"))
)
cat_row = cursor.fetchone()
cursor.close()
record["category_name"] = cat_row[0] if cat_row else None

upsert_record(...)  # existing call unchanged
upsert_to_shared(conn, "sp_products", prefix, record)
```

**`sync_receipts()`** — for each receipt:
```python
upsert_record(...)  # existing call unchanged
upsert_to_shared(conn, "sp_receipts", prefix, receipt_record)
```

For each line_item inside the receipts loop, enrich with `category_name`:
```python
# Look up category_name via product's category
cursor = conn.cursor()
cursor.execute(
    "SELECT category_name FROM sp_products WHERE tenant_id=%s AND id=%s",
    (prefix, prod_id)
)
prod_row = cursor.fetchone()
cursor.close()
li_record["category_name"] = prod_row[0] if prod_row else None
li_record["created_at"]    = receipt_dt   # denormalize receipt date to line_item

upsert_record(...)  # existing call unchanged
upsert_to_shared(conn, "sp_receipt_line_items", prefix, li_record)
```

### 4.4 `datamind/backend/providers/salesplay/analytics.py`

**Add TTL cache** at top of file:
```python
import time as _time
_result_cache: dict = {}
_CACHE_TTL = 300  # 5 minutes

def _cache_get(tenant_id: str, template_id: str):
    key = (tenant_id, template_id)
    entry = _result_cache.get(key)
    if entry and _time.monotonic() < entry[1]:
        return entry[0]
    _result_cache.pop(key, None)
    return None

def _cache_set(tenant_id: str, template_id: str, result: dict):
    _result_cache[(tenant_id, template_id)] = (result, _time.monotonic() + _CACHE_TTL)

def cache_bust(tenant_id: str):
    """Call this after a sync completes to invalidate stale cached results."""
    for key in list(_result_cache):
        if key[0] == tenant_id:
            del _result_cache[key]
```

**Rewrite `TEMPLATES`** — replace `{prefix}tablename` with `sp_tablename WHERE tenant_id='{tenant_id}'`.

New template format (all 8 templates):
```python
TEMPLATES = {
    "revenue_trend": {
        "sql": """
            SELECT DATE(created_at) AS date,
                   ROUND(SUM(total_money), 2) AS revenue,
                   COUNT(*) AS transactions,
                   ROUND(AVG(total_money), 2) AS avg_ticket
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
              AND receipt_type = 'SALE'
            GROUP BY DATE(created_at)
            ORDER BY date
        """
    },
    "top_products": {
        "sql": """
            SELECT product_name AS product,
                   COALESCE(category_name, '—') AS category,
                   SUM(quantity) AS units_sold,
                   ROUND(SUM(total_money), 2) AS revenue,
                   ROUND(AVG(price), 2) AS avg_price
            FROM sp_receipt_line_items
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY product_name, category_name
            ORDER BY revenue DESC
            LIMIT 20
        """
    },
    "customer_analysis": {
        "sql": """
            SELECT customer_name AS customer,
                   DATEDIFF(CURDATE(), MAX(created_at)) AS days_since_last_purchase,
                   COUNT(DISTINCT id) AS total_orders,
                   ROUND(SUM(total_money), 2) AS lifetime_value,
                   ROUND(AVG(total_money), 2) AS avg_order_value
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND receipt_type = 'SALE'
              AND customer_name IS NOT NULL AND customer_name != ''
            GROUP BY customer_name
            ORDER BY lifetime_value DESC
            LIMIT 50
        """
    },
    "payment_breakdown": {
        "sql": """
            SELECT COALESCE(payment_type_name, 'Unknown') AS payment_method,
                   COUNT(*) AS transaction_count,
                   ROUND(SUM(total_money), 2) AS total_revenue,
                   ROUND(AVG(total_money), 2) AS avg_transaction
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY payment_type_name
            ORDER BY total_revenue DESC
        """
    },
    "hourly_performance": {
        "sql": """
            SELECT HOUR(created_at) AS hour_of_day,
                   COUNT(*) AS transactions,
                   ROUND(SUM(total_money), 2) AS revenue,
                   ROUND(AVG(total_money), 2) AS avg_ticket
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY HOUR(created_at)
            ORDER BY hour_of_day
        """
    },
    "category_performance": {
        "sql": """
            SELECT COALESCE(category_name, 'Uncategorized') AS category,
                   COUNT(DISTINCT product_name) AS products_count,
                   SUM(quantity) AS units_sold,
                   ROUND(SUM(total_money), 2) AS revenue,
                   ROUND(AVG(price), 2) AS avg_price
            FROM sp_receipt_line_items
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY category_name
            ORDER BY revenue DESC
        """
    },
    "daily_summary": {
        "sql": """
            SELECT DATE(created_at) AS sale_date,
                   COUNT(*) AS transactions,
                   ROUND(SUM(total_money), 2) AS revenue,
                   ROUND(AVG(total_money), 2) AS avg_ticket,
                   COUNT(DISTINCT customer_id) AS unique_customers
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY DATE(created_at)
            ORDER BY sale_date DESC
        """
    },
    "shop_performance": {
        "sql": """
            SELECT COALESCE(shop_name, shop_id) AS shop,
                   COUNT(*) AS transactions,
                   ROUND(SUM(total_money), 2) AS revenue,
                   ROUND(AVG(total_money), 2) AS avg_ticket,
                   COUNT(DISTINCT customer_id) AS unique_customers
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY shop_id, shop_name
            ORDER BY revenue DESC
        """
    },
}
```

**Rewrite `run_salesplay_analytics()`**:
```python
def run_salesplay_analytics(conn, table_prefix: str, template_id: str) -> dict:
    # Check cache first
    cached = _cache_get(table_prefix, template_id)
    if cached:
        return {**cached, "source": "cache"}

    template = TEMPLATES[template_id]
    sql = template["sql"].format(tenant_id=table_prefix)

    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    cursor.close()

    data = [{col: _safe(val) for col, val in zip(cols, row)} for row in rows]
    result = {
        "title":       template["title"],
        "description": template["description"],
        "type":        template["type"],
        "columns":     cols,
        "data":        data,
        "row_count":   len(data),
    }
    _cache_set(table_prefix, template_id, result)
    return result
```

### 4.5 `datamind/backend/main.py` — NL Query for Integration Users

**Lines 1171–1202** — change `tables_filter` lookup for integration users.

**Current code** queries INFORMATION_SCHEMA for VIEW names matching `{prefix}%`.

**New code**: For integration users, set `tables_filter` to the shared table names and inject `tenant_id` into the SQL schema context. The LLM will see proper typed column schemas and generate efficient `WHERE tenant_id = '...'` queries.

```python
# Integration user — use shared normalized tables
user_conns = get_user_connections(user["email"])
if not user_conns:
    raise HTTPException(422, "No data source connected.")

prefixes = [c.get("table_prefix", "") for c in user_conns if c.get("table_prefix")]
provider_ids = [c.get("provider_id", "") for c in user_conns if c.get("provider_id")]

# Map provider to its shared tables
_PROVIDER_SHARED_TABLES = {
    "salesplay": ["sp_receipts", "sp_receipt_line_items", "sp_products",
                  "sp_customers", "sp_categories", "sp_shops", "sp_payment_types"],
    "loyverse":  ["ly_receipts", "ly_receipt_line_items", "ly_products",
                  "ly_customers", "ly_categories"],  # (when loyverse is updated)
}
tables_filter = []
tenant_ids = {}  # table_name → tenant_id for prompt injection
for prefix, pid in zip(prefixes, provider_ids):
    for tbl in _PROVIDER_SHARED_TABLES.get(pid, []):
        tables_filter.append(tbl)
        tenant_ids[tbl] = prefix

conn = _get_internal_conn()
```

Then in the LLM prompt (in `query_to_sql()`), the schema is augmented with:
```
IMPORTANT: All tables have a `tenant_id` column. You MUST add
`WHERE tenant_id = '{prefix}'` to every table reference.
The user's tenant_id is: '{prefix}'
```

This ensures the LLM always scopes to the correct tenant.

### 4.6 `datamind/backend/integrations.py` — Cache Bust on Sync Complete

In `_run_sync_inner()` after the sync result is written (around line 817), add:
```python
# Bust analytics cache so next query gets fresh data
try:
    from providers.salesplay.analytics import cache_bust
    cache_bust(table_prefix)
except Exception:
    pass
```

### 4.7 `datamind/backend/scripts/migrate_to_shared_tables.py` (New File)

One-time migration script. Reads every row in `integration_records` for SalesPlay and writes it to the appropriate `sp_*` table. Run once after deployment.

```
python datamind/backend/scripts/migrate_to_shared_tables.py
```

### 4.8 `datamind/frontend/src/pages/OnboardingWizard.jsx` — Plan Before Sync

**Current step order:**
```
Step 0 → Step 1 (connect provider + START SYNC) → Step 2 → Step 3 (pick plan)
```

**New step order:**
```
Step 0 → Step 1 (pick plan + subscribe) → Step 2 (connect provider) → Step 3 (sync progress)
```

Changes:
1. Move plan selection UI from Step 3 to Step 1 (right after LLM setup)
2. Call `subscribeToPlan(plan_id)` before any provider connection
3. `connectProvider()` is now called in Step 2 — by then the plan is already set
4. Backend `_run_sync_inner()` already reads `get_plan_history_limit()` to compute `since` (line 741–748) — no backend change needed

### 4.9 MySQL Server Config

Edit `my.cnf` (Linux) or `my.ini` (Windows) — location found via `mysql --help | grep my.cnf`:

```ini
[mysqld]
innodb_buffer_pool_size = 256M
innodb_buffer_pool_instances = 2
tmp_table_size = 64M
max_heap_table_size = 64M
join_buffer_size = 4M
sort_buffer_size = 4M
```

Restart MySQL: `net stop MySQL80 && net start MySQL80` (Windows) or `systemctl restart mysql` (Linux).

---

## 5. Predicted vs Actual Results

> Live database: `livedata@test.com` — 10,749 receipts, 26,694 line items, 5 tenants total.  
> Measured 2026-05-21 before and after deploying `fix/shared-normalized-tables-performance`.

### Analytics Hub — All 8 Templates

| Template | Before (measured) | Predicted after | **Actual after** | Predicted vs Actual |
|---|---|---|---|---|
| revenue_trend | 0.283s | ~0.05s | **0.022s** | Better than predicted |
| payment_breakdown | 0.287s | ~0.03s | **0.007s** | Better than predicted |
| hourly_performance | 0.252s | ~0.03s | **0.009s** | Better than predicted |
| daily_summary | 0.368s | ~0.05s | **0.033s** | On target |
| shop_performance | 0.337s | ~0.05s | **0.011s** | Better than predicted |
| customer_analysis | 46.8s | ~0.1s | **0.041s** | Better than predicted |
| top_products | **507s (8.4 min)** | ~0.2s | **0.035s** | **6× better than predicted** |
| category_performance | **680s (11.3 min)** | ~0.1s | **0.066s** | On target |
| **Total all 8** | **1,235s (20+ min)** | **~0.6s** | **0.224s** | **3× better than predicted** |

**Overall speedup: 5,513×** — every template now returns in under 70ms.

### EXPLAIN: top_products (the worst offender)

Before fix:

```sql
-- type=ALL on integration_records × 3 (full table scan each time)
table=integration_records  type=ALL  rows=37848  Using join buffer (flat, BNL join)
table=integration_records  type=ALL  rows=37848  Using join buffer (incremental, BNL join)
table=integration_records  type=ALL  rows=37848  ...
```

After fix:

```sql
-- type=ref — uses (tenant_id, id) PRIMARY KEY, scans only this tenant's rows
table=sp_receipt_line_items  type=ref  key=PRIMARY  rows=13315
```

The access type changed from `ALL` (full scan) to `ref` (index lookup). MySQL no longer does 37,848 × 37,848 comparisons — it reads exactly the rows belonging to that tenant.

### NL Query (natural language questions)

| Scenario | Before | Predicted | Actual |
|---|---|---|---|
| Simple question (revenue by day) | ~1s total | <3s | **<1s** |
| JOIN question (top products) | **5–8 min** | ~2–5s | **<1s** (SQL on sp_* tables) |
| LLM response time | Instant (DeepSeek) | Instant | **Instant** ✓ confirmed |

The 5–8 minute wait was never the LLM — it was the SQL executing on the views.

### Migration Stats

Script `scripts/migrate_to_shared_tables.py` — predicted ~30–60s, **actual 16.2s**:

| Table | Rows migrated | category_name enriched |
|---|---|---|
| sp_receipts | 10,848 | — (shop/customer/payment already denormalized at sync) |
| sp_receipt_line_items | 26,936 | Yes — via product→category lookup |
| sp_products | 162 | Yes — via category lookup |
| sp_customers | 600 | — |
| sp_categories | 70 | — |
| sp_shops | 18 | — |
| sp_payment_types | 44 | — |
| **Total** | **38,678 rows** | **5 tenants in 16.2s** |

---

## 6. Execution Order (Completed)

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | MySQL config documented in `docs/server-setup/mysql-config.md` | ✓ Done | `8533022` |
| 2 | Add `sp_*` shared tables to `_bootstrap_db()` in `integrations.py` | ✓ Done | `8533022` |
| 3 | Add `upsert_to_shared()` to `providers/upsert.py` | ✓ Done | `9a4a1bd` |
| 4 | Dual-write in `salesplay/sync.py` with category_name enrichment | ✓ Done | `deaa7ed` |
| 5 | Rewrite `salesplay/analytics.py` templates + 5-min TTL cache | ✓ Done | `a45aba1` |
| 6 | Stop calling `_create_views_for_integration()` in `connect_integration()` | ✓ Done | `161bd9a` |
| 7 | Fix NL query schema for integration users in `main.py` + `llm.py` | ✓ Done | `a8e95a7` |
| 8 | Run migration script — 38,678 rows in 16.2s | ✓ Done | `89daa44` |
| 9 | Move plan selection before provider connect in `OnboardingWizard.jsx` | ✓ Done | `77060f1` |
| 10 | Verify timing — all 8 templates <0.1s | ✓ Done | — |
