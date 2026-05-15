"""
integrations.py
===============
Manages the full lifecycle of external API integrations:
  - Creating user-namespaced tables
  - Storing/retrieving encrypted credentials
  - Running full and delta syncs
  - Recording sync history
  - Scheduling background syncs

Uses DataMind's own MySQL (from .env / DATAMIND_DB_* vars) to store
integration metadata and synced data — completely separate from the
user's "bring your own DB" connection.
"""

import os
import json
import threading
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import mysql.connector
from cryptography.fernet import Fernet

from logger import get_logger
from providers import get_provider, list_providers
from providers.base import SyncResult

log = get_logger(__name__)

# In-memory sync progress store  keyed by integration_id; cleared when sync finishes
_sync_progress: Dict[int, Dict] = {}


# ── DataMind's own internal DB connection ─────────────────────────────────────

def _get_internal_conn():
    """
    Connect to DataMind's own MySQL database (not the user's DB).
    Configure via DATAMIND_DB_* env vars (or falls back to DB_* if not set).
    """
    return mysql.connector.connect(
        host=os.getenv("DATAMIND_DB_HOST") or os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DATAMIND_DB_PORT") or os.getenv("DB_PORT", "3306")),
        database=os.getenv("DATAMIND_DB_NAME") or os.getenv("DB_NAME", ""),
        user=os.getenv("DATAMIND_DB_USER") or os.getenv("DB_USER", "root"),
        password=os.getenv("DATAMIND_DB_PASSWORD") or os.getenv("DB_PASSWORD", ""),
        connection_timeout=10,
    )


# ── Credential encryption ──────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    """
    Derive a Fernet key from SECRET_KEY for credential encryption.
    In production, use a dedicated ENCRYPTION_KEY env var.
    """
    import base64, hashlib
    raw = os.getenv("ENCRYPTION_KEY") or os.getenv("SECRET_KEY", "fallback-key")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)


def _encrypt(data: dict) -> str:
    return _get_fernet().encrypt(json.dumps(data).encode()).decode()


def _decrypt(token: str) -> dict:
    return json.loads(_get_fernet().decrypt(token.encode()).decode())


# ── Bootstrap (run once at startup) ───────────────────────────────────────────

def bootstrap_integration_tables():
    """
    Create the integration management tables in DataMind's own DB.
    Safe to run on every startup (IF NOT EXISTS).
    """
    conn = _get_internal_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_integrations (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            user_email      VARCHAR(255) NOT NULL,
            provider_id     VARCHAR(50)  NOT NULL,
            display_label   VARCHAR(100),
            table_prefix    VARCHAR(100) NOT NULL,
            credentials_enc TEXT NOT NULL,
            status          ENUM('active','paused','error','syncing') DEFAULT 'active',
            last_sync_at    DATETIME,
            last_sync_rows  INT DEFAULT 0,
            last_error      TEXT,
            created_at      DATETIME DEFAULT NOW(),
            UNIQUE KEY uq_user_provider (user_email, provider_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sync_logs (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            integration_id  INT NOT NULL,
            sync_type       ENUM('full','delta') DEFAULT 'full',
            started_at      DATETIME DEFAULT NOW(),
            finished_at     DATETIME,
            rows_fetched    INT DEFAULT 0,
            rows_inserted   INT DEFAULT 0,
            rows_updated    INT DEFAULT 0,
            status          ENUM('running','success','error') DEFAULT 'running',
            error_message   TEXT,
            INDEX idx_integration (integration_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    conn.close()
    log.info("Integration tables bootstrapped")


# ── Core integration operations ───────────────────────────────────────────────

def get_integration(user_email: str, provider_id: str) -> Optional[Dict]:
    """Fetch a user's integration record (without credentials)."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, user_email, provider_id, display_label, table_prefix,
               status, last_sync_at, last_sync_rows, last_error, created_at
        FROM user_integrations
        WHERE user_email=%s AND provider_id=%s
    """, (user_email, provider_id))
    row = cursor.fetchone()
    conn.close()
    return row


def list_integrations(user_email: str) -> List[Dict]:
    """List all integrations for a user."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT id, provider_id, display_label, table_prefix,
               status, last_sync_at, last_sync_rows, last_error, created_at
        FROM user_integrations WHERE user_email=%s ORDER BY created_at DESC
    """, (user_email,))
    rows = cursor.fetchall()
    conn.close()
    # Convert datetimes to strings
    for r in rows:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
    return rows


def connect_integration(
    user_email: str,
    provider_id: str,
    creds: dict,
    display_label: str = "",
    progress_callback=None,
) -> Dict:
    """
    Full connect flow:
    1. Get provider
    2. Validate credentials
    3. Create user-namespaced tables in DataMind's DB
    4. Save integration record
    5. Trigger full sync in background
    """
    provider = get_provider(provider_id)
    table_prefix = provider.get_table_prefix(user_email)
    log.info("Connecting integration",
             user=user_email, provider=provider_id, prefix=table_prefix)

    # Validate first
    result = provider.validate_credentials(creds)
    if not result.ok:
        raise ValueError(f"Credential validation failed: {result.error}")

    # Create tables
    conn = _get_internal_conn()
    cursor = conn.cursor()
    schema_sql = provider.get_schema_sql(table_prefix)
    for statement in _split_sql(schema_sql):
        cursor.execute(statement)
    conn.commit()
    log.info("Tables created", prefix=table_prefix)

    # Save integration record
    enc_creds = _encrypt(creds)
    label = display_label or provider.manifest.display_name
    cursor.execute("""
        INSERT INTO user_integrations
          (user_email, provider_id, display_label, table_prefix, credentials_enc, status)
        VALUES (%s,%s,%s,%s,%s,'active')
        ON DUPLICATE KEY UPDATE
          display_label=VALUES(display_label),
          credentials_enc=VALUES(credentials_enc),
          status='active', last_error=NULL
    """, (user_email, provider_id, label, table_prefix, enc_creds))
    conn.commit()
    integration_id = cursor.lastrowid or _get_integration_id(cursor, user_email, provider_id)
    conn.close()

    # Trigger full sync in background
    _start_sync_thread(integration_id, user_email, provider_id, sync_type="full",
                       progress_callback=progress_callback)

    return {
        "ok": True,
        "provider_id": provider_id,
        "table_prefix": table_prefix,
        "validation_details": result.details,
        "message": "Integration connected. Full sync started in background.",
    }


def _trigger_sync(user_email: str, provider_id: str,
                  sync_type: str = "delta", progress_callback=None):
    """Manually trigger a sync (full or delta)."""
    row = get_integration(user_email, provider_id)
    if not row:
        raise ValueError("Integration not found.")
    _start_sync_thread(
        row["id"], user_email, provider_id,
        sync_type=sync_type, progress_callback=progress_callback
    )


def disconnect_integration(user_email: str, provider_id: str,
                           drop_tables: bool = False):
    """Remove an integration. Optionally drop all synced data tables."""
    row = get_integration(user_email, provider_id)
    if not row:
        raise ValueError("Integration not found.")

    conn = _get_internal_conn()
    cursor = conn.cursor()

    if drop_tables:
        provider = get_provider(provider_id)
        prefix = row["table_prefix"]
        # List tables with this prefix and drop them
        cursor.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE %s",
            (f"{prefix}%",)
        )
        for (tbl,) in cursor.fetchall():
            cursor.execute(f"DROP TABLE IF EXISTS `{tbl}`")
            log.info("Dropped provider table", table=tbl)

    cursor.execute(
        "DELETE FROM user_integrations WHERE user_email=%s AND provider_id=%s",
        (user_email, provider_id)
    )
    conn.commit()
    conn.close()
    log.info("Integration disconnected", user=user_email, provider=provider_id)


def delete_user_data(user_email: str):
    """Drop all synced tables and every DB record belonging to this user."""
    integrations = list_integrations(user_email)
    conn = _get_internal_conn()
    cursor = conn.cursor()
    for integ in integrations:
        prefix = integ.get("table_prefix", "")
        if prefix:
            cursor.execute(
                "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE %s",
                (f"{prefix}%",)
            )
            for (tbl,) in cursor.fetchall():
                cursor.execute(f"DROP TABLE IF EXISTS `{tbl}`")
                log.info("Dropped user table", user=user_email, table=tbl)
    cursor.execute(
        "DELETE sl FROM sync_logs sl "
        "JOIN user_integrations ui ON sl.integration_id = ui.id "
        "WHERE ui.user_email = %s",
        (user_email,)
    )
    cursor.execute("DELETE FROM user_integrations WHERE user_email = %s", (user_email,))
    cursor.execute("DELETE FROM user_credits WHERE user_email = %s", (user_email,))
    cursor.execute("DELETE FROM credit_usage_log WHERE user_email = %s", (user_email,))
    conn.commit()
    conn.close()
    log.info("All user data deleted", user=user_email)


def get_sync_logs(user_email: str, provider_id: str, limit: int = 20) -> List[Dict]:
    """Return recent sync log entries for an integration."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT sl.id, sl.sync_type, sl.started_at, sl.finished_at,
               sl.rows_fetched, sl.rows_inserted, sl.status, sl.error_message
        FROM sync_logs sl
        JOIN user_integrations ui ON sl.integration_id=ui.id
        WHERE ui.user_email=%s AND ui.provider_id=%s
        ORDER BY sl.started_at DESC LIMIT %s
    """, (user_email, provider_id, limit))
    rows = cursor.fetchall()
    conn.close()
    for r in rows:
        for k, v in r.items():
            if isinstance(v, datetime):
                r[k] = v.isoformat()
    return rows


# ── Internal sync machinery ───────────────────────────────────────────────────

def _get_integration_id(cursor, user_email, provider_id) -> int:
    cursor.execute(
        "SELECT id FROM user_integrations WHERE user_email=%s AND provider_id=%s",
        (user_email, provider_id)
    )
    row = cursor.fetchone()
    if not row:
        return 0
    # cursor may be a dict cursor (fetchone returns dict) or a tuple cursor
    return row["id"] if isinstance(row, dict) else row[0]


def _split_sql(sql: str) -> List[str]:
    """Split multi-statement SQL into individual statements."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def _run_sync(integration_id: int, user_email: str, provider_id: str,
              sync_type: str, progress_callback=None):
    """The actual sync worker — runs in a thread."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)

    # Mark as syncing
    cursor.execute(
        "UPDATE user_integrations SET status='syncing' WHERE id=%s",
        (integration_id,)
    )
    # Create sync log entry
    cursor.execute(
        "INSERT INTO sync_logs (integration_id, sync_type, started_at, status) "
        "VALUES (%s,%s,NOW(),'running')",
        (integration_id, sync_type)
    )
    conn.commit()
    log_id = cursor.lastrowid

    # Get credentials + table prefix
    cursor.execute(
        "SELECT credentials_enc, table_prefix, last_sync_at FROM user_integrations WHERE id=%s",
        (integration_id,)
    )
    row = cursor.fetchone()
    creds = _decrypt(row["credentials_enc"])
    table_prefix = row["table_prefix"]
    since = row["last_sync_at"] if sync_type == "delta" and row["last_sync_at"] else None
    conn.close()

    provider = get_provider(provider_id)
    log.info("Sync worker starting", user=user_email, provider=provider_id,
             sync_type=sync_type, since=str(since))

    # Set up in-memory progress tracking
    sync_start   = time.time()
    steps_done   = [0]
    rows_so_far  = [0]
    total_steps  = 6   # Salesplay has 6 entity types; providers can vary but 6 is a safe default

    def _progress(msg: str):
        clean = msg.strip()
        # A line starting with "✓" means one step just finished
        if clean.startswith("✓") or clean.startswith("✅"):
            if clean.startswith("✓"):
                steps_done[0] += 1
            # Extract row count from "✓ Shops: 314 rows" or "  ✓ Shops: 314 rows"
            try:
                count_str = clean.split(":", 1)[1].split("rows")[0].strip()
                rows_so_far[0] += int(count_str)
            except Exception:
                pass
        elapsed = int(time.time() - sync_start)
        pct     = min(int((steps_done[0] / total_steps) * 100), 99)
        _sync_progress[integration_id] = {
            "message":    clean,
            "percent":    pct,
            "rows_synced": rows_so_far[0],
            "elapsed_s":  elapsed,
        }
        if progress_callback:
            progress_callback(msg)

    # Seed initial progress so UI shows something immediately
    _sync_progress[integration_id] = {
        "message": "Starting sync…", "percent": 0,
        "rows_synced": 0, "elapsed_s": 0,
    }

    sync_conn = _get_internal_conn()
    try:
        result: SyncResult = provider.sync(
            creds=creds,
            conn=sync_conn,
            table_prefix=table_prefix,
            since=since,
            progress_callback=_progress,
        )
    finally:
        sync_conn.close()

    # Update status
    status = "success" if result.ok else "error"
    conn2 = _get_internal_conn()
    c2 = conn2.cursor()
    c2.execute("""
        UPDATE sync_logs SET
          finished_at=NOW(), status=%s,
          rows_fetched=%s, rows_inserted=%s, rows_updated=%s,
          error_message=%s
        WHERE id=%s
    """, (status, result.rows_fetched, result.rows_inserted,
          result.rows_updated, result.error or None, log_id))
    c2.execute("""
        UPDATE user_integrations SET
          status=%s, last_sync_at=IF(%s='success', NOW(), last_sync_at),
          last_sync_rows=%s, last_error=%s
        WHERE id=%s
    """, (
        "active" if result.ok else "error",
        status, result.rows_fetched,
        result.error or None,
        integration_id,
    ))
    conn2.commit()
    conn2.close()

    # Clear in-memory progress now that sync is done
    _sync_progress.pop(integration_id, None)

    log.info("Sync worker finished", user=user_email, provider=provider_id,
             status=status, rows=result.rows_fetched)


def _start_sync_thread(integration_id: int, user_email: str, provider_id: str,
                       sync_type: str = "delta", progress_callback=None):
    t = threading.Thread(
        target=_run_sync,
        args=(integration_id, user_email, provider_id, sync_type, progress_callback),
        daemon=True,
    )
    t.start()
    log.info("Sync thread started", user=user_email, provider=provider_id,
             sync_type=sync_type)


# ── Background scheduler ──────────────────────────────────────────────────────

_scheduler_running = False


def start_scheduler():
    """
    Start the background scheduler that auto-syncs all active integrations
    according to each provider's sync_interval_minutes.
    Call once at application startup.
    """
    global _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True

    def _loop():
        log.info("Integration scheduler started")
        while _scheduler_running:
            try:
                _scheduler_tick()
            except Exception as e:
                log.error("Scheduler tick failed", error=str(e))
            time.sleep(60)  # check every minute

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def _scheduler_tick():
    """Check all active integrations and sync if due."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT ui.id, ui.user_email, ui.provider_id, ui.last_sync_at
        FROM user_integrations ui
        WHERE ui.status='active'
    """)
    rows = cursor.fetchall()
    conn.close()

    from providers import REGISTRY
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    for row in rows:
        provider = REGISTRY.get(row["provider_id"])
        if not provider:
            continue
        interval = provider.manifest.sync_interval_minutes
        if interval <= 0:
            continue  # manual only
        last = row["last_sync_at"]
        if last is None:
            continue  # not yet synced (connect flow handles first sync)
        elapsed = (now - last).total_seconds() / 60
        if elapsed >= interval:
            log.info("Scheduler triggering delta sync",
                     user=row["user_email"], provider=row["provider_id"],
                     elapsed_minutes=round(elapsed, 1))
            _start_sync_thread(row["id"], row["user_email"],
                               row["provider_id"], sync_type="delta")


# ── Compatibility wrappers for main.py API routes ─────────────────────────────

def connect_provider(user_email: str, provider_id: str, credentials: dict) -> str:
    """
    Connect a provider and return a connection_id string.
    Wraps connect_integration.
    """
    connect_integration(user_email, provider_id, credentials)
    # Return a stable connection_id = "provider_id" for simplicity
    # (one connection per provider per user in current design)
    return provider_id


def disconnect_provider(user_email: str, connection_id: str):
    """Disconnect a provider by connection_id (= provider_id)."""
    disconnect_integration(user_email, connection_id)


def get_user_connections(user_email: str) -> List[Dict]:
    """
    Return all active connections for a user, enriched with provider manifest data.
    Maps backend status to frontend-expected values.
    """
    import dataclasses
    from providers import get_provider
    rows = list_integrations(user_email)
    result = []
    for row in rows:
        pid = row.get("provider_id")
        try:
            p = get_provider(pid)
            m = p.manifest
            
            # Map backend status to frontend expectations
            backend_status = row.get("status", "pending")
            if backend_status == "active":
                frontend_status = "connected"
            elif backend_status == "syncing":
                frontend_status = "syncing"
            elif backend_status == "error":
                frontend_status = "error"
            elif backend_status == "paused":
                frontend_status = "paused"
            else:
                frontend_status = "pending"
            
            result.append({
                "connection_id":    pid,
                "provider_id":      pid,
                "display_name":     m.display_name,
                "logo_emoji":       m.logo_emoji,
                "category":         m.category,
                "last_sync_at":     row.get("last_sync_at"),
                "last_sync_status": frontend_status,  # ← FIXED: Map to frontend values
                "connected_at":     row.get("created_at"),
                "table_prefix":     row.get("table_prefix"),
            })
        except Exception:
            result.append({**row, "connection_id": pid, "display_name": pid})
    return result


def get_connection_status(user_email: str, connection_id: str) -> Dict:
    """
    Return live status for a single connection.
    Maps backend status to frontend-expected values.
    """
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    try:
        integration_id = _get_integration_id(cursor, user_email, connection_id)
        cursor.execute("""
            SELECT status, last_sync_at, last_sync_rows, table_prefix,
                   last_error
            FROM user_integrations
            WHERE id = %s
        """, (integration_id,))
        row = cursor.fetchone() or {}

        # Count total rows using actual COUNT(*) — information_schema is approximate for InnoDB
        total_rows = 0
        table_prefix = row.get("table_prefix", "")
        if table_prefix:
            try:
                cursor.execute("""
                    SELECT TABLE_NAME FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE %s
                """, (f"{table_prefix}%",))
                tables = [r["TABLE_NAME"] for r in cursor.fetchall()]
                for tbl in tables:
                    cursor.execute(f"SELECT COUNT(*) AS cnt FROM `{tbl}`")
                    r = cursor.fetchone()
                    total_rows += int((r or {}).get("cnt") or 0)
            except Exception as e:
                log.warning("Row count failed", prefix=table_prefix, error=str(e))

        # Map backend status to frontend
        backend_status = row.get("status", "unknown")
        if backend_status == "active":
            frontend_status = "connected"
        elif backend_status == "syncing":
            frontend_status = "syncing"
        elif backend_status == "error":
            frontend_status = "error"
        elif backend_status == "paused":
            frontend_status = "paused"
        else:
            frontend_status = "pending"

        progress = _sync_progress.get(integration_id) if backend_status == "syncing" else None

        return {
            "status": frontend_status,
            "last_sync_at": str(row["last_sync_at"]) if row.get("last_sync_at") else None,
            "last_sync_rows": row.get("last_sync_rows", 0),
            "total_rows": total_rows,
            "last_error": row.get("last_error"),
            "progress": progress,
        }
    finally:
        conn.close()


def get_user_total_rows(user_email: str) -> int:
    """Sum COUNT(*) across all synced tables for every integration the user has."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT table_prefix FROM user_integrations WHERE user_email=%s",
            (user_email,)
        )
        prefixes = [r["table_prefix"] for r in cursor.fetchall()]
        total = 0
        for prefix in prefixes:
            cursor.execute("""
                SELECT TABLE_NAME FROM information_schema.TABLES
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME LIKE %s
            """, (f"{prefix}%",))
            tables = [r["TABLE_NAME"] for r in cursor.fetchall()]
            for tbl in tables:
                cursor.execute(f"SELECT COUNT(*) AS cnt FROM `{tbl}`")
                r = cursor.fetchone()
                total += int((r or {}).get("cnt") or 0)
        return total
    except Exception as e:
        log.warning("get_user_total_rows failed", user=user_email, error=str(e))
        return 0
    finally:
        conn.close()


def get_sync_history(user_email: str, connection_id: str) -> List[Dict]:
    """Return sync history for a connection."""
    logs = get_sync_logs(user_email, connection_id, limit=20)
    return [
        {
            "started_at":  str(l.get("started_at", "")),
            "finished_at": str(l.get("finished_at", "")) if l.get("finished_at") else None,
            "status":      l.get("status"),
            "rows_synced": l.get("rows_fetched", 0),
            "error":       l.get("error_message"),
        }
        for l in logs
    ]


def trigger_sync(user_email: str, connection_id: str, full: bool = False):
    """Trigger a sync for a connection. full=True forces a full re-sync."""
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    try:
        integration_id = _get_integration_id(cursor, user_email, connection_id)
        if not integration_id:
            raise ValueError("Integration not found.")
        sync_type = "full" if full else "delta"
        _trigger_sync(user_email, connection_id, sync_type=sync_type)
    except Exception as e:
        log.error("trigger_sync failed", user=user_email, connection_id=connection_id, error=str(e))
    finally:
        conn.close()