"""
report_cache/auth.py
=====================
Resolves a tenant's stored SalesPlay API token, and the DataMind user_email
behind a tenant_id, for report_cache jobs.

Reuses the existing encrypted-credential mechanism (Fernet, integrations.py)
rather than a new one — per-tenant tokens already live encrypted in
user_integrations.credentials_enc, keyed by (user_email, provider_id), with
table_prefix as the tenant_id used across every sp_* table (see
docs/unified-db-schema-migration.md). integrations.get_integration() looks
up by (user_email, provider_id) and deliberately omits credentials_enc from
its result; report_cache jobs only have tenant_id, so this module queries by
table_prefix directly instead.

Connects via pool.get_internal_conn() — NOT db.get_connection(). user_integrations
lives in the core DB (DATAMIND_DB_*), and db.get_connection(db_config=None) only
reads DB_HOST/DB_NAME/DB_USER/DB_PASSWORD (the "user default DB" fallback, blank
in most .env files) — see the same bug fixed in scripts/run_migration.py
(docs/plan/CHANGELOG.md, "Post-review fixes" #3). pool.get_internal_conn() is
the one helper with the correct DATAMIND_DB_* -> DB_* fallback chain.
"""

from typing import Optional

import pool
from integrations import _decrypt
from logger import get_logger

log = get_logger(__name__)


def get_report_token(tenant_id: str, provider_id: str = "salesplay") -> Optional[str]:
    """Decrypt and return the stored POS API token for a tenant, or None if
    the tenant has no active integration of this provider."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        # disconnect_integration() deletes the row (integrations.py) — no
        # 'disconnected' status exists, so any row found here is a live
        # integration (status one of 'active'/'paused'/'error'/'syncing').
        cursor.execute(
            "SELECT credentials_enc FROM user_integrations "
            "WHERE table_prefix=%s AND provider_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id, provider_id),
        )
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if row is None:
        log.warning("get_report_token: no integration found", tenant=tenant_id, provider=provider_id)
        return None

    try:
        creds = _decrypt(row["credentials_enc"])
    except Exception as exc:
        log.error("get_report_token: decryption failed", tenant=tenant_id, error=str(exc))
        return None

    token = creds.get("api_token", "").strip()
    return token or None


def get_tenant_user_email(tenant_id: str, provider_id: str = "salesplay") -> Optional[str]:
    """Resolve the DataMind user_email behind a tenant_id (table_prefix).

    Needed because billing.py's plan/tier functions are keyed by user_email,
    not tenant_id — see report_cache/tiers.py, which uses this to bridge the
    two identifiers rather than threading user_email through every caller."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_email FROM user_integrations "
            "WHERE table_prefix=%s AND provider_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (tenant_id, provider_id),
        )
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if row is None:
        log.warning("get_tenant_user_email: no integration found", tenant=tenant_id, provider=provider_id)
        return None
    return row["user_email"]
