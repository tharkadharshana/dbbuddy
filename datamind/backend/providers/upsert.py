"""
providers/upsert.py
===================
Shared helper for writing integration data into the unified integration_records table.
All provider sync.py files call upsert_record() instead of writing to per-user tables.
"""

import json
from typing import Any, Dict, Optional


def upsert_record(
    conn,
    tenant_id: str,
    user_email: str,
    provider_id: str,
    record_type: str,
    record: Dict[str, Any],
    id_field: str = "id",
    ext_created_field: Optional[str] = None,
    ext_updated_field: Optional[str] = None,
    budget=None,
) -> bool:
    """
    Upsert a single normalized record into integration_records.

    Args:
        conn:               MySQL connection to DataMind's internal DB
        tenant_id:          table_prefix, e.g. 'dm_a1b2c3_salesplay'
        user_email:         e.g. 'john@example.com'
        provider_id:        e.g. 'salesplay'
        record_type:        e.g. 'receipt', 'product', 'shop'
        record:             dict with normalized field names matching old table columns
        id_field:           field in record that holds the external PK
        ext_created_field:  field name for external created_at (optional)
        ext_updated_field:  field name for external updated_at (optional)
        budget:             RowBudget instance — checked before insert

    Returns:
        True if inserted/updated, False if skipped due to budget
    """
    if budget is not None and not budget.request():
        return False

    external_id = str(record.get(id_field) or "")
    data_json   = json.dumps(record, default=str)
    ext_created = record.get(ext_created_field) if ext_created_field else None
    ext_updated = record.get(ext_updated_field) if ext_updated_field else None

    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO integration_records
            (tenant_id, user_email, provider_id, record_type,
             external_id, data, external_created_at, external_updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            data                = VALUES(data),
            external_updated_at = VALUES(external_updated_at),
            updated_at          = NOW(3)
    """, (tenant_id, user_email, provider_id, record_type,
          external_id, data_json, ext_created, ext_updated))
    cursor.close()
    return True


def upsert_to_shared(conn, table: str, tenant_id: str, record: dict,
                     pk_field: str = "id") -> None:
    """
    Write a record into a shared normalized table (sp_receipts, sp_products, etc.).

    Prepends tenant_id automatically so callers don't have to.
    Uses ON DUPLICATE KEY UPDATE so re-syncs overwrite cleanly.
    Skips None values for non-PK columns to avoid overwriting good data with NULL.
    """
    if not record.get(pk_field):
        return

    row = {"tenant_id": tenant_id}
    for k, v in record.items():
        # Always include PK; skip other None values to avoid nulling out good columns
        if v is not None or k == pk_field:
            row[k] = v

    cols         = list(row.keys())
    vals         = [row[c] for c in cols]
    placeholders = ", ".join(["%s"] * len(cols))
    col_names    = ", ".join(f"`{c}`" for c in cols)
    updates      = ", ".join(
        f"`{c}` = VALUES(`{c}`)"
        for c in cols if c not in ("tenant_id", pk_field)
    )
    if not updates:
        return

    cursor = conn.cursor()
    cursor.execute(
        f"INSERT INTO `{table}` ({col_names}) VALUES ({placeholders})"
        f" ON DUPLICATE KEY UPDATE {updates}, synced_at = NOW()",
        vals,
    )
    cursor.close()


def lookup_map(conn, tenant_id: str, provider_id: str, record_type: str,
               id_field: str, name_field: str) -> Dict[str, str]:
    """
    Build an id→name dict from already-synced records in integration_records.
    Used by sync_receipts to enrich receipt records with shop/customer/payment names
    without hitting per-user tables (which no longer exist).

    Example:
        shop_map = lookup_map(conn, prefix, 'salesplay', 'shop', 'id', 'shop_name')
        # → {'123': 'Main Store', '456': 'Branch'}
    """
    # JSON_UNQUOTE(JSON_EXTRACT(...)) used for MariaDB 10.4 compatibility.
    # ->>'$.x' shorthand requires MariaDB 10.5+.
    sql = f"""
        SELECT
            JSON_UNQUOTE(JSON_EXTRACT(data, '$.{id_field}'))   AS id,
            JSON_UNQUOTE(JSON_EXTRACT(data, '$.{name_field}')) AS name
        FROM integration_records
        WHERE tenant_id = %s AND provider_id = %s AND record_type = %s
    """
    cursor = conn.cursor()
    cursor.execute(sql, (tenant_id, provider_id, record_type))
    rows = cursor.fetchall()
    cursor.close()
    return {str(r[0]): str(r[1] or "") for r in rows if r[0] is not None}
