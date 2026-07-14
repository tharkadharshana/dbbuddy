"""
report_cache/jobs/worker.py
=============================
The background worker (PLAN 04 Step 1/8). A single long-running process that:
  1. Drains the report_cache_job queue (onboarding / ingest / profile-sync
     jobs enqueued from embed onboarding + lazy backfill).
  2. Runs the cron-style lifecycle jobs on a schedule (refinalize daily,
     retention daily, rollover at month start + a daily month-change guard,
     profile-sync sweep daily).

Queue tech = APScheduler + a DB job table (PLAN 04's documented fallback; no
Redis/arq in this env). Ingestion runs ONLY here, never on a web-request
thread — the web app only ever enqueues (fast INSERT) or reads the cache.

Run it as its own process:
    cd datamind/backend && python -m report_cache.jobs.worker

Concurrency is deliberately small (one drain loop, sequential jobs) so the
per-tenant rate limiter + circuit breaker (guards.py) genuinely cap POS load —
these reports are 90s-class calls. If throughput ever needs to grow, raise the
executor pool AND move guards.py's state out of process (see its ponytail note).
"""

import os
import signal

from apscheduler.schedulers.blocking import BlockingScheduler

from logger import get_logger
import report_cache.jobs.enqueue as queue  # submodule (the package re-exports the enqueue() fn under the same name)
from report_cache.jobs import tasks

log = get_logger(__name__)

_POLL_SECONDS = int(os.getenv("REPORT_CACHE_WORKER_POLL_SECONDS", "5"))
_DRAIN_BATCH = int(os.getenv("REPORT_CACHE_WORKER_DRAIN_BATCH", "20"))

# Tasks that can be dispatched from the durable queue. Lifecycle sweeps
# (rollover/refinalize/retention/profile-sync-all) are fired by the scheduler
# directly, not enqueued, so they're intentionally NOT here.
TASKS = {
    "job_sync_profile": tasks.job_sync_profile,
    "job_onboard_tenant": tasks.job_onboard_tenant,
    "job_ingest_period": tasks.job_ingest_period,
}


def run_job(job: dict) -> None:
    """Dispatch one claimed job; mark done, or fail (requeue-with-backoff /
    error) on exception. Never propagates — the drain loop must keep going."""
    func = TASKS.get(job["task"])
    if func is None:
        queue.fail(job["id"], f"unknown task {job['task']!r}", job["attempts"])
        return
    try:
        func(**job["payload"])
        queue.complete(job["id"])
    except Exception as exc:
        log.error("Job execution failed", job_id=job["id"], task=job["task"], error=str(exc))
        queue.fail(job["id"], str(exc), job["attempts"])


def drain_once() -> int:
    """Claim and run due jobs, up to a batch cap per tick (so a flood can't
    starve the scheduler). Returns how many ran."""
    ran = 0
    for _ in range(_DRAIN_BATCH):
        job = queue.claim_next()
        if job is None:
            break
        run_job(job)
        ran += 1
    return ran


def _register_schedules(scheduler: BlockingScheduler) -> None:
    scheduler.add_job(drain_once, "interval", seconds=_POLL_SECONDS, id="drain",
                      max_instances=1, coalesce=True)
    scheduler.add_job(tasks.job_refinalize, "cron", hour=3, minute=0, id="refinalize")
    scheduler.add_job(tasks.job_retention_purge, "cron", hour=4, minute=0, id="retention")
    scheduler.add_job(tasks.job_sync_profile_all, "cron", hour=2, minute=0, id="profile_sync")
    # Rollover: primary run at month start, plus a daily guard so a worker that
    # was down at 00:30 on the 1st still finalizes last month (job_rollover is
    # idempotent — re-fetch/upsert + finalize + purge).
    scheduler.add_job(tasks.job_rollover, "cron", day=1, hour=0, minute=30, id="rollover")
    scheduler.add_job(tasks.job_rollover, "cron", hour=1, minute=0, id="rollover_guard")


def run_worker() -> None:
    log.info("report_cache worker starting", poll_seconds=_POLL_SECONDS, drain_batch=_DRAIN_BATCH)
    scheduler = BlockingScheduler(timezone="UTC")
    _register_schedules(scheduler)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: scheduler.shutdown(wait=False))
        except (ValueError, OSError):
            pass  # not on main thread (e.g. tests) — nothing to trap

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("report_cache worker stopped")


if __name__ == "__main__":
    run_worker()
