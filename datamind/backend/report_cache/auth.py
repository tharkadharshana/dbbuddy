"""
report_cache/auth.py
=====================
Resolves a tenant's stored SalesPlay API token for report_cache jobs.

Reuses the existing encrypted-credential mechanism (Fernet, integrations.py)
rather than a new one — per-tenant tokens already live encrypted in
user_integrations.credentials_enc, keyed by (user_email, provider_id), with
table_prefix as the tenant_id used across every sp_* table (see
docs/unified-db-schema-migration.md). integrations.get_integration() looks
up by (user_email, provider_id) and deliberately omits credentials_enc from
its result; report_cache jobs only have tenant_id, so this module queries by
table_prefix directly instead.
"""

from typing import Optional

import db
from integrations import _decrypt
from logger import get_logger

log = get_logger(__name__)


def get_report_token(tenant_id: str, provider_id: str = "salesplay") -> Optional[str]:
    """Decrypt and return the stored POS API token for a tenant, or None if
    the tenant has no active integration of this provider."""
    conn = db.get_connection()
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
