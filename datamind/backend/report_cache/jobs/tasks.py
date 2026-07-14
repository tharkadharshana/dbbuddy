"""
report_cache/jobs/tasks.py
============================
The PLAN 04 task functions: onboarding, ingest, rollover, re-finalization,
retention. Run by the worker (report_cache/jobs/worker.py) — either drained
from the report_cache_job queue (job_sync_profile / job_onboard_tenant /
job_ingest_period) or fired directly by APScheduler for the tenant-sweeping
lifecycle jobs (job_rollover / job_refinalize / job_retention_purge /
job_sync_profile_all).

Sync, not async: PLAN 04's arq task signatures assumed Redis; the documented
fallback uses APScheduler + a sync worker, and report_cache.ingest is already
sync, so these are plain functions (no `ctx`). Each is wrapped by the worker
in try/except; the breaker + rate limiter (guards.py) cap POS load.

── The token wrinkle (read this) ───────────────────────────────────────────
The report API (v2.0 /app/*) needs the embed session's short-lived `aat`.
report_cache.auth.get_report_token() returns the STORED api_token, which is
the v1.0 data-sync token and does NOT authenticate against v2.0 (confirmed in
PLAN 02, see docs/plan/PROGRESS.md's open background-auth item). So:
  - Jobs triggered from a live embed request (onboarding, lazy backfill) carry
    the fresh `aat` in their payload — those work end-to-end today.
  - Unattended API-touching jobs (refinalize/rollover/profile-sync) fall back
    to get_report_token(); until the background-auth item is resolved that
    call may 401 — handled gracefully (error sync-state + breaker), never a
    crash. Retention purge is pure DB and works fully unattended regardless.
This module does NOT try to fix the auth gap (out of PLAN 04 scope) — it just
threads a token through and fails safe when one isn't valid.
"""

import os
from datetime import date, datetime, timedelta
from typing import List, Optional

import pool
from logger import get_logger
from report_cache import tiers
from report_cache.auth import get_report_token
from report_cache.ingest import ingest_period
from report_cache.jobs.guards import BreakerOpen, breaker, rate_limiter
from report_cache.periods import daterange_to_months, month_bounds
from report_cache.profile import sync_tenant_profile
from report_cache.registry import REPORTS
from report_cache.store import set_sync_state

log = get_logger(__name__)

_ONBOARD_MONTHS = int(os.getenv("REPORT_CACHE_ONBOARD_MONTHS", "3"))
_FINALIZE_LAG_DAYS = int(os.getenv("REPORT_CACHE_FINALIZE_LAG_DAYS", "2"))
_REFINALIZE_DAILY_DAYS = int(os.getenv("REPORT_CACHE_REFINALIZE_DAILY_DAYS", "45"))
_REFINALIZE_DIM_MONTHS = int(os.getenv("REPORT_CACHE_REFINALIZE_DIM_MONTHS", "2"))
_UNLIMITED_TIER = "unlimited"


# ── token + tenant helpers ──────────────────────────────────────────────────

def _resolve_token(tenant_id: str, token: Optional[str]) -> Optional[str]:
    return token or get_report_token(tenant_id)


def _recent_months(count: int, today: Optional[date] = None) -> List[date]:
    """The `count` most recent calendar months as month-first dates, oldest first
    (e.g. count=3 in July → [May 1, Jun 1, Jul 1])."""
    today = today or date.today()
    month = today.replace(day=1)
    months = [month]
    for _ in range(count - 1):
        month = (month - timedelta(days=1)).replace(day=1)
        months.append(month)
    return list(reversed(months))


def active_tenants() -> List[str]:
    """Every tenant with a live SalesPlay integration (report_cache is
    SalesPlay-only). A row in user_integrations means the integration is live —
    disconnect deletes the row (see report_cache/auth.py)."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DISTINCT table_prefix FROM user_integrations WHERE provider_id='salesplay'"
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


def onboarded_tenants() -> List[str]:
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT tenant_id FROM report_cache_state WHERE onboarded_at IS NOT NULL")
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return [r[0] for r in rows if r[0]]


def is_onboarded(tenant_id: str) -> bool:
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT onboarded_at FROM report_cache_state WHERE tenant_id=%s AND onboarded_at IS NOT NULL",
            (tenant_id,),
        )
        found = cursor.fetchone() is not None
        cursor.close()
    finally:
        conn.close()
    return found


def _mark_onboarded(tenant_id: str) -> None:
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO report_cache_state (tenant_id, onboarded_at, updated_at)
            VALUES (%s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE onboarded_at=NOW(), updated_at=NOW()
            """,
            (tenant_id,),
        )
        cursor.close()
        conn.commit()
    finally:
        conn.close()


# ── queued tasks ────────────────────────────────────────────────────────────

def job_sync_profile(tenant_id: str, token: Optional[str] = None) -> dict:
    """Refresh tenant_profile/shops/cashiers/tier (PLAN 02)."""
    sync_tenant_profile(tenant_id, access_token=token)
    return {"tenant_id": tenant_id, "synced": True}


def job_ingest_period(tenant_id: str, report_id: str, period_iso: str,
                      shop_id: str = "all", token: Optional[str] = None) -> dict:
    """Ingest one period (scalar month-of-days, or one dim month) — the unit of
    work for backfill and ad-hoc warming. Guarded by breaker + rate limiter."""
    breaker.check(tenant_id)
    tok = _resolve_token(tenant_id, token)
    if not tok:
        raise ValueError(f"No usable report API token for tenant {tenant_id!r}")
    period = date.fromisoformat(period_iso)

    rate_limiter.acquire(tenant_id)
    conn = pool.get_internal_conn()
    try:
        result = ingest_period(conn, tenant_id, report_id, tok, period, shop_id=shop_id)
        conn.commit()
    except Exception:
        conn.rollback()
        breaker.record_failure(tenant_id)
        raise
    finally:
        conn.close()
    breaker.record_success(tenant_id)
    return {"tenant_id": tenant_id, "report_id": report_id, "period": period_iso, **result}


def job_onboard_tenant(tenant_id: str, token: Optional[str] = None) -> dict:
    """Onboarding (doc 09 C5): profile-sync, then EAGER-recent ingest of the
    last N months (default 3) for all 8 registry reports at their native grain,
    shop_id='all'. Deep history is left to lazy backfill (request_backfill).
    Idempotent — re-running just re-ingests (upserts) and re-stamps the marker.

    Ingests inline (not via the queue) so the single fresh `token` is used
    within one job execution rather than fanned out across many queued rows
    that could outlive a short-lived aat. One report/month failing is logged
    and skipped; it does not abort the rest."""
    if is_onboarded(tenant_id):
        log.info("job_onboard_tenant: already onboarded — refreshing recent window", tenant=tenant_id)

    job_sync_profile(tenant_id, token=token)  # needs shops/currency + tier first
    tok = _resolve_token(tenant_id, token)
    if not tok:
        raise ValueError(f"No usable report API token for tenant {tenant_id!r}")

    months = _recent_months(_ONBOARD_MONTHS)
    ingested = 0
    failed = 0
    for report_id in REPORTS:
        for month in months:
            try:
                breaker.check(tenant_id)
                rate_limiter.acquire(tenant_id)
                conn = pool.get_internal_conn()
                try:
                    ingest_period(conn, tenant_id, report_id, tok, month, shop_id="all")
                    conn.commit()
                finally:
                    conn.close()
                breaker.record_success(tenant_id)
                ingested += 1
            except BreakerOpen:
                log.warning("job_onboard_tenant: breaker open, aborting remaining onboarding",
                            tenant=tenant_id, report=report_id, month=month.isoformat())
                failed += 1
                return {"tenant_id": tenant_id, "ingested": ingested, "failed": failed, "onboarded": False}
            except Exception as exc:
                breaker.record_failure(tenant_id)
                failed += 1
                log.warning("job_onboard_tenant: report/month failed (skipped)", tenant=tenant_id,
                            report=report_id, month=month.isoformat(), error=str(exc))

    _mark_onboarded(tenant_id)
    log.info("Tenant onboarded", tenant=tenant_id, months=len(months), reports=len(REPORTS),
             ingested=ingested, failed=failed)
    return {"tenant_id": tenant_id, "ingested": ingested, "failed": failed, "onboarded": True}


# ── scheduled lifecycle jobs (sweep all tenants) ────────────────────────────

def job_sync_profile_all() -> dict:
    """Daily: enqueue a profile refresh for each active tenant (PLAN 04 Step 8).
    Enqueues rather than syncing inline so the sweep returns immediately and the
    worker paces the actual POS calls."""
    from report_cache.jobs.enqueue import enqueue

    tenants = active_tenants()
    for tenant_id in tenants:
        enqueue("job_sync_profile", tenant_id=tenant_id)
    log.info("Profile-sync sweep enqueued", tenants=len(tenants))
    return {"tenants": len(tenants)}


def job_refinalize() -> dict:
    """Re-finalization (doc 09 C4): re-fetch a trailing window per onboarded
    tenant to catch late refunds/voids/edits, then mark now-safely-past days/
    months 'finalized'. Bounded to onboarded tenants; each report/tenant guarded
    by the breaker + rate limiter."""
    today = date.today()
    daily_start = today - timedelta(days=_REFINALIZE_DAILY_DAYS)
    dim_months = _recent_months(_REFINALIZE_DIM_MONTHS, today)

    scalar_reports = [r for r in REPORTS.values() if r.kind == "scalar"]
    dim_reports = [r for r in REPORTS.values() if r.kind == "dimensional"]

    tenants = onboarded_tenants()
    processed = 0
    for tenant_id in tenants:
        tok = _resolve_token(tenant_id, None)
        if not tok:
            log.info("job_refinalize: no usable token, skipping tenant", tenant=tenant_id)
            continue
        try:
            breaker.check(tenant_id)
        except BreakerOpen:
            log.warning("job_refinalize: breaker open, skipping tenant", tenant=tenant_id)
            continue

        _refetch_reports(tenant_id, tok, scalar_reports, daterange_to_months(daily_start, today))
        _refetch_dim_reports(tenant_id, tok, dim_reports, dim_months)
        _finalize_past(tenant_id, today)
        processed += 1

    log.info("Re-finalization sweep complete", tenants=processed,
             daily_since=daily_start.isoformat())
    return {"tenants": processed}


def job_rollover() -> dict:
    """Rollover (doc 09 Part 5): at month change, finalize the just-closed month
    per active tenant (re-fetch → finalized) then purge anything now outside the
    tier window. Re-finalization already covers a 45-day trailing window, so
    rollover leans on the same finalize step scoped to last month, then delegates
    purging to retention."""
    today = date.today()
    last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    scalar_reports = [r for r in REPORTS.values() if r.kind == "scalar"]
    dim_reports = [r for r in REPORTS.values() if r.kind == "dimensional"]

    tenants = active_tenants()
    processed = 0
    for tenant_id in tenants:
        tok = _resolve_token(tenant_id, None)
        if not tok:
            continue
        try:
            breaker.check(tenant_id)
        except BreakerOpen:
            continue
        _refetch_reports(tenant_id, tok, scalar_reports, [last_month])
        _refetch_dim_reports(tenant_id, tok, dim_reports, [last_month])
        _finalize_past(tenant_id, today)
        processed += 1

    purged = job_retention_purge()
    log.info("Rollover complete", tenants=processed, finalized_month=last_month.isoformat())
    return {"tenants": processed, "finalized_month": last_month.isoformat(), **purged}


def job_retention_purge() -> dict:
    """Retention (doc 09 Part 5): delete facts + sync-state older than the
    tenant's tier window. NEVER purges 'unlimited' tenants. Pure DB — no POS
    calls, so it works fully unattended regardless of the token wrinkle."""
    tenants = active_tenants()
    total_deleted = 0
    for tenant_id in tenants:
        if tiers.get_ai_tier(tenant_id) == _UNLIMITED_TIER:
            log.info("job_retention_purge: unlimited tenant — skipped", tenant=tenant_id)
            continue
        cutoff = tiers.window_start(tenant_id)
        deleted = _purge_tenant(tenant_id, cutoff)
        total_deleted += deleted
        counts = storage_metrics(tenant_id)
        log.info("Retention purge", tenant=tenant_id, cutoff=cutoff.isoformat(),
                 deleted=deleted, remaining=counts)
    return {"deleted": total_deleted}


# ── internal: refetch / finalize / purge SQL ────────────────────────────────

def _refetch_reports(tenant_id: str, token: str, reports, months) -> None:
    """Re-ingest scalar reports over the given months (upsert corrects late edits)."""
    for report in reports:
        for month in months:
            try:
                rate_limiter.acquire(tenant_id)
                conn = pool.get_internal_conn()
                try:
                    ingest_period(conn, tenant_id, report.id, token, month, shop_id="all")
                    conn.commit()
                finally:
                    conn.close()
                breaker.record_success(tenant_id)
            except Exception as exc:
                breaker.record_failure(tenant_id)
                log.warning("_refetch_reports failed (skipped)", tenant=tenant_id,
                            report=report.id, month=month.isoformat(), error=str(exc))


def _refetch_dim_reports(tenant_id: str, token: str, reports, months) -> None:
    _refetch_reports(tenant_id, token, reports, months)  # ingest_period dispatches on grain


def _finalize_past(tenant_id: str, today: date) -> None:
    """Mark facts safely in the past 'finalized' (doc 09 C4). A day is safe once
    it's older than the finalize lag (late refunds/voids have settled). A dim
    month is safe once it's a fully past calendar month."""
    daily_cutoff = today - timedelta(days=_FINALIZE_LAG_DAYS)
    dim_cutoff = today.replace(day=1)  # any month strictly before the current one
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE report_daily_fact SET status='finalized' "
            "WHERE tenant_id=%s AND business_date < %s AND status <> 'finalized'",
            (tenant_id, daily_cutoff),
        )
        cursor.execute(
            "UPDATE report_dim_fact SET status='finalized' "
            "WHERE tenant_id=%s AND period_month < %s AND status <> 'finalized'",
            (tenant_id, dim_cutoff),
        )
        cursor.close()
        conn.commit()
    finally:
        conn.close()


def _purge_tenant(tenant_id: str, cutoff: date) -> int:
    """DELETE facts + sync-state strictly before `cutoff`. Returns rows deleted.
    Dim facts/sync-state are keyed by month — compare against the cutoff's month
    so a partially-in-window boundary month is kept."""
    cutoff_month = cutoff.replace(day=1)
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM report_daily_fact WHERE tenant_id=%s AND business_date < %s",
                       (tenant_id, cutoff))
        deleted = cursor.rowcount
        cursor.execute("DELETE FROM report_dim_fact WHERE tenant_id=%s AND period_month < %s",
                       (tenant_id, cutoff_month))
        deleted += cursor.rowcount
        cursor.execute("DELETE FROM report_sync_state WHERE tenant_id=%s AND period < %s",
                       (tenant_id, cutoff))
        deleted += cursor.rowcount
        cursor.close()
        conn.commit()
    finally:
        conn.close()
    return deleted


def storage_metrics(tenant_id: str) -> dict:
    """Per-tenant row counts across the three fact/state tables — cheap cost
    signal for monitoring (PLAN 04 Step 7). PLAN 08 owns real dashboards."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor()
        counts = {}
        for table in ("report_daily_fact", "report_dim_fact", "report_sync_state"):
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id=%s", (tenant_id,))
            (counts[table],) = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    return counts
