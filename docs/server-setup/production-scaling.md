# Production Scaling Guide — DataMind AI

## What Happens at 1,000 Concurrent Users

### Data at Scale

One real user (`livedata@test.com`) with 3 months of SalesPlay data:

| Table | Rows | Data | Indexes |
|---|---|---|---|
| sp_receipt_line_items | 26,694 | 5.5 MB | 14.6 MB |
| sp_receipts | 10,749 | 3.5 MB | 6.6 MB |
| sp_customers | 583 | 0.1 MB | 0.1 MB |

Extrapolated to 1,000 users:

| Table | Rows | Data | Indexes | Total |
|---|---|---|---|---|
| sp_receipt_line_items | **26.7M** | 5.5 GB | 14.6 GB | **20.1 GB** |
| sp_receipts | **10.7M** | 3.5 GB | 6.6 GB | **10.1 GB** |
| sp_customers | 583K | 0.1 GB | 0.1 GB | 0.2 GB |
| integration_records | **37.8M** | 10.3 GB | 6.3 GB | **16.6 GB** |

**Total: ~47 GB of data** across the main tables.

With `innodb_buffer_pool_size = 16MB`, MySQL can cache 0.03% of that. Every query reads from disk.

---

## Failure Modes in Order

### 1. MySQL Connection Exhaustion (breaks first, instantly)

```
max_connections    = 151   (MySQL hard limit)
DB_POOL_SIZE       = 20    (per uvicorn worker)
Workers            = 4     (typical production)
App connections    = 80
Available for sync = 71

1,000 concurrent users → "Too many connections" error
```

**Fix:** Raise `max_connections`, add a connection pooler (ProxySQL or PgBouncer-equivalent for MySQL).

### 2. Thread Pool Saturation (breaks within seconds)

Every NL query blocks a uvicorn thread synchronously:
- LLM call: 2–5 seconds (DeepSeek)
- SQL execution: 0.1–2 seconds
- Total: 2–7 seconds per query

With 4 workers × 4 threads = 16 concurrent NL queries:
```
Throughput = 16 threads / 5s avg = 3.2 queries/second
1,000 users / 3.2 = 312 seconds queue time per user
```

**Fix:** Make LLM calls async (aiohttp), increase worker count, add Redis query cache.

### 3. Buffer Pool Thrashing (every query, constantly)

```
innodb_buffer_pool_size = 16MB
Data at 1,000 users     = 47GB
Cache ratio             = 0.03%
Disk reads per query    = ~100%
```

**Fix:** Set buffer pool to 50–70% of RAM. For 16GB server: 8–10 GB.

### 4. `wait_timeout = 28,800` (8 hours!)

Idle database connections are held open for 8 hours. At scale:
- User logs in, connection acquired from pool
- User goes idle for 2 hours
- Connection is held for 6 more hours
- Pool exhausted by idle connections

**Fix:** Set `wait_timeout = 300` (5 minutes).

### 5. `max_allowed_packet = 1MB`

`integration_records.data` stores full JSON blobs. A SalesPlay receipt with 30 line items serialized as JSON can exceed 1MB. Silently fails to write.

**Fix:** Set `max_allowed_packet = 64M`.

### 6. Log File Too Small (`innodb_log_file_size = 5MB`)

Each sync writes to BOTH `integration_records` AND `sp_*` tables (dual-write). With heavy sync traffic, MySQL fills the 5MB log file, triggers a checkpoint flush, and stalls all writes for seconds.

**Fix:** Set `innodb_log_file_size = 256M` (25% of buffer pool).

---

## MySQL Config: Development → Production

### Current (broken for production)

```ini
innodb_buffer_pool_size = 16M     ← should be 8G on 16GB server
max_connections         = 151     ← exhausted by 8 uvicorn workers
tmp_table_size          = 16M     ← GROUP BY spills to disk
join_buffer_size        = 262144  ← 256KB, JOIN buffers spill
wait_timeout            = 28800   ← 8 hours! connection leak
max_allowed_packet      = 1M      ← too small for JSON blobs
innodb_log_file_size    = 5M      ← too small, constant flushes
slow_query_log          = OFF     ← blind to slow queries
```

### Required (for 1,000 users on a 16GB RAM server)

Edit `/etc/my.cnf` (RHEL) — see `docs/server-setup/mysql-config.md` for the full file.

```ini
[mysqld]
# ── Memory ───────────────────────────────────────────────────────────────────
# Buffer pool: 50-70% of available RAM
# 16GB server → 8GB. 32GB server → 20GB. 8GB server → 4GB.
innodb_buffer_pool_size     = 8G
innodb_buffer_pool_instances = 8      # 1 per GB of pool

# Temp tables for GROUP BY / ORDER BY (analytics uses these heavily)
tmp_table_size              = 256M
max_heap_table_size         = 256M

# JOIN buffers — each JOIN operation gets this much RAM
join_buffer_size            = 8M
sort_buffer_size            = 8M

# ── InnoDB log (write throughput) ────────────────────────────────────────────
# 25% of buffer pool size. Larger = fewer checkpoint flushes under write load.
innodb_log_file_size        = 2G
innodb_log_buffer_size      = 64M
innodb_flush_log_at_trx_commit = 1   # 1=safe (ACID). Set 2 for 10x faster writes
                                      # with 1-second data loss risk on crash.

# ── Connections ───────────────────────────────────────────────────────────────
max_connections             = 500    # 4 workers × 20 pool + 80 headroom + sync threads
wait_timeout                = 300    # Kill idle connections after 5 minutes (not 8 hours)
interactive_timeout         = 300

# ── Packet size (for JSON blobs in integration_records) ───────────────────────
max_allowed_packet          = 64M

# ── I/O (SSD: 4000-8000, NVMe: 10000+, HDD: 200) ─────────────────────────────
innodb_io_capacity          = 4000
innodb_io_capacity_max      = 8000
innodb_read_io_threads      = 8
innodb_write_io_threads     = 8

# ── Observability ────────────────────────────────────────────────────────────
slow_query_log              = ON
slow_query_log_file         = /var/log/mysql/slow.log
long_query_time             = 1      # Log queries > 1 second
```

**Apply:**
```bash
sudo mysqld --validate-config        # check for errors first
sudo systemctl restart mysqld
```

---

## Application Layer Fixes (Beyond MySQL Config)

These require code changes — listed in priority order.

### 1. Redis Cache for NL Query Results (highest impact)

Same user asking "top products" 10 times should hit a cache, not call DeepSeek 10 times.

```
Redis key:  md5(user_email + question + schema_version)
TTL:        5 minutes (or bust on sync complete)
```

Without Redis, 1,000 users × 5 identical questions = 5,000 DeepSeek API calls.
With Redis, 1,000 users × 5 identical questions = 5 DeepSeek calls + 4,995 cache hits.

### 2. Connection Pooler: ProxySQL

ProxySQL sits between your app and MySQL:
- App maintains 80 connections to ProxySQL
- ProxySQL multiplexes to 20 real MySQL connections
- 1,000 users share 20 real connections via queueing
- Automatic read/write split when you add a read replica

```
App (4 workers × 20 pool) → ProxySQL → MySQL primary (writes)
                                      → MySQL replica (reads, analytics)
```

Install on RHEL:
```bash
sudo dnf install proxysql
sudo systemctl enable --now proxysql
# Configure in /etc/proxysql.cnf
```

### 3. Async LLM Calls

Currently every NL query holds a uvicorn thread for 2–30 seconds (synchronous `requests.post()`).

Switch to `aiohttp`:
```python
# Current (blocks thread)
resp = requests.post(url, json=body, timeout=90)

# Fixed (frees thread while waiting)
async with aiohttp.ClientSession() as session:
    async with session.post(url, json=body, timeout=90) as resp:
        data = await resp.json()
```

With async LLM calls: 16 threads handle 1,000 concurrent users with no queue.

### 4. uvicorn Workers

For production (4-core server):
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

For production (8-core server):
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 8
```

Each worker = separate Python process with its own DB pool. Do NOT use `--reload` in production.

### 5. Read Replica for Analytics

Analytics and NL queries are read-only. Add a MySQL read replica and route those there:

```
Writes (sync, user settings, billing) → Primary MySQL
Reads  (NL queries, analytics, reports) → Replica MySQL
```

This doubles read throughput with zero changes to data integrity.

---

## Database Partitioning (at 10M+ rows per table)

When `sp_receipt_line_items` grows past 30–50M rows, even indexed queries slow down. Add MySQL RANGE partitioning by `created_at`:

```sql
ALTER TABLE sp_receipt_line_items
PARTITION BY RANGE (YEAR(created_at)) (
    PARTITION p2024 VALUES LESS THAN (2025),
    PARTITION p2025 VALUES LESS THAN (2026),
    PARTITION p2026 VALUES LESS THAN (2027),
    PARTITION pmax  VALUES LESS THAN MAXVALUE
);
```

Queries with `WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)` then only scan the current-year partition instead of the whole table.

**Note:** MySQL partitioning requires the partition key in the primary key. Our current `PRIMARY KEY (tenant_id, id)` would need to become `PRIMARY KEY (tenant_id, id, created_at)`.

---

## What to Do Right Now (Priority Order)

### Immediate (no code, just config — do today)

| Action | File | Impact |
|---|---|---|
| Apply `my.ini` changes | `C:\xampp\mysql\bin\my.ini` (dev) or `/etc/my.cnf` (prod) | Fixes buffer pool, connection limits, wait_timeout |
| Restart MySQL | XAMPP Control Panel or `systemctl restart mysqld` | Config takes effect |
| Turn on slow query log | `slow_query_log = ON, long_query_time = 1` | Catch regressions early |

### Short-term (1–2 days)

| Action | Effort | Impact |
|---|---|---|
| Redis for NL query result cache | 1 day | 10× throughput on repeated questions |
| Increase uvicorn workers to CPU count | 5 min | Linear throughput scaling |
| ProxySQL connection pooler | 2 hours | Handles connection burst from 1,000 users |

### Medium-term (1–2 weeks)

| Action | Effort | Impact |
|---|---|---|
| Async LLM calls (aiohttp) | 2 days | Free threads during LLM wait |
| MySQL read replica | 1 day | Double read throughput |
| Partition sp_* tables by year | 4 hours | Future-proofs for 50M+ rows |

---

## Recommended Server Specs for 1,000 Active Users

| Component | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores | 8 cores |
| RAM | 16 GB | 32 GB |
| Disk | 200 GB SSD | 500 GB NVMe SSD |
| MySQL `innodb_buffer_pool_size` | 8 GB | 20 GB |
| uvicorn workers | 4 | 8 |
| Redis | 1 GB instance | 4 GB instance |
| MySQL max_connections | 300 | 500 |

A single 32GB / 8-core RHEL server with proper config and ProxySQL handles 1,000 concurrent users comfortably. At 5,000+ users, add a read replica and horizontal API scaling (2× uvicorn servers behind a load balancer).

---

## Quick Diagnostic Commands

Run these on any live server to check health:

```sql
-- Is the buffer pool big enough? Hit rate should be > 99%
SELECT
    (1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests) * 100
    AS hit_rate_pct
FROM (
    SELECT
        (SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS
         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_reads') AS Innodb_buffer_pool_reads,
        (SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_STATUS
         WHERE VARIABLE_NAME = 'Innodb_buffer_pool_read_requests') AS Innodb_buffer_pool_read_requests
) s;

-- Are we near the connection limit?
SHOW STATUS LIKE 'Max_used_connections';
SHOW VARIABLES LIKE 'max_connections';

-- Slow queries since last restart
SHOW STATUS LIKE 'Slow_queries';

-- Temp tables spilling to disk (should be low)
SHOW STATUS LIKE 'Created_tmp_disk_tables';
SHOW STATUS LIKE 'Created_tmp_tables';
-- Ratio: disk/total should be < 5%

-- Lock waits (contention under load)
SHOW STATUS LIKE 'Innodb_row_lock_waits';
```
