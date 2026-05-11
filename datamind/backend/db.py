import mysql.connector
import os
from typing import List, Optional, Dict, Any


def get_connection(db_config: dict = None):
    if db_config:
        return mysql.connector.connect(
            host=db_config.get("host", "localhost"),
            port=int(db_config.get("port", 3306)),
            database=db_config.get("database", ""),
            user=db_config.get("user", "root"),
            password=db_config.get("password", ""),
            connection_timeout=10,
        )
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", ""),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        connection_timeout=10,
    )


def get_table_schemas(conn, tables: Optional[List[str]]) -> Dict[str, Any]:
    cursor = conn.cursor()
    if tables is None:
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
    schemas = {}
    for table in tables:
        cursor.execute(f"DESCRIBE `{table}`")
        columns = cursor.fetchall()
        schemas[table] = [
            {"name": col[0], "type": col[1], "null": col[2], "key": col[3], "default": col[4]}
            for col in columns
        ]
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


def schema_to_text(schemas: Dict[str, Any], fkeys: List[Dict] = None) -> str:
    lines = []
    for table, columns in schemas.items():
        col_defs = ", ".join(f"`{c['name']}` {c['type']}" for c in columns)
        lines.append(f"Table `{table}`: ({col_defs})")
    if fkeys:
        lines.append("\nRelationships:")
        for fk in fkeys:
            lines.append(f"  {fk['table']}.{fk['column']} → {fk['ref_table']}.{fk['ref_column']}")
    return "\n".join(lines)

def discover_dynamic_mapping(conn) -> Dict[str, Any]:
    """
    Scans the database to map logical entities to actual table names.
    Supports multiple integrations by identifying all related tables.
    """
    cursor = conn.cursor()
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall()]

    mapping = {
        "SALES_TABLES": ["invoices"],
        "ITEM_TABLES":  ["invoice_items"],
        "PRODUCT_TABLES":["products"],
        "CUSTOMER_TABLES":["customers"],
        "SHOP_TABLES":   ["locations"],
        "PRIMARY_SALES": "invoices"
    }

    found_sales = []
    found_items = []
    found_products = []

    for t in tables:
        t_low = t.lower()
        if t_low.startswith("dm_"):
            if "receipt" in t_low and "item" not in t_low: found_sales.append(t)
            if "receipt" in t_low and "item" in t_low:     found_items.append(t)
            if "product" in t_low:                         found_products.append(t)
            
    if found_sales:
        mapping["SALES_TABLES"] = found_sales
        mapping["PRIMARY_SALES"] = found_sales[0]
    if found_items:
        mapping["ITEM_TABLES"] = found_items
    if found_products:
        mapping["PRODUCT_TABLES"] = found_products

    return mapping
