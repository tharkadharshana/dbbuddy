# MySQL Configuration for DataMind AI

## Why These Changes Are Required

DataMind AI uses a multi-tenant analytics architecture where all customer data lives in
shared normalized tables (`sp_receipts`, `sp_receipt_line_items`, etc.). Analytics queries
JOIN these tables for each customer. The default XAMPP/MySQL config is tuned for tiny
systems and causes JOIN queries to spill to disk, making them take 8–11 minutes instead
of milliseconds.

**Root cause (measured live):**
- `innodb_buffer_pool_size = 16MB` → table data cannot fit in RAM, every query hits disk
- `join_buffer_size = 256KB` → intermediate JOIN rows can't fit in memory → disk spill
- `tmp_table_size = 16MB` → GROUP BY aggregations spill to disk temp files

---

## RedHat / CentOS / RHEL Server Setup

### MySQL Config File Location

| OS | Config file path |
|---|---|
| RHEL / CentOS 7–9 | `/etc/my.cnf` |
| RHEL / CentOS (alternate) | `/etc/mysql/my.cnf` |
| Ubuntu / Debian | `/etc/mysql/mysql.conf.d/mysqld.cnf` |
| XAMPP (Windows) | `C:\xampp\mysql\bin\my.ini` |

Find the active config: `mysql --help | grep "Default options" -A1`

### Install MySQL 8.0 on RHEL 9

```bash
# Add MySQL repo
sudo dnf install -y https://dev.mysql.com/get/mysql80-community-release-el9-1.noarch.rpm
sudo dnf install -y mysql-community-server

# Start and enable
sudo systemctl enable --now mysqld

# Get the temporary root password
sudo grep 'temporary password' /var/log/mysqld.log

# Secure the installation
sudo mysql_secure_installation
```

### Required Config Changes

Edit `/etc/my.cnf` and add/update these values under `[mysqld]`:

```ini
[mysqld]
# ── Character set ─────────────────────────────────────────────────────────────
character-set-server    = utf8mb4
collation-server        = utf8mb4_general_ci

# ── InnoDB buffer pool ────────────────────────────────────────────────────────
# Set to 50-70% of available RAM.
# With 4GB RAM  → 2048M
# With 8GB RAM  → 4096M
# With 16GB RAM → 8192M
# Minimum for DataMind: 256M (development), 1024M (production)
innodb_buffer_pool_size     = 1024M
innodb_buffer_pool_instances = 4      # 1 per 1GB of buffer pool

# ── InnoDB log ────────────────────────────────────────────────────────────────
innodb_log_file_size        = 256M    # 25% of buffer pool size
innodb_log_buffer_size      = 64M
innodb_flush_log_at_trx_commit = 1   # 1 = safest (ACID), 2 = faster but 1s data loss risk

# ── Temp tables (GROUP BY, ORDER BY, subqueries) ──────────────────────────────
# Analytics queries build large temp tables. If they exceed this, they spill to disk.
tmp_table_size              = 256M
max_heap_table_size         = 256M

# ── JOIN buffer (Block Nested Loop joins) ─────────────────────────────────────
# Each JOIN operation gets this much RAM. DataMind JOINs up to 4 tables.
join_buffer_size            = 8M
sort_buffer_size            = 8M

# ── Connection limits ─────────────────────────────────────────────────────────
max_connections             = 200     # DataMind pool: 20 per worker, allow 5 workers + overhead
wait_timeout                = 600
interactive_timeout         = 600

# ── Query performance ─────────────────────────────────────────────────────────
innodb_io_capacity          = 1000    # SSD: 2000, HDD: 200
innodb_io_capacity_max      = 2000
innodb_read_io_threads      = 8
innodb_write_io_threads     = 8

# ── Logging ───────────────────────────────────────────────────────────────────
slow_query_log              = ON
slow_query_log_file         = /var/log/mysql/slow.log
long_query_time             = 2       # Log queries slower than 2s

# ── JSON support (required for integration_records table) ─────────────────────
# MySQL 5.7.8+ and 8.0+ have native JSON. No config needed.
# Ensure MySQL >= 5.7.8 is installed.

[client]
default-character-set = utf8mb4
```

### Apply the Config

```bash
# Validate config before restarting
sudo mysqld --validate-config

# Restart MySQL
sudo systemctl restart mysqld

# Verify settings took effect
mysql -u root -p -e "SHOW VARIABLES LIKE 'innodb_buffer_pool_size';"
mysql -u root -p -e "SHOW VARIABLES LIKE 'tmp_table_size';"
mysql -u root -p -e "SHOW VARIABLES LIKE 'join_buffer_size';"
```

### Create DataMind Database and User

```sql
-- Run as root
CREATE DATABASE IF NOT EXISTS datamind_db
  CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

CREATE USER IF NOT EXISTS 'datamind_user'@'localhost'
  IDENTIFIED WITH mysql_native_password BY 'YOUR_STRONG_PASSWORD_HERE';

GRANT ALL PRIVILEGES ON datamind_db.* TO 'datamind_user'@'localhost';
FLUSH PRIVILEGES;
```

Update `datamind/backend/.env`:
```
DB_HOST=localhost
DB_PORT=3306
DB_NAME=datamind_db
DB_USER=datamind_user
DB_PASSWORD=YOUR_STRONG_PASSWORD_HERE
DB_POOL_SIZE=20
```

---

## Minimum vs Recommended Specs

| Resource | Minimum (dev) | Recommended (prod) |
|---|---|---|
| RAM | 4 GB | 16 GB |
| CPU | 2 cores | 4+ cores |
| Disk | 20 GB SSD | 100 GB NVMe SSD |
| MySQL | 5.7.8+ | 8.0+ |
| `innodb_buffer_pool_size` | 256 MB | 8 GB |
| `tmp_table_size` | 64 MB | 256 MB |

---

## XAMPP (Windows Development Only)

Config file: `C:\xampp\mysql\bin\my.ini`

Add/update under `[mysqld]`:

```ini
innodb_buffer_pool_size   = 256M
innodb_buffer_pool_instances = 2
innodb_log_file_size      = 64M
tmp_table_size            = 64M
max_heap_table_size       = 64M
join_buffer_size          = 4M
sort_buffer_size          = 4M
```

Restart MySQL via XAMPP Control Panel → MySQL → Stop → Start.

---

## Verify Performance After Config Change

```sql
-- Should show 256M+ (or whatever you set)
SHOW VARIABLES LIKE 'innodb_buffer_pool_size';

-- Run EXPLAIN on a JOIN query — should NOT show "Using join buffer (flat, BNL join)" anymore
EXPLAIN
SELECT product_name, SUM(total_money)
FROM sp_receipt_line_items
WHERE tenant_id = 'dm_2180422af798eb91_salesplay'
  AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
GROUP BY product_name
ORDER BY 2 DESC LIMIT 20;
-- Expected: type=ref, Using index condition (not type=ALL)
```
