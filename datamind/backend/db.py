import hashlib
import os
import re
import threading
import decimal
import datetime
import mysql.connector
import mysql.connector.pooling
from typing import List, Optional, Dict, Any

# Per-user-DB connection pools.
# Each unique (host, port, db, user, password) gets a small pool of
# USER_DB_POOL_SIZE connections. Capped at MAX_USER_DB_POOLS active pools
# to prevent unbounded growth; overflow users fall back to raw connections.
_USER_DB_POOL_SIZE = int(os.getenv("USER_DB_POOL_SIZE", "3"))
_MAX_USER_DB_POOLS = int(os.getenv("MAX_USER_DB_POOLS", "50"))
_user_pools: Dict[str, mysql.connector.pooling.MySQLConnectionPool] = {}
_user_pools_lock = threading.Lock()


def _config_key(cfg: dict) -> str:
    raw = f"{cfg.get('host')}:{cfg.get('port')}:{cfg.get('database')}:{cfg.get('user')}:{cfg.get('password')}"
    return hashlib.md5(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def _get_or_create_user_pool(db_config: dict):
    key = _config_key(db_config)
    with _user_pools_lock:
        if key in _user_pools:
            return _user_pools[key]
        if len(_user_pools) >= _MAX_USER_DB_POOLS:
            return None  # too many pools — caller will use a raw connection

    # Create pool outside the lock (slow operation)
    try:
        pool = mysql.connector.pooling.MySQLConnectionPool(
            pool_name          = f"udb_{key}",
            pool_size          = _USER_DB_POOL_SIZE,
            pool_reset_session = True,
            host               = db_config.get("host", "localhost"),
            port               = int(db_config.get("port", 3306)),
            database           = db_config.get("database", ""),
            user               = db_config.get("user", "root"),
            password           = db_config.get("password", ""),
            connection_timeout = 10,
        )
    except Exception:
        return None  # pool creation failed — caller falls back to raw connection

    with _user_pools_lock:
        if key not in _user_pools:
            _user_pools[key] = pool
        return _user_pools[key]


def get_connection(db_config: dict = None):
    if db_config:
        pool = _get_or_create_user_pool(db_config)
        if pool is not None:
            try:
                return pool.get_connection()
            except Exception:
                pass  # pool exhausted or broken — fall through to raw connection
        return mysql.connector.connect(
            host               = db_config.get("host", "localhost"),
            port               = int(db_config.get("port", 3306)),
            database           = db_config.get("database", ""),
            user               = db_config.get("user", "root"),
            password           = db_config.get("password", ""),
            connection_timeout = 10,
        )
    return mysql.connector.connect(
        host               = os.getenv("DB_HOST", "localhost"),
        port               = int(os.getenv("DB_PORT", "3306")),
        database           = os.getenv("DB_NAME", ""),
        user               = os.getenv("DB_USER", "root"),
        password           = os.getenv("DB_PASSWORD", ""),
        connection_timeout = 10,
    )


def get_table_schemas(conn, tables: Optional[List[str]]) -> Dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute("SELECT DATABASE()")
    db_name = cursor.fetchone()[0]

    if tables is None:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]

    if not tables:
        return {}

    placeholders = ", ".join(["%s"] * len(tables))
    cursor.execute(
        f"""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ({placeholders})
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        [db_name, *tables],
    )
    schemas: Dict[str, Any] = {t: [] for t in tables}
    for table_name, col_name, col_type, is_null, col_key, col_default in cursor.fetchall():
        if table_name in schemas:
            schemas[table_name].append(
                {"name": col_name, "type": col_type, "null": is_null, "key": col_key, "default": col_default}
            )
    return schemas


def get_foreign_keys(conn) -> List[Dict]:
    cursor = conn.cursor()
    cursor.execute("SELECT DATABASE()")
    db_name = cursor.fetchone()[0]
    if not db_name:
        return []
    cursor.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE REFERENCED_TABLE_NAME IS NOT NULL AND TABLE_SCHEMA = %s
    """, (db_name,))
    return [{"table": r[0], "column": r[1], "ref_table": r[2], "ref_column": r[3]} for r in cursor.fetchall()]


# Matches raw DB surrogate ID columns (`id`, `customer_id`, ...) — internal
# keys with no meaning to end users; human-readable codes are used instead.
_ID_COL_RE = re.compile(r'^id$|_id$', re.IGNORECASE)


def _safe_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    return v


def run_select_and_format(conn, sql: str, set_timeout=None) -> Dict[str, Any]:
    """
    Execute one SELECT and return {"columns": [...], "data": [...]}:
      - decimal/datetime values converted to JSON-safe types
      - surrogate ID columns (id, *_id) stripped from output
      - all-NULL rows collapsed to an empty result (treated as "no data found")

    `set_timeout`, if given, is called with the new cursor before execution
    (e.g. main.py's _set_query_timeout, which caches the server's timeout
    variable across calls) — optional so callers that don't need it can omit it.

    Shared by main.py's _run_sql (legacy one-shot query path) and the MCP
    business-data tools' run_select_query, so this formatting/stripping logic
    exists in exactly one place instead of drifting apart in two.
    """
    cursor = conn.cursor()
    if set_timeout:
        set_timeout(cursor)
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    visible_cols = [c for c in cols if not _ID_COL_RE.search(c)]
    data = [
        {c: _safe_value(v) for c, v in zip(cols, row) if not _ID_COL_RE.search(c)}
        for row in rows
    ]
    if data and all(all(v is None for v in row.values()) for row in data):
        data = []
    return {"columns": visible_cols, "data": data}


def get_sample_data(conn, tables: List[str], rows: int = 3) -> Dict[str, Any]:
    import decimal, datetime
    cursor = conn.cursor()
    samples = {}
    for table in tables:
        try:
            cursor.execute(f"SELECT * FROM `{table}` LIMIT {rows}")
            cols = [d[0] for d in cursor.description]
            data = cursor.fetchall()
            def safe(v):
                if isinstance(v, decimal.Decimal): return float(v)
                if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
                return v
            samples[table] = {"columns": cols, "rows": [[safe(v) for v in row] for row in data]}
        except Exception:
            samples[table] = {"columns": [], "rows": []}
    return samples


# Semantic descriptions for known shared tables.
# These are injected into the schema text so the LLM understands the PURPOSE
# of each table, not just its columns. This prevents it from picking the wrong
# table when multiple tables share similar column names (e.g. price/cost exist
# in both sp_products and sp_receipt_line_items with very different meanings).
_TABLE_DESCRIPTIONS: Dict[str, str] = {
    "sp_products": (
        "Product catalog — one row per product. "
        "Use this for: current retail price, cost price, product details, SKU lookups. "
        "price = current selling price per unit; cost = purchase/wholesale cost per unit."
    ),
    "sp_receipt_line_items": (
        "Individual line items within sales transactions — one row per product per receipt. "
        "Use this for: sales volume, revenue, what was sold, sales history, quantity sold. "
        "price = unit price at time of sale; total_money = line revenue after discounts."
    ),
    "sp_receipts": (
        "Sales receipts — one row per completed or voided transaction. "
        "Use this for: total revenue, transaction count, payment analysis, order history."
    ),
    "sp_customers": (
        "Customer profiles with lifetime aggregates — one row per customer. "
        "Use this for: customer lookup, total spend, visit count, loyalty points, recency."
    ),
    "sp_shops": (
        "Store/branch locations — one row per shop. "
        "Use this for: branch filtering, shop details."
    ),
    "sp_categories": (
        "Product categories — one row per category. "
        "Use this for: category-level filtering or lookups."
    ),
    "sp_payment_types": (
        "Payment methods (cash, card, etc) — one row per payment type. "
        "Use this for: payment method analysis or lookups."
    ),
}


def schema_to_text(schemas: Dict[str, Any], fkeys: List[Dict] = None) -> str:
    lines = []
    for table, columns in schemas.items():
        col_defs = ", ".join(f"`{c['name']}` {c['type']}" for c in columns)
        desc = _TABLE_DESCRIPTIONS.get(table)
        if desc:
            lines.append(f"Table `{table}` [{desc}]: ({col_defs})")
        else:
            lines.append(f"Table `{table}`: ({col_defs})")
    if fkeys:
        lines.append("\nRelationships:")
        for fk in fkeys:
            lines.append(f"  {fk['table']}.{fk['column']} → {fk['ref_table']}.{fk['ref_column']}")
    return "\n".join(lines)
