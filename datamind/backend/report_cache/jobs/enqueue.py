"""
report_cache/jobs/enqueue.py
==============================
Durable, DB-backed job queue (PLAN 04 Step 1/5). This is the fallback queue
PLAN 04 documents for when Redis/arq aren't available (they aren't here) —
the report_cache_job table is the queue, report_cache/jobs/worker.py is the
drain loop.

Public API (safe to call from a web-request thread — these are fast INSERTs,
never ingestion):
  - enqueue(task, tenant_id, run_after, **payload)   -> job id
  - request_backfill(tenant_id, report_id, start, end, shop_id, token)
        -> month-by-month job ids (PLAN 04 Step 5, doc 09 C5). Called by the
           answer layer (PLAN 05) on a cache miss for an in-window historical
           range; warms the cache for next time WITHOUT blocking the request.

Worker-side helpers (claim_next / complete / fail) are here too so the whole
queue lives in one module.

`payload` may carry a short-lived POS `token` (the embed request's v2.0 aat).
It is stored as plain JSON in the internal DB — acceptable because the token
is short-lived and the DB is the same trust boundary that already holds the
encrypted credentials. See tasks.py for why the stored api_token can't be
used instead.
"""

import json
import os
from datetime import date, datetime
from typing import List, Optional

import pool
from logger import get_logger
from report_cache import tiers
from report_cache.periods import daterange_to_months
from report_cache.registry import REPORTS

log = get_logger(__name__)

_MAX_ATTEMPTS = int(os.getenv("REPORT_CACHE_JOB_MAX_ATTEMPTS", "3"))


def enqueue(task: str, tenant_id: Optional[str] = None,
            run_after: Optional[datetime] = None, **payload) -> int:
    """Insert a job. Returns its id. Never runs the task — that's the worker."""
    run_after = run_after or datetime.utcnow()
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO report_cache_job
                (task, tenant_id, payload, status, attempts, run_after, created_at, updated_at)
            VALUES (%s, %s, %s, 'queued', 0, %s, NOW(), NOW())
            """,
            (task, tenant_id, json.dumps(payload), run_after),
        )
        job_id = cursor.lastrowid
        cursor.close()
        conn.commit()
    finally:
        conn.close()
    log.info("Job enqueued", task=task, tenant=tenant_id, job_id=job_id)
    return job_id


def request_backfill(tenant_id: str, report_id: str, start: date, end: date,
                     shop_id: str = "all", token: Optional[str] = None) -> List[int]:
    """Lazy backfill (doc 09 C5): enqueue one job_ingest_period per calendar
    month in [start, end], clipped to the tenant's tier window. Month-by-month
    so each month's fetch is independently reusable/idempotent (doc 09) and the
    rate limiter can space them out. Returns the enqueued job ids (empty if the
    whole range is outside the window or the report is unknown)."""
    if report_id not in REPORTS:
        log.warning("request_backfill: unknown report_id", tenant=tenant_id, report=report_id)
        return []
    if start > end:
        start, end = end, start

    win_start = tiers.window_start(tenant_id)
    effective_start = max(start, win_start)
    if effective_start > end:
        log.info("request_backfill: range entirely before tenant window — nothing enqueued",
                 tenant=tenant_id, report=report_id, start=start, end=end, window_start=win_start)
        return []

    job_ids = []
    for month in daterange_to_months(effective_start, end):
        job_ids.append(enqueue(
            "job_ingest_period", tenant_id=tenant_id,
            report_id=report_id, period_iso=month.isoformat(), shop_id=shop_id, token=token,
        ))
    log.info("Backfill requested", tenant=tenant_id, report=report_id,
             months=len(job_ids), start=effective_start, end=end)
    return job_ids


def claim_next(now: Optional[datetime] = None) -> Optional[dict]:
    """Atomically claim one due 'queued' job (oldest first): flip it to
    'running' with an optimistic `WHERE status='queued'` guard so two workers
    can't grab the same row. Returns the job dict (payload parsed) or None."""
    now = now or datetime.utcnow()
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, task, tenant_id, payload, attempts
            FROM report_cache_job
            WHERE status='queued' AND run_after <= %s
            ORDER BY id
            LIMIT 1
            """,
            (now,),
        )
        row = cursor.fetchone()
        if row is None:
            cursor.close()
            return None

        cursor.execute(
            "UPDATE report_cache_job SET status='running', attempts=attempts+1, updated_at=NOW() "
            "WHERE id=%s AND status='queued'",
            (row["id"],),
        )
        claimed = cursor.rowcount == 1
        cursor.close()
        conn.commit()
    finally:
        conn.close()

    if not claimed:
        return None  # another worker won the race — caller loops and tries the next
    row["payload"] = _parse_payload(row.get("payload"))
    row["attempts"] += 1
    return row


def complete(job_id: int) -> None:
    _set_status(job_id, "done")


def fail(job_id: int, error: str, attempts: int, retry_delay_seconds: int = 60) -> None:
    """Requeue with backoff if attempts remain, else mark 'error'. Backoff is
    linear (retry_delay * attempts) — enough for transient POS blips without a
    thundering retry."""
    from datetime import timedelta

    if attempts < _MAX_ATTEMPTS:
        run_after = datetime.utcnow() + timedelta(seconds=retry_delay_seconds * attempts)
        conn = pool.get_internal_conn()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE report_cache_job SET status='queued', run_after=%s, last_error=%s, updated_at=NOW() "
                "WHERE id=%s",
                (run_after, error[:512], job_id),
            )
            cursor.close()
            conn.commit()
        finally:
            conn.close()
        log.warning("Job requeued after failure", job_id=job_id, attempts=attempts, retry_at=run_after)
    else:
        _set_status(job_id, "error", error=error)
        log.error("Job failed permanently", job_id=job_id, attempts=attempts, error=error[:200])


def pending_count() -> int:
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM report_cache_job WHERE status='queued'")
        (n,) = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    return int(n)


def _set_status(job_id: int, status: str, error: Optional[str] = None) -> None:
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE report_cache_job SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s",
            (status, (error[:512] if error else None), job_id),
        )
        cursor.close()
        conn.commit()
    finally:
        conn.close()


def _parse_payload(value) -> dict:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}
