"""
tests/test_jobs.py — PLAN 04 background jobs.

Hermetic: ingestion, token resolution, and DB writes are all monkeypatched or
routed through small in-test fakes, so no MySQL / no POS API is touched.
Covers the four PLAN 04 Step 9 cases:
  1. job_onboard_tenant ingests exactly the recent-N-month set for all reports.
  2. job_retention_purge deletes out-of-window rows, keeps in-window, skips unlimited.
  3. request_backfill enqueues month-by-month tasks for the requested range.
  4. the circuit breaker opens after N failures and short-circuits.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pool
from report_cache import tiers
import report_cache.jobs.enqueue as queue  # submodule, not the re-exported enqueue() fn
from report_cache.jobs import tasks
from report_cache.jobs.guards import BreakerOpen, CircuitBreaker
from report_cache.registry import REPORTS
from tests.fakedb import FakeConn


# ── 1. Onboarding ───────────────────────────────────────────────────────────

def test_onboard_ingests_exactly_recent_n_months(monkeypatch):
    calls = []
    monkeypatch.setattr(pool, "get_internal_conn", lambda: FakeConn())
    monkeypatch.setattr(tasks, "is_onboarded", lambda t: False)
    monkeypatch.setattr(tasks, "_mark_onboarded", lambda t: None)
    monkeypatch.setattr(tasks, "sync_tenant_profile", lambda t, access_token=None: None)
    monkeypatch.setattr(tasks.rate_limiter, "acquire", lambda tid: None)
    monkeypatch.setattr(
        tasks, "ingest_period",
        lambda conn, tid, rid, tok, period, shop_id="all": calls.append((rid, period)),
    )

    result = tasks.job_onboard_tenant("t1", token="aat")

    n_months = tasks._ONBOARD_MONTHS
    assert result["onboarded"] is True
    assert len(calls) == len(REPORTS) * n_months
    assert {c[0] for c in calls} == set(REPORTS.keys())        # every registry report
    assert len({c[1] for c in calls}) == n_months              # exactly N distinct months
    assert all(m.day == 1 for _, m in calls)                   # month-first dates


def test_onboard_no_token_raises(monkeypatch):
    monkeypatch.setattr(tasks, "is_onboarded", lambda t: False)
    monkeypatch.setattr(tasks, "sync_tenant_profile", lambda t, access_token=None: None)
    monkeypatch.setattr(tasks, "_resolve_token", lambda t, tok: None)
    with pytest.raises(ValueError):
        tasks.job_onboard_tenant("t1")


# ── 2. Retention purge ──────────────────────────────────────────────────────

class PurgeFakeConn:
    """Filtering fake for the three DELETE ... WHERE <col> < cutoff statements
    _purge_tenant issues, so we can assert out-of-window rows go and in-window
    rows stay (a data-loss-adjacent path — worth a real check)."""

    def __init__(self, daily, dim, sync):
        self.daily, self.dim, self.sync = daily, dim, sync
        self._rowcount = 0

    def cursor(self, dictionary=False):
        return self

    def execute(self, sql, params=()):
        _tenant, cutoff = params
        low = sql.lower()
        if "report_daily_fact" in low:
            self._rowcount = self._purge(self.daily, "business_date", cutoff)
        elif "report_dim_fact" in low:
            self._rowcount = self._purge(self.dim, "period_month", cutoff)
        elif "report_sync_state" in low:
            self._rowcount = self._purge(self.sync, "period", cutoff)

    def _purge(self, rows, col, cutoff):
        before = len(rows)
        rows[:] = [r for r in rows if r[col] >= cutoff]
        return before - len(rows)

    @property
    def rowcount(self):
        return self._rowcount

    def close(self):
        pass

    def commit(self):
        pass


def test_purge_tenant_deletes_out_of_window_keeps_in_window(monkeypatch):
    daily = [{"business_date": date(2026, 1, 1)},   # out
             {"business_date": date(2026, 5, 1)}]   # in
    dim = [{"period_month": date(2026, 1, 1)},      # out
           {"period_month": date(2026, 6, 1)}]      # in
    sync = [{"period": date(2026, 2, 15)},          # out
            {"period": date(2026, 5, 15)}]          # in
    fake = PurgeFakeConn(daily, dim, sync)
    monkeypatch.setattr(pool, "get_internal_conn", lambda: fake)

    deleted = tasks._purge_tenant("t1", cutoff=date(2026, 4, 1))

    assert deleted == 3
    assert daily == [{"business_date": date(2026, 5, 1)}]
    assert dim == [{"period_month": date(2026, 6, 1)}]
    assert sync == [{"period": date(2026, 5, 15)}]


def test_retention_purge_skips_unlimited(monkeypatch):
    purged = []
    monkeypatch.setattr(tasks, "active_tenants", lambda: ["t_basic", "t_unlimited"])
    monkeypatch.setattr(tiers, "get_ai_tier",
                        lambda t: "unlimited" if t == "t_unlimited" else "basic")
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 4, 1))
    monkeypatch.setattr(tasks, "storage_metrics", lambda t: {})
    monkeypatch.setattr(tasks, "_purge_tenant",
                        lambda t, cutoff: purged.append((t, cutoff)) or 2)

    result = tasks.job_retention_purge()

    assert purged == [("t_basic", date(2026, 4, 1))]   # unlimited never purged
    assert result["deleted"] == 2


# ── 3. Lazy backfill ────────────────────────────────────────────────────────

def test_request_backfill_enqueues_month_by_month(monkeypatch):
    enqueued = []
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 1, 1))
    monkeypatch.setattr(
        queue, "enqueue",
        lambda task, tenant_id=None, **payload: enqueued.append((task, payload)) or len(enqueued),
    )

    ids = queue.request_backfill("t1", "sales_summary", date(2026, 3, 15), date(2026, 5, 20))

    assert len(ids) == 3
    assert all(task == "job_ingest_period" for task, _ in enqueued)
    assert {p["period_iso"] for _, p in enqueued} == {"2026-03-01", "2026-04-01", "2026-05-01"}


def test_request_backfill_range_before_window_enqueues_nothing(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 6, 1))
    monkeypatch.setattr(queue, "enqueue", lambda *a, **k: pytest.fail("should not enqueue"))
    assert queue.request_backfill("t1", "sales_summary", date(2026, 1, 1), date(2026, 3, 1)) == []


# ── 4. Circuit breaker ──────────────────────────────────────────────────────

def test_breaker_opens_after_threshold_and_short_circuits():
    cb = CircuitBreaker(threshold=3, cooldown=100)
    for _ in range(3):
        cb.record_failure("t1")
    with pytest.raises(BreakerOpen):
        cb.check("t1")


def test_breaker_success_resets_failure_count():
    cb = CircuitBreaker(threshold=3, cooldown=100)
    cb.record_failure("t1")
    cb.record_failure("t1")
    cb.record_success("t1")          # resets before threshold reached
    cb.record_failure("t1")
    cb.check("t1")                    # still closed — no exception


def test_breaker_is_per_tenant():
    # global_threshold high so one tenant's failures can't open the global breaker
    cb = CircuitBreaker(threshold=2, cooldown=100, global_threshold=100)
    cb.record_failure("t1")
    cb.record_failure("t1")          # opens t1 only
    with pytest.raises(BreakerOpen):
        cb.check("t1")
    cb.check("t2")                    # a different tenant is unaffected — no exception
