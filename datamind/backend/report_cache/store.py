"""
report_cache/store.py
======================
Write side of the cache (PLAN_03 Step 1). Idempotent upserts — doc 09 C4
requires in-place correction on re-fetch (a re-finalization re-fetch of a
closed period must overwrite the old row, not duplicate it).

All three functions take an already-open `conn` and do NOT commit/close it —
the caller (an ingestion function, a job, a test, or the manual-verification
script) owns the transaction boundary. This matches report_cache/ingest.py's
functions, which also take `conn` and let the caller commit.
"""

import json
from datetime import date
from typing import Optional

from logger import get_logger

log = get_logger(__name__)


def upsert_daily_fact(conn, tenant_id: str, report_id: str, shop_id: str,
                       business_date: date, metrics: dict, status: str) -> None:
    """One row per (tenant, report, shop, day) — scalar report facts."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO report_daily_fact
            (tenant_id, report_id, shop_id, business_date, metrics, status, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            metrics=VALUES(metrics), status=VALUES(status), fetched_at=NOW()
        """,
        (tenant_id, report_id, shop_id, business_date, json.dumps(metrics), status),
    )
    cursor.close()


def upsert_dim_fact(conn, tenant_id: str, report_id: str, shop_id: str,
                     period_month: date, dim_type: str, dim_key: str, dim_name: Optional[str],
                     metrics: dict, status: str) -> None:
    """One row per (tenant, report, shop, month, dim_type, dim_key) — dimensional
    report facts (e.g. one product or category per month)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO report_dim_fact
            (tenant_id, report_id, shop_id, period_month, dim_type, dim_key, dim_name,
             metrics, status, fetched_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            dim_name=VALUES(dim_name), metrics=VALUES(metrics), status=VALUES(status), fetched_at=NOW()
        """,
        (tenant_id, report_id, shop_id, period_month.replace(day=1), dim_type, dim_key, dim_name,
         json.dumps(metrics), status),
    )
    cursor.close()


def set_sync_state(conn, tenant_id: str, report_id: str, shop_id: str, period: date,
                    grain: str, status: str, error: Optional[str] = None) -> None:
    """Records the outcome of one ingestion attempt for (tenant, report, shop, period, grain).

    `fetched_at` here means "last attempt time", not strictly "last successful
    fetch" — an error-status call still stamps it, since report_daily_fact/
    report_dim_fact have no error-tracking columns at all (a fact row only
    exists once data actually landed); this table is where a failed attempt
    gets recorded so PLAN 04's job/retry logic has somewhere to look.
    `attempts` starts at 1 on first insert and increments on every subsequent
    call for the same key (retries, re-finalization re-fetches, etc.)."""
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO report_sync_state
            (tenant_id, report_id, shop_id, period, grain, status, fetched_at, attempts, last_error)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), 1, %s)
        ON DUPLICATE KEY UPDATE
            status=VALUES(status), fetched_at=NOW(), attempts=attempts + 1, last_error=VALUES(last_error)
        """,
        (tenant_id, report_id, shop_id, period, grain, status, error),
    )
    cursor.close()
