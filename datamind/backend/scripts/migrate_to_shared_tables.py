"""
migrate_to_shared_tables.py
===========================
One-time migration: copies existing SalesPlay data from integration_records
(JSON blobs) into the new sp_* shared normalized tables.

Run once after deploying the fix/shared-normalized-tables-performance branch:
    cd datamind/backend
    python scripts/migrate_to_shared_tables.py

Safe to re-run — uses ON DUPLICATE KEY UPDATE so no duplicates are created.
Does NOT delete anything from integration_records.

Progress is printed to stdout. On a 38k-row integration_records table
this completes in ~30-60 seconds.
"""

import json
import os
import sys
import time

# Add backend to path so db / upsert imports work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from providers.upsert import upsert_to_shared

# ── Connection ────────────────────────────────────────────────────────────────

def get_conn():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "datamind_db"),
        user=os.getenv("DB_USER", "datamind_user"),
        password=os.getenv("DB_PASSWORD", "datamind_pass"),
        connection_timeout=30,
    )


# ── Field extractors (mirror what the views / sync produce) ───────────────────

def ensure_tables(conn):
    """Create sp_* tables if they don't exist (same DDL as _bootstrap_db)."""
    cur = conn.cursor()
    stmts = [
        """CREATE TABLE IF NOT EXISTS sp_receipts (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            receipt_number VARCHAR(100), shop_id VARCHAR(64), shop_name VARCHAR(255),
            customer_id VARCHAR(64), customer_name VARCHAR(255),
            created_at DATETIME, updated_at DATETIME,
            total_money DECIMAL(14,4) DEFAULT 0, total_discount DECIMAL(14,4) DEFAULT 0,
            total_tax DECIMAL(14,4) DEFAULT 0, receipt_type VARCHAR(30) DEFAULT 'SALE',
            status VARCHAR(30) DEFAULT 'COMPLETED', payment_type_id VARCHAR(64),
            payment_type_name VARCHAR(255), payment_amount DECIMAL(14,4) DEFAULT 0,
            synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_date (tenant_id, created_at),
            INDEX idx_customer (tenant_id, customer_id),
            INDEX idx_shop (tenant_id, shop_id),
            INDEX idx_type (tenant_id, receipt_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_receipt_line_items (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(128) NOT NULL,
            receipt_id VARCHAR(64) NOT NULL, product_id VARCHAR(64),
            variant_id VARCHAR(64), product_name VARCHAR(500), category_name VARCHAR(255),
            sku VARCHAR(100), quantity DECIMAL(12,4) DEFAULT 0, price DECIMAL(12,4) DEFAULT 0,
            gross_total_money DECIMAL(14,4) DEFAULT 0, total_discount DECIMAL(14,4) DEFAULT 0,
            total_money DECIMAL(14,4) DEFAULT 0, cost DECIMAL(12,4) DEFAULT 0,
            created_at DATETIME, synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_receipt (tenant_id, receipt_id),
            INDEX idx_product (tenant_id, product_id),
            INDEX idx_date (tenant_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_products (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            product_name VARCHAR(500), description TEXT, category_id VARCHAR(64),
            category_name VARCHAR(255), reference_id VARCHAR(100), sku VARCHAR(100),
            barcode VARCHAR(100), price DECIMAL(12,4) DEFAULT 0, cost DECIMAL(12,4) DEFAULT 0,
            is_active TINYINT(1) DEFAULT 1, created_at DATETIME, updated_at DATETIME,
            synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_cat (tenant_id, category_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_customers (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            customer_name VARCHAR(255), email VARCHAR(255), phone_number VARCHAR(50),
            customer_code VARCHAR(100), note TEXT, total_visits INT DEFAULT 0,
            total_spent DECIMAL(14,4) DEFAULT 0, points_balance DECIMAL(12,2) DEFAULT 0,
            created_at DATETIME, updated_at DATETIME, synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_categories (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            category_name VARCHAR(255), color VARCHAR(20), shop_id VARCHAR(64),
            created_at DATETIME, updated_at DATETIME, synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_shops (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            shop_name VARCHAR(255), address VARCHAR(500), phone VARCHAR(50),
            email VARCHAR(255), currency VARCHAR(10), country VARCHAR(10),
            timezone VARCHAR(100), status VARCHAR(20), updated_at DATETIME,
            synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE IF NOT EXISTS sp_payment_types (
            tenant_id VARCHAR(64) NOT NULL, id VARCHAR(64) NOT NULL,
            payment_name VARCHAR(255), payment_type VARCHAR(50), is_active TINYINT(1) DEFAULT 1,
            shop_id VARCHAR(64), created_at DATETIME, updated_at DATETIME,
            synced_at DATETIME DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ]
    for stmt in stmts:
        cur.execute(stmt)
    conn.commit()
    cur.close()
    print("sp_* tables ensured.")


def _safe_dec(val, default=0.0):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default

def _safe_int(val, default=0):
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _map_shop(data: dict) -> dict:
    return {
        "id":         data.get("id", ""),
        "shop_name":  data.get("shop_name"),
        "address":    data.get("address"),
        "phone":      data.get("phone"),
        "email":      data.get("email"),
        "status":     data.get("status"),
        "updated_at": data.get("updated_at"),
    }


def _map_category(data: dict) -> dict:
    return {
        "id":            data.get("id", ""),
        "category_name": data.get("category_name"),
        "color":         data.get("color"),
        "shop_id":       data.get("shop_id"),
        "created_at":    data.get("created_at"),
        "updated_at":    data.get("updated_at"),
    }


def _map_payment_type(data: dict) -> dict:
    return {
        "id":           data.get("id", ""),
        "payment_name": data.get("payment_name"),
        "created_at":   data.get("created_at"),
        "updated_at":   data.get("updated_at"),
    }


def _map_customer(data: dict) -> dict:
    return {
        "id":             data.get("id", ""),
        "customer_name":  data.get("customer_name"),
        "email":          data.get("email"),
        "phone_number":   data.get("phone_number"),
        "customer_code":  data.get("customer_code"),
        "note":           data.get("note"),
        "total_visits":   _safe_int(data.get("total_visits")),
        "total_spent":    _safe_dec(data.get("total_spent")),
        "points_balance": _safe_dec(data.get("points_balance")),
        "created_at":     data.get("created_at"),
        "updated_at":     data.get("updated_at"),
    }


def _map_product(data: dict) -> dict:
    return {
        "id":           data.get("id", ""),
        "product_name": data.get("product_name"),
        "description":  data.get("description"),
        "category_id":  data.get("category_id"),
        "reference_id": data.get("reference_id"),
        "sku":          data.get("sku"),
        "barcode":      data.get("barcode"),
        "price":        _safe_dec(data.get("price")),
        "cost":         _safe_dec(data.get("cost")),
        "is_active":    _safe_int(data.get("is_active"), 1),
        "created_at":   data.get("created_at"),
        "updated_at":   data.get("updated_at"),
    }


def _map_receipt(data: dict, external_created_at) -> dict:
    return {
        "id":                data.get("id", ""),
        "receipt_number":    data.get("receipt_number"),
        "shop_id":           data.get("shop_id"),
        "shop_name":         data.get("shop_name"),
        "customer_id":       data.get("customer_id"),
        "customer_name":     data.get("customer_name"),
        "created_at":        str(external_created_at) if external_created_at else data.get("created_at"),
        "updated_at":        data.get("updated_at"),
        "total_money":       _safe_dec(data.get("total_money")),
        "total_discount":    _safe_dec(data.get("total_discount")),
        "total_tax":         _safe_dec(data.get("total_tax")),
        "receipt_type":      data.get("receipt_type", "SALE"),
        "status":            data.get("status", "COMPLETED"),
        "payment_type_id":   data.get("payment_type_id"),
        "payment_type_name": data.get("payment_type_name"),
        "payment_amount":    _safe_dec(data.get("payment_amount")),
    }


def _map_line_item(data: dict, external_created_at) -> dict:
    return {
        "id":                data.get("id", ""),
        "receipt_id":        data.get("receipt_id"),
        "product_id":        data.get("product_id"),
        "variant_id":        data.get("variant_id"),
        "product_name":      data.get("product_name"),
        "sku":               data.get("sku"),
        "quantity":          _safe_dec(data.get("quantity"), 0),
        "price":             _safe_dec(data.get("price"), 0),
        "gross_total_money": _safe_dec(data.get("gross_total_money"), 0),
        "total_discount":    _safe_dec(data.get("total_discount"), 0),
        "total_money":       _safe_dec(data.get("total_money"), 0),
        "cost":              _safe_dec(data.get("cost"), 0),
        "created_at":        str(external_created_at) if external_created_at else data.get("created_at"),
    }


# ── Record type → shared table mapping ───────────────────────────────────────

RECORD_MAP = {
    "shop":              ("sp_shops",              _map_shop),
    "category":          ("sp_categories",         _map_category),
    "payment_type":      ("sp_payment_types",      _map_payment_type),
    "customer":          ("sp_customers",          _map_customer),
    "product":           ("sp_products",           _map_product),
    "receipt":           ("sp_receipts",           None),  # special — uses external_created_at
    "receipt_line_item": ("sp_receipt_line_items", None),  # special — uses external_created_at
}


# ── Main migration ────────────────────────────────────────────────────────────

def migrate_tenant(conn, tenant_id: str, provider_id: str = "salesplay"):
    print(f"\n  Migrating tenant: {tenant_id} ({provider_id})")

    # Build category and product lookup maps for enrichment
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT external_id, data FROM integration_records "
        "WHERE tenant_id=%s AND provider_id=%s AND record_type='category'",
        (tenant_id, provider_id)
    )
    cat_map = {}  # category_id → category_name
    for row in cur.fetchall():
        d = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        cat_map[row["external_id"]] = d.get("category_name", "")

    cur.execute(
        "SELECT external_id, data FROM integration_records "
        "WHERE tenant_id=%s AND provider_id=%s AND record_type='product'",
        (tenant_id, provider_id)
    )
    prod_cat_map = {}  # product_id → category_name
    for row in cur.fetchall():
        d = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        cat_id = d.get("category_id", "")
        prod_cat_map[row["external_id"]] = cat_map.get(cat_id, "")
    cur.close()

    # Process each record type in dependency order
    order = ["shop", "category", "payment_type", "customer", "product",
             "receipt", "receipt_line_item"]

    counts = {}
    for record_type in order:
        if record_type not in RECORD_MAP:
            continue
        table, mapper = RECORD_MAP[record_type]
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT external_id, data, external_created_at FROM integration_records "
            "WHERE tenant_id=%s AND provider_id=%s AND record_type=%s",
            (tenant_id, provider_id, record_type)
        )
        rows = cur.fetchall()
        cur.close()

        n = 0
        for row in rows:
            d = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            ext_created = row["external_created_at"]

            if record_type == "receipt":
                record = _map_receipt(d, ext_created)
            elif record_type == "receipt_line_item":
                record = _map_line_item(d, ext_created)
                # Enrich with category_name from product lookup
                pid = d.get("product_id", "")
                record["category_name"] = prod_cat_map.get(pid, "")
            else:
                record = mapper(d)
                # Enrich products with category_name
                if record_type == "product":
                    cat_id = d.get("category_id", "")
                    record["category_name"] = cat_map.get(cat_id, "")

            upsert_to_shared(conn, table, tenant_id, record)
            n += 1

        conn.commit()
        counts[record_type] = n
        print(f"    {record_type:25s} → {table}: {n:,} rows")

    return counts


def run():
    t0 = time.time()
    conn = get_conn()
    print("Connected to database.")

    # Find all SalesPlay tenants in integration_records
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT tenant_id FROM integration_records WHERE provider_id='salesplay'"
    )
    tenants = [r[0] for r in cur.fetchall()]
    cur.close()

    if not tenants:
        print("No SalesPlay tenants found in integration_records. Nothing to migrate.")
        conn.close()
        return

    print(f"Found {len(tenants)} SalesPlay tenant(s): {tenants}")
    ensure_tables(conn)

    total = {}
    for tenant in tenants:
        counts = migrate_tenant(conn, tenant, "salesplay")
        for rt, n in counts.items():
            total[rt] = total.get(rt, 0) + n

    conn.close()
    elapsed = time.time() - t0
    print(f"\nMigration complete in {elapsed:.1f}s")
    print("Totals:")
    for rt, n in total.items():
        print(f"  {rt}: {n:,} rows")


if __name__ == "__main__":
    run()
