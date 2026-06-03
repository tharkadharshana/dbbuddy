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
from pool import get_internal_conn as _get_internal_conn

log = get_logger(__name__)

# In-memory sync progress store keyed by integration_id; cleared when sync finishes
_sync_progress: Dict[int, Dict] = {}

# Guard set: integration IDs currently running a sync thread.
# Prevents double-sync from scheduler + manual trigger racing on the same integration.
_sync_active: set = set()

# Max concurrent sync threads. Each sync borrows 3 pool connections.
# MAX_CONCURRENT_SYNCS × 3 must leave headroom in DB_POOL_SIZE.
# Default 5 syncs × 3 connections = 15 connections (safe with pool_size=20).
_MAX_CONCURRENT_SYNCS = int(os.getenv("MAX_CONCURRENT_SYNCS", "5"))

# Cache for get_integration() — result is static between syncs.
# Every analytics click called get_integration() for a fresh DB query even though
# table_prefix / status / display_label never change between syncs.
_integration_cache: dict = {}            # (user_email, provider_id) → (row, expires_at)
_integration_cache_lock = threading.Lock()
_INTEGRATION_CACHE_TTL = int(os.getenv("INTEGRATION_CACHE_TTL", "300"))  # 5 minutes


def _invalidate_integration_cache(user_email: str, provider_id: str) -> None:
    """Bust a single integration entry. Call on connect, disconnect, or sync completion."""
    with _integration_cache_lock:
        _integration_cache.pop((user_email, provider_id), None)


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
    Create all integration management tables in DataMind's own DB.
    Safe to run on every startup (IF NOT EXISTS).
    Includes the unified integration_records and integration_sync_state tables.
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
    # Unified multi-tenant records table — replaces all dm_*_* per-user tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS integration_records (
            id                   BIGINT       NOT NULL AUTO_INCREMENT,
            tenant_id            VARCHAR(64)  NOT NULL,
            user_email           VARCHAR(255) NOT NULL,
            provider_id          VARCHAR(50)  NOT NULL,
            record_type          VARCHAR(50)  NOT NULL,
            external_id          VARCHAR(255) NOT NULL,
            data                 JSON         NOT NULL,
            external_created_at  DATETIME,
            external_updated_at  DATETIME,
            synced_at            DATETIME(3)  NOT NULL DEFAULT NOW(3),
            created_at           DATETIME(3)  NOT NULL DEFAULT NOW(3),
            updated_at           DATETIME(3)  NOT NULL DEFAULT NOW(3) ON UPDATE NOW(3),
            PRIMARY KEY (id),
            UNIQUE KEY uq_record (tenant_id, provider_id, record_type, external_id),
            INDEX idx_query (tenant_id, provider_id, record_type, synced_at DESC),
            INDEX idx_user  (user_email, provider_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=COMPRESSED
    """)
    # Sync cursor state per tenant/provider/record_type
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS integration_sync_state (
            id              INT          NOT NULL AUTO_INCREMENT,
            tenant_id       VARCHAR(64)  NOT NULL,
            user_email      VARCHAR(255) NOT NULL,
            provider_id     VARCHAR(50)  NOT NULL,
            record_type     VARCHAR(50)  NOT NULL,
            last_synced_at  DATETIME(3),
            status          ENUM('ok','error','syncing') DEFAULT 'ok',
            error_message   TEXT,
            updated_at      DATETIME(3)  NOT NULL DEFAULT NOW(3) ON UPDATE NOW(3),
            PRIMARY KEY (id),
            UNIQUE KEY uq_state (tenant_id, provider_id, record_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # ── SalesPlay shared normalized tables ─────────────────────────────────────
    # Shared across ALL tenants — tenant_id is part of every primary key.
    # This replaces per-user views over integration_records for analytics queries.
    # JOINs between these tables use proper B-tree indexes → milliseconds not minutes.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_receipts (
            tenant_id           VARCHAR(64)   NOT NULL,
            id                  VARCHAR(64)   NOT NULL,
            receipt_number      VARCHAR(100),
            shop_id             VARCHAR(64),
            shop_name           VARCHAR(255),
            customer_id         VARCHAR(64),
            customer_name       VARCHAR(255),
            created_at          DATETIME,
            updated_at          DATETIME,
            total_money         DECIMAL(14,4) DEFAULT 0,
            total_discount      DECIMAL(14,4) DEFAULT 0,
            total_tax           DECIMAL(14,4) DEFAULT 0,
            points_earned       DECIMAL(12,2) DEFAULT 0,
            points_deducted     DECIMAL(12,2) DEFAULT 0,
            note                TEXT,
            receipt_type        VARCHAR(30)   DEFAULT 'SALE',
            status              VARCHAR(30)   DEFAULT 'COMPLETED',
            payment_type_id     VARCHAR(64),
            payment_type_name   VARCHAR(255),
            payment_amount      DECIMAL(14,4) DEFAULT 0,
            synced_at           DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_date     (tenant_id, created_at),
            INDEX idx_customer (tenant_id, customer_id),
            INDEX idx_shop     (tenant_id, shop_id),
            INDEX idx_type     (tenant_id, receipt_type),
            INDEX idx_id       (id)           -- JOIN lookup without tenant_id
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_receipt_line_items (
            tenant_id       VARCHAR(64)   NOT NULL,
            id              VARCHAR(128)  NOT NULL,
            receipt_id      VARCHAR(64)   NOT NULL,
            product_id      VARCHAR(64),
            variant_id      VARCHAR(64),
            product_name    VARCHAR(500),
            category_name   VARCHAR(255),
            sku             VARCHAR(100),
            quantity        DECIMAL(12,4) DEFAULT 0,
            price           DECIMAL(12,4) DEFAULT 0,
            gross_total_money DECIMAL(14,4) DEFAULT 0,
            total_discount  DECIMAL(14,4) DEFAULT 0,
            total_money     DECIMAL(14,4) DEFAULT 0,
            cost            DECIMAL(12,4) DEFAULT 0,
            created_at      DATETIME,
            synced_at       DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_receipt      (tenant_id, receipt_id),
            INDEX idx_product      (tenant_id, product_id),
            INDEX idx_date         (tenant_id, created_at),
            INDEX idx_bare_receipt (receipt_id),  -- JOIN without tenant_id
            INDEX idx_bare_product (product_id)   -- JOIN without tenant_id
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_products (
            tenant_id       VARCHAR(64)   NOT NULL,
            id              VARCHAR(64)   NOT NULL,
            product_name    VARCHAR(500),
            description     TEXT,
            category_id     VARCHAR(64),
            category_name   VARCHAR(255),
            reference_id    VARCHAR(100),
            sku             VARCHAR(100),
            barcode         VARCHAR(100),
            price           DECIMAL(12,4) DEFAULT 0,
            cost            DECIMAL(12,4) DEFAULT 0,
            is_active       TINYINT(1)    DEFAULT 1,
            created_at      DATETIME,
            updated_at      DATETIME,
            synced_at       DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_cat (tenant_id, category_id),
            INDEX idx_id  (id)                    -- JOIN without tenant_id
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_customers (
            tenant_id           VARCHAR(64)   NOT NULL,
            id                  VARCHAR(64)   NOT NULL,
            customer_name       VARCHAR(255),
            email               VARCHAR(255),
            phone_number        VARCHAR(50),
            customer_code       VARCHAR(100),
            note                TEXT,
            total_visits        INT           DEFAULT 0,
            total_spent         DECIMAL(14,4) DEFAULT 0,
            points_balance      DECIMAL(12,2) DEFAULT 0,
            last_purchase_date  DATETIME      DEFAULT NULL,
            created_at          DATETIME,
            updated_at          DATETIME,
            synced_at           DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id),
            INDEX idx_id        (id)                  -- JOIN without tenant_id
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Migration: add last_purchase_date if it was missing from a pre-9de48df install
    cursor.execute("""
        ALTER TABLE sp_customers
        ADD COLUMN IF NOT EXISTS last_purchase_date DATETIME DEFAULT NULL
        AFTER points_balance
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_categories (
            tenant_id       VARCHAR(64)   NOT NULL,
            id              VARCHAR(64)   NOT NULL,
            category_name   VARCHAR(255),
            color           VARCHAR(20),
            shop_id         VARCHAR(64),
            created_at      DATETIME,
            updated_at      DATETIME,
            synced_at       DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_shops (
            tenant_id   VARCHAR(64)   NOT NULL,
            id          VARCHAR(64)   NOT NULL,
            shop_name   VARCHAR(255),
            address     VARCHAR(500),
            phone       VARCHAR(50),
            email       VARCHAR(255),
            currency    VARCHAR(10),
            country     VARCHAR(10),
            timezone    VARCHAR(100),
            status      VARCHAR(20),
            updated_at  DATETIME,
            synced_at   DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sp_payment_types (
            tenant_id    VARCHAR(64)   NOT NULL,
            id           VARCHAR(64)   NOT NULL,
            payment_name VARCHAR(255),
            payment_type VARCHAR(50),
            is_active    TINYINT(1)    DEFAULT 1,
            shop_id      VARCHAR(64),
            created_at   DATETIME,
            updated_at   DATETIME,
            synced_at    DATETIME      DEFAULT NOW(),
            PRIMARY KEY (tenant_id, id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # ── Conversation memory tables ────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id            VARCHAR(64)  NOT NULL,
            user_email    VARCHAR(255) NOT NULL,
            title         VARCHAR(255) NOT NULL DEFAULT 'New conversation',
            summary       TEXT,
            message_count INT          NOT NULL DEFAULT 0,
            created_at    DATETIME     NOT NULL DEFAULT NOW(),
            updated_at    DATETIME     NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id),
            INDEX idx_conv_user_updated (user_email, updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id              BIGINT       NOT NULL AUTO_INCREMENT,
            conversation_id VARCHAR(64)  NOT NULL,
            role            VARCHAR(16)  NOT NULL,
            content         TEXT         NOT NULL,
            sql_query       TEXT,
            row_count       INT          NOT NULL DEFAULT 0,
            data_snapshot   JSON,
            created_at      DATETIME     NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id),
            INDEX idx_cmsg_conv (conversation_id, created_at),
            FOREIGN KEY (conversation_id)
                REFERENCES conversations(id) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            id              BIGINT      NOT NULL AUTO_INCREMENT,
            conversation_id VARCHAR(64) NOT NULL,
            summary_text    TEXT        NOT NULL,
            covers_up_to_id BIGINT      NOT NULL,
            created_at      DATETIME    NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id),
            INDEX idx_csum_conv (conversation_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    conn.commit()

    # Reset any integrations stuck in 'syncing' from a previous server crash.
    # On a clean shutdown the status is always updated to 'active' or 'error',
    # but a hard restart (SIGKILL, code reload, OOM) leaves the DB in 'syncing'
    # permanently since the thread that would have updated it is gone.
    cursor.execute("""
        UPDATE user_integrations
        SET status = 'error',
            last_error = 'Sync was interrupted by a server restart. Will retry automatically.'
        WHERE status = 'syncing'
    """)
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    if affected:
        log.warning("Reset stuck syncing integrations on startup", count=affected)
    log.info("Integration tables bootstrapped")


# ── SQL view creation ─────────────────────────────────────────────────────────
# Views are named identically to the old per-user tables so analytics SQL
# (salesplay/analytics.py, loyverse/analytics.py) works unchanged.
# Views are created once per integration at connect time.

# MariaDB 10.4 compatible JSON extraction.
# ->>'$.x' is MySQL 5.7.13+ / MariaDB 10.5+ only.
# Use JSON_UNQUOTE(JSON_EXTRACT(col, '$.x')) for string fields,
# and JSON_EXTRACT(col, '$.x') (no UNQUOTE) inside CAST for numeric fields.

def _jstr(field: str) -> str:
    """Extract a string JSON field — MariaDB 10.4 compatible."""
    return f"JSON_UNQUOTE(JSON_EXTRACT(data, '$.{field}'))"

def _jnum(field: str) -> str:
    """Extract a numeric JSON field for use inside CAST."""
    return f"JSON_EXTRACT(data, '$.{field}')"


_SALESPLAY_VIEWS = {
    "{prefix}shops": """
        SELECT
            {jid}                                          AS id,
            {jshop_name}                                   AS shop_name,
            {jaddress}                                     AS address,
            {jphone}                                       AS phone,
            {jemail}                                       AS email,
            {jstatus}                                      AS status,
            {jupdated_at}                                  AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='shop'
    """.format(
        jid=_jstr("id"), jshop_name=_jstr("shop_name"), jaddress=_jstr("address"),
        jphone=_jstr("phone"), jemail=_jstr("email"), jstatus=_jstr("status"),
        jupdated_at=_jstr("updated_at"),
    ),
    "{prefix}categories": """
        SELECT
            {jid}          AS id,
            {jcat}         AS category_name,
            {jcreated}     AS created_at,
            {jupdated}     AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='category'
    """.format(jid=_jstr("id"), jcat=_jstr("category_name"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}payment_types": """
        SELECT
            {jid}      AS id,
            {jname}    AS payment_name,
            {jcreated} AS created_at,
            {jupdated} AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='payment_type'
    """.format(jid=_jstr("id"), jname=_jstr("payment_name"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}products": """
        SELECT
            {jid}                                              AS id,
            {jname}                                            AS product_name,
            {jcat}                                             AS category_id,
            {jsku}                                             AS sku,
            CAST({jprice} AS DECIMAL(12,4))                    AS price,
            CAST({jcost}  AS DECIMAL(12,4))                    AS cost,
            {jcreated}                                         AS created_at,
            {jupdated}                                         AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='product'
    """.format(jid=_jstr("id"), jname=_jstr("product_name"), jcat=_jstr("category_id"),
               jsku=_jstr("sku"), jprice=_jnum("price"), jcost=_jnum("cost"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}customers": """
        SELECT
            {jid}                                              AS id,
            {jname}                                            AS customer_name,
            {jemail}                                           AS email,
            {jphone}                                           AS phone_number,
            CAST({jspent}   AS DECIMAL(14,4))                  AS total_spent,
            CAST({jvisits}  AS UNSIGNED)                       AS total_visits,
            CAST({jpoints}  AS DECIMAL(12,2))                  AS points_balance,
            {jcreated}                                         AS created_at,
            {jupdated}                                         AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='customer'
    """.format(jid=_jstr("id"), jname=_jstr("customer_name"), jemail=_jstr("email"),
               jphone=_jstr("phone_number"), jspent=_jnum("total_spent"),
               jvisits=_jnum("total_visits"), jpoints=_jnum("points_balance"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}receipts": """
        SELECT
            {jid}                                              AS id,
            {jrn}                                              AS receipt_number,
            {jshop_id}                                         AS shop_id,
            {jshop_name}                                       AS shop_name,
            {jcust_id}                                         AS customer_id,
            {jcust_name}                                       AS customer_name,
            external_created_at                                AS created_at,
            {jupdated}                                         AS updated_at,
            CAST({jtotal}    AS DECIMAL(14,4))                 AS total_money,
            CAST({jdisc}     AS DECIMAL(14,4))                 AS total_discount,
            CAST({jtax}      AS DECIMAL(14,4))                 AS total_tax,
            {jrtype}                                           AS receipt_type,
            {jstatus}                                          AS status,
            {jpay_id}                                          AS payment_type_id,
            {jpay_name}                                        AS payment_type_name,
            CAST({jpay_amt}  AS DECIMAL(14,4))                 AS payment_amount,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='receipt'
    """.format(
        jid=_jstr("id"), jrn=_jstr("receipt_number"), jshop_id=_jstr("shop_id"),
        jshop_name=_jstr("shop_name"), jcust_id=_jstr("customer_id"),
        jcust_name=_jstr("customer_name"), jupdated=_jstr("updated_at"),
        jtotal=_jnum("total_money"), jdisc=_jnum("total_discount"), jtax=_jnum("total_tax"),
        jrtype=_jstr("receipt_type"), jstatus=_jstr("status"),
        jpay_id=_jstr("payment_type_id"), jpay_name=_jstr("payment_type_name"),
        jpay_amt=_jnum("payment_amount"),
    ),
    "{prefix}receipt_line_items": """
        SELECT
            {jid}                                              AS id,
            {jrid}                                             AS receipt_id,
            {jpid}                                             AS product_id,
            {jvid}                                             AS variant_id,
            {jpname}                                           AS product_name,
            {jsku}                                             AS sku,
            CAST({jqty}   AS DECIMAL(12,4))                    AS quantity,
            CAST({jprice} AS DECIMAL(12,4))                    AS price,
            CAST({jgross} AS DECIMAL(14,4))                    AS gross_total_money,
            CAST({jdisc}  AS DECIMAL(14,4))                    AS total_discount,
            CAST({jtotal} AS DECIMAL(14,4))                    AS total_money,
            CAST({jcost}  AS DECIMAL(12,4))                    AS cost,
            {jcreated}                                         AS created_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='salesplay' AND record_type='receipt_line_item'
    """.format(
        jid=_jstr("id"), jrid=_jstr("receipt_id"), jpid=_jstr("product_id"),
        jvid=_jstr("variant_id"), jpname=_jstr("product_name"), jsku=_jstr("sku"),
        jqty=_jnum("quantity"), jprice=_jnum("price"), jgross=_jnum("gross_total_money"),
        jdisc=_jnum("total_discount"), jtotal=_jnum("total_money"), jcost=_jnum("cost"),
        jcreated=_jstr("created_at"),
    ),
}

_LOYVERSE_VIEWS = {
    "{prefix}_stores": """
        SELECT
            {jid}      AS id,
            {jname}    AS name,
            {jaddr}    AS address,
            {jphone}   AS phone_number,
            {jdesc}    AS description,
            {jcreated} AS created_at,
            {jupdated} AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='shop'
    """.format(jid=_jstr("id"), jname=_jstr("name"), jaddr=_jstr("address"),
               jphone=_jstr("phone_number"), jdesc=_jstr("description"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}_employees": """
        SELECT
            {jid}       AS id,
            {jname}     AS name,
            {jemail}    AS email,
            {jrole}     AS role,
            {jstore}    AS store_id,
            {jcreated}  AS created_at,
            {jupdated}  AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='employee'
    """.format(jid=_jstr("id"), jname=_jstr("name"), jemail=_jstr("email"),
               jrole=_jstr("role"), jstore=_jstr("store_id"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}_categories": """
        SELECT
            {jid}    AS id,
            {jname}  AS name,
            {jcolor} AS color,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='category'
    """.format(jid=_jstr("id"), jname=_jstr("name"), jcolor=_jstr("color")),
    "{prefix}_products": """
        SELECT
            {jid}                            AS id,
            {jhandle}                        AS handle,
            {jitem}                          AS item_name,
            {jdesc}                          AS description,
            {jcat}                           AS category_id,
            CAST({jstock} AS UNSIGNED)       AS track_stock,
            {jcreated}                       AS created_at,
            {jupdated}                       AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='product'
    """.format(jid=_jstr("id"), jhandle=_jstr("handle"), jitem=_jstr("item_name"),
               jdesc=_jstr("description"), jcat=_jstr("category_id"),
               jstock=_jnum("track_stock"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}_variants": """
        SELECT
            {jid}                                AS id,
            {jitem}                              AS item_id,
            {jsku}                               AS sku,
            {jbar}                               AS barcode,
            CAST({jcost}  AS DECIMAL(12,4))      AS cost,
            CAST({jprice} AS DECIMAL(12,4))      AS default_price,
            {jstores}                            AS stores_json,
            {jcreated}                           AS created_at,
            {jupdated}                           AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='variant'
    """.format(jid=_jstr("id"), jitem=_jstr("item_id"), jsku=_jstr("sku"),
               jbar=_jstr("barcode"), jcost=_jnum("cost"), jprice=_jnum("default_price"),
               jstores=_jstr("stores_json"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}_customers": """
        SELECT
            {jid}                                    AS id,
            {jname}                                  AS name,
            {jemail}                                 AS email,
            {jphone}                                 AS phone_number,
            CAST({jvisits}  AS UNSIGNED)              AS total_visits,
            CAST({jspent}   AS DECIMAL(14,4))         AS total_spent,
            CAST({jpoints}  AS DECIMAL(12,2))         AS points_balance,
            {jcreated}                               AS created_at,
            {jupdated}                               AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='customer'
    """.format(jid=_jstr("id"), jname=_jstr("name"), jemail=_jstr("email"),
               jphone=_jstr("phone_number"), jvisits=_jnum("total_visits"),
               jspent=_jnum("total_spent"), jpoints=_jnum("points_balance"),
               jcreated=_jstr("created_at"), jupdated=_jstr("updated_at")),
    "{prefix}_receipts": """
        SELECT
            {jrn}                                     AS receipt_number,
            external_created_at                       AS receipt_date,
            {jotype}                                  AS order_type,
            {jstore}                                  AS store_id,
            {jcashier}                                AS cashier_id,
            {jcust}                                   AS customer_id,
            {jcname}                                  AS customer_name,
            CAST({jtotal}  AS DECIMAL(14,4))          AS total_money,
            CAST({jdisc}   AS DECIMAL(14,4))          AS total_discounts,
            CAST({jtax}    AS DECIMAL(14,4))           AS total_tax,
            {jrtype}                                  AS receipt_type,
            {jcreated}                                AS created_at,
            {jupdated}                                AS updated_at,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='receipt'
    """.format(
        jrn=_jstr("receipt_number"), jotype=_jstr("order_type"),
        jstore=_jstr("store_id"), jcashier=_jstr("cashier_id"),
        jcust=_jstr("customer_id"), jcname=_jstr("customer_name"),
        jtotal=_jnum("total_money"), jdisc=_jnum("total_discounts"), jtax=_jnum("total_tax"),
        jrtype=_jstr("receipt_type"),
        jcreated=_jstr("created_at"), jupdated=_jstr("updated_at"),
    ),
    "{prefix}_receipt_line_items": """
        SELECT
            {jid}                                      AS id,
            {jrn}                                      AS receipt_number,
            {jitem}                                    AS item_id,
            {jvar}                                     AS variant_id,
            {jiname}                                   AS item_name,
            {jvname}                                   AS variant_name,
            {jsku}                                     AS sku,
            CAST({jqty}   AS DECIMAL(12,4))            AS quantity,
            CAST({jprice} AS DECIMAL(12,4))            AS price,
            CAST({jgross} AS DECIMAL(14,4))            AS gross_total_money,
            CAST({jdisc}  AS DECIMAL(12,4))            AS discount,
            CAST({jtotal} AS DECIMAL(14,4))            AS total_money,
            {jcat}                                     AS category_id,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='receipt_line_item'
    """.format(
        jid=_jstr("id"), jrn=_jstr("receipt_number"), jitem=_jstr("item_id"),
        jvar=_jstr("variant_id"), jiname=_jstr("item_name"), jvname=_jstr("variant_name"),
        jsku=_jstr("sku"), jqty=_jnum("quantity"), jprice=_jnum("price"),
        jgross=_jnum("gross_total_money"), jdisc=_jnum("discount"),
        jtotal=_jnum("total_money"), jcat=_jstr("category_id"),
    ),
    "{prefix}_payment_line_items": """
        SELECT
            {jid}      AS id,
            {jrn}      AS receipt_number,
            {jpid}     AS payment_type_id,
            {jpname}   AS payment_type_name,
            CAST({jamt} AS DECIMAL(14,4)) AS money_amount,
            synced_at
        FROM integration_records
        WHERE tenant_id='{{prefix}}' AND provider_id='loyverse' AND record_type='payment_line_item'
    """.format(jid=_jstr("id"), jrn=_jstr("receipt_number"),
               jpid=_jstr("payment_type_id"), jpname=_jstr("payment_type_name"),
               jamt=_jnum("money_amount")),
}

_PROVIDER_VIEWS = {
    "salesplay": _SALESPLAY_VIEWS,
    "loyverse":  _LOYVERSE_VIEWS,
}


def _create_views_for_integration(conn, table_prefix: str, provider_id: str):
    """
    Create (or replace) SQL views that expose integration_records data using
    the same names as the old per-user tables. Analytics SQL is unchanged.
    Called once per integration at connect time.
    """
    view_map = _PROVIDER_VIEWS.get(provider_id, {})
    cursor = conn.cursor()
    for name_template, select_sql in view_map.items():
        view_name = name_template.format(prefix=table_prefix)
        body      = select_sql.format(prefix=table_prefix)
        cursor.execute(f"CREATE OR REPLACE VIEW `{view_name}` AS {body}")
    cursor.close()
    log.info("Integration views created", prefix=table_prefix, provider=provider_id,
             count=len(view_map))


# ── Core integration operations ───────────────────────────────────────────────

def get_integration(user_email: str, provider_id: str) -> Optional[Dict]:
    """Fetch a user's integration record (without credentials).

    Result is cached for INTEGRATION_CACHE_TTL seconds — table_prefix and status
    don't change between syncs so repeated reads from analytics/forecast endpoints
    are served from memory without hitting the DB.
    """
    key = (user_email, provider_id)
    with _integration_cache_lock:
        entry = _integration_cache.get(key)
    if entry:
        row, exp = entry
        if time.monotonic() < exp:
            return row
        with _integration_cache_lock:
            _integration_cache.pop(key, None)

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

    if row is not None:
        with _integration_cache_lock:
            _integration_cache[key] = (row, time.monotonic() + _INTEGRATION_CACHE_TTL)
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
    skip_validation: bool = False,
) -> Dict:
    """
    Full connect flow:
    1. Get provider
    2. Validate credentials (skipped when skip_validation=True — e.g. embed onboard
       where the token was just created server-side and is guaranteed valid)
    3. Create user-namespaced tables in DataMind's DB
    4. Save integration record
    5. Trigger full sync in background
    """
    provider = get_provider(provider_id)
    table_prefix = provider.get_table_prefix(user_email)
    log.info("Connecting integration",
             user=user_email, provider=provider_id, prefix=table_prefix,
             skip_validation=skip_validation)

    result = None
    if not skip_validation:
        result = provider.validate_credentials(creds)
        if not result.ok:
            raise ValueError(f"Credential validation failed: {result.error}")

    # Shared sp_* tables are created at bootstrap — no per-connection DDL needed.
    # _create_views_for_integration() is kept defined for reference but no longer
    # called; existing views on live connections continue to work harmlessly.
    conn = _get_internal_conn()

    # Save integration record
    enc_creds = _encrypt(creds)
    label = display_label or provider.manifest.display_name
    cursor = conn.cursor()
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
    _invalidate_integration_cache(user_email, provider_id)  # status changed to active

    # Trigger full sync in background
    _start_sync_thread(integration_id, user_email, provider_id, sync_type="full",
                       progress_callback=progress_callback)

    return {
        "ok": True,
        "provider_id": provider_id,
        "table_prefix": table_prefix,
        "validation_details": result.details if result else {},
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
        prefix = row["table_prefix"]
        cursor.execute(
            "DELETE FROM integration_records WHERE tenant_id=%s AND provider_id=%s",
            (prefix, provider_id)
        )
        cursor.execute(
            "DELETE FROM integration_sync_state WHERE tenant_id=%s AND provider_id=%s",
            (prefix, provider_id)
        )
        log.info("Deleted integration data", prefix=prefix, provider=provider_id)

    cursor.execute(
        "DELETE FROM user_integrations WHERE user_email=%s AND provider_id=%s",
        (user_email, provider_id)
    )
    conn.commit()
    conn.close()
    log.info("Integration disconnected", user=user_email, provider=provider_id)


def delete_user_data(user_email: str):
    """Delete all synced records and every DB row belonging to this user."""
    conn = _get_internal_conn()
    cursor = conn.cursor()
    # Delete unified integration data
    cursor.execute(
        "DELETE FROM integration_records WHERE user_email = %s", (user_email,)
    )
    cursor.execute(
        "DELETE FROM integration_sync_state WHERE user_email = %s", (user_email,)
    )
    # Delete metadata rows
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
    try:
        _run_sync_inner(integration_id, user_email, provider_id, sync_type, progress_callback)
    finally:
        # Always release the guard, even if the thread crashes unexpectedly
        _sync_active.discard(integration_id)
        _sync_progress.pop(integration_id, None)


def _run_sync_inner(integration_id: int, user_email: str, provider_id: str,
                    sync_type: str, progress_callback=None):
    """Inner sync logic — separated so _run_sync can guarantee cleanup."""
    conn = _get_internal_conn()
    try:
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
        if sync_type == "delta" and row["last_sync_at"]:
            since = row["last_sync_at"]
        else:
            try:
                from billing import get_plan_history_limit
                history = get_plan_history_limit(user_email)
                since = datetime.combine(history["cutoff_date"], datetime.min.time())
                log.info("Full sync history cutoff applied",
                         user=user_email, months=history["months"], since=str(since))
            except Exception as _he:
                log.warning("Could not get plan history limit — syncing all data", error=str(_he))
                since = None
    finally:
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

    # Calculate how many rows this user can still receive before hitting their plan limit
    row_budget = None
    try:
        from billing import get_user_subscription
        sub = get_user_subscription(user_email)
        db_total = sub.get("db_total_available")
        db_used  = sub.get("db_base_used", 0)
        if db_total is not None:
            row_budget = max(0, int(db_total) - int(db_used))
    except Exception as _be:
        log.warning("Could not calculate row budget — syncing without limit", error=str(_be))

    sync_conn = _get_internal_conn()
    try:
        result: SyncResult = provider.sync(
            creds=creds,
            conn=sync_conn,
            table_prefix=table_prefix,
            since=since,
            progress_callback=_progress,
            row_budget=row_budget,
            user_email=user_email,
        )
    finally:
        sync_conn.close()

    # Update status
    status = "success" if result.ok else "error"
    limit_msg = (
        f"Row limit reached — {result.rows_skipped:,} rows skipped. "
        "Upgrade your plan or purchase add-on rows to sync the rest."
        if result.limit_hit else None
    )
    error_msg = limit_msg or result.error or None

    conn2 = _get_internal_conn()
    try:
        c2 = conn2.cursor()
        c2.execute("""
            UPDATE sync_logs SET
              finished_at=NOW(), status=%s,
              rows_fetched=%s, rows_inserted=%s, rows_updated=%s,
              error_message=%s
            WHERE id=%s
        """, (status, result.rows_fetched, result.rows_inserted,
              result.rows_updated, error_msg, log_id))
        # Always update last_sync_at even on failure so the scheduler
        # applies backoff instead of retrying on every tick.
        c2.execute("""
            UPDATE user_integrations SET
              status=%s, last_sync_at=NOW(),
              last_sync_rows=%s, last_error=%s
            WHERE id=%s
        """, (
            "active" if result.ok else "error",
            result.rows_inserted,
            error_msg,
            integration_id,
        ))
        conn2.commit()
    finally:
        conn2.close()

    log.info("Sync worker finished", user=user_email, provider=provider_id,
             status=status, rows=result.rows_fetched)

    # Bust analytics result cache so next query gets fresh post-sync data.
    # Bust even when ok=False — individual entity steps commit their own data,
    # so rows may be present even if the sync was later marked as error.
    if provider_id == "salesplay" and (result.ok or result.rows_inserted > 0):
        try:
            from providers.salesplay.analytics import cache_bust
            cache_bust(table_prefix)
            log.debug("Analytics cache busted after sync", prefix=table_prefix)
        except Exception:
            pass


def _start_sync_thread(integration_id: int, user_email: str, provider_id: str,
                       sync_type: str = "delta", progress_callback=None) -> bool:
    """Start a sync thread. Returns False (and skips) if pool is full or already running."""
    if len(_sync_active) >= _MAX_CONCURRENT_SYNCS:
        log.warning("Sync skipped — concurrent sync limit reached",
                    active=len(_sync_active), limit=_MAX_CONCURRENT_SYNCS,
                    user=user_email, provider=provider_id)
        return False
    if integration_id in _sync_active:
        log.info("Sync skipped — already in progress",
                 user=user_email, provider=provider_id, integration_id=integration_id)
        return False
    _sync_active.add(integration_id)
    t = threading.Thread(
        target=_run_sync,
        args=(integration_id, user_email, provider_id, sync_type, progress_callback),
        daemon=True,
    )
    t.start()
    log.info("Sync thread started", user=user_email, provider=provider_id,
             sync_type=sync_type)
    return True


# ── Background scheduler ──────────────────────────────────────────────────────

_scheduler_thread: Optional[threading.Thread] = None


def _try_acquire_scheduler_lock() -> bool:
    """
    Attempt to acquire a DB-level advisory lock for the scheduler.
    Returns True only for the one worker that wins the lock.
    Uses GET_LOCK() with a 0-second timeout so non-winners return immediately.
    The lock is held for the lifetime of the connection — released on conn.close().
    """
    try:
        conn = _get_internal_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT GET_LOCK('datamind_scheduler', 0)")
        result = cursor.fetchone()
        acquired = result and result[0] == 1
        if acquired:
            # Intentionally do NOT close this connection — releasing it drops the lock.
            # The connection is kept open for the scheduler's lifetime.
            log.info("Scheduler: acquired DB advisory lock — this worker owns the scheduler")
        else:
            conn.close()
            log.debug("Scheduler: another worker already holds the lock — skipping")
        return acquired
    except Exception as e:
        log.warning("Scheduler: could not acquire DB lock, starting anyway", error=str(e))
        return True  # fail open — better to have duplicate ticks than no ticks


def start_scheduler():
    """
    Start the background scheduler. Only one worker per server will actually run
    ticks — the others are blocked by a DB advisory lock. The scheduler thread is
    monitored and restarted if it dies (watchdog pattern).
    """
    _launch_scheduler_thread()


def _launch_scheduler_thread():
    global _scheduler_thread

    def _loop():
        if not _try_acquire_scheduler_lock():
            return  # another worker won — this thread exits cleanly
        log.info("Integration scheduler started")
        while True:
            try:
                _scheduler_tick()
            except Exception as e:
                log.error("Scheduler tick failed", error=str(e))
            time.sleep(60)

    _scheduler_thread = threading.Thread(target=_loop, daemon=True, name="datamind-scheduler")
    _scheduler_thread.start()

    # Watchdog: a second daemon thread that checks if the scheduler is alive
    # and relaunches it if it dies.
    def _watchdog():
        time.sleep(90)  # give the scheduler time to start
        while True:
            time.sleep(60)
            if _scheduler_thread and not _scheduler_thread.is_alive():
                log.warning("Scheduler thread died — restarting")
                _launch_scheduler_thread()
                break  # the new launch spawns its own watchdog

    threading.Thread(target=_watchdog, daemon=True, name="datamind-scheduler-watchdog").start()


_SYNC_ERROR_BACKOFF_MULTIPLIER = 5  # failed integrations wait 5× the normal interval
_SYNC_STUCK_TIMEOUT_MINUTES = 30    # 'syncing' records older than this are considered crashed


def _scheduler_tick():
    """Check all active and errored integrations and sync if due."""
    conn = _get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)

        # Reset any integrations that have been stuck in 'syncing' for too long.
        # This handles cases where a sync thread died without updating the status
        # (e.g. OOM kill between our bootstrap cleanup and the next restart).
        cursor.execute("""
            UPDATE user_integrations
            SET status = 'error',
                last_error = 'Sync timed out — no completion after 30 minutes.'
            WHERE status = 'syncing'
              AND last_sync_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
        """, (_SYNC_STUCK_TIMEOUT_MINUTES,))
        if cursor.rowcount:
            log.warning("Scheduler reset stuck syncing integrations", count=cursor.rowcount)
            conn.commit()

        cursor.execute("""
            SELECT ui.id, ui.user_email, ui.provider_id, ui.last_sync_at, ui.status
            FROM user_integrations ui
            WHERE ui.status IN ('active', 'error')
        """)
        rows = cursor.fetchall()
    finally:
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
        # Apply backoff multiplier for integrations in error state
        effective_interval = (
            interval * _SYNC_ERROR_BACKOFF_MULTIPLIER
            if row["status"] == "error" else interval
        )
        if elapsed >= effective_interval:
            log.info("Scheduler triggering delta sync",
                     user=row["user_email"], provider=row["provider_id"],
                     elapsed_minutes=round(elapsed, 1), status=row["status"])
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

        # Count total rows from unified table — fast, indexed
        total_rows = 0
        table_prefix = row.get("table_prefix", "")
        if table_prefix:
            try:
                cursor.execute("""
                    SELECT COUNT(*) AS cnt
                    FROM integration_records
                    WHERE tenant_id = %s AND provider_id = %s
                """, (table_prefix, connection_id))
                r = cursor.fetchone()
                total_rows = int((r or {}).get("cnt") or 0)
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


def get_user_total_rows(user_email: str) -> Optional[int]:
    """
    Sum all synced records for this user across all providers.
    Returns None on DB error so callers can distinguish 'zero rows' from
    'count unavailable' — important for billing row-limit enforcement.
    """
    conn = _get_internal_conn()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT COUNT(*) AS cnt
            FROM integration_records
            WHERE user_email = %s
        """, (user_email,))
        row = cursor.fetchone()
        return int((row or {}).get("cnt") or 0)
    except Exception as e:
        log.warning("get_user_total_rows failed", user=user_email, error=str(e))
        return None  # None signals 'count unavailable', not 'zero rows'
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