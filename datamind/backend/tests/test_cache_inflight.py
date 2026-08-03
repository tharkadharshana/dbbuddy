"""The _inflight dedupe fix (doc 14 §B1.5).

Keyed on tenant_id alone, a running onboarding backfill swallowed every
warm_months_async call for that tenant — silently, with no log and no retry —
so a month requested during a backfill could stay uncached forever while every
ask for it went live (and then failed outright once the POS token expired).
"""
from report_cache import ingest


def setup_function():
    ingest._inflight.clear()


def test_backfill_does_not_block_month_warms_for_the_same_tenant():
    assert ingest._claim([("backfill", "t1")]) == [("backfill", "t1")]
    # The warm the old code dropped on the floor.
    assert ingest._claim([("t1", "sales_summary", "2026-04")])


def test_two_reports_warm_the_same_month_concurrently():
    assert ingest._claim([("t1", "sales_summary", "2026-04")])
    assert ingest._claim([("t1", "tax_summary", "2026-04")])


def test_the_same_work_is_still_deduped():
    key = ("t1", "sales_summary", "2026-04")
    assert ingest._claim([key]) == [key]
    assert ingest._claim([key]) == []          # already in flight
    ingest._release([key])
    assert ingest._claim([key]) == [key]       # released, claimable again


def test_partial_claim_returns_only_the_free_months():
    ingest._claim([("t1", "r", "2026-04")])
    claimed = ingest._claim([("t1", "r", "2026-04"), ("t1", "r", "2026-05")])
    assert claimed == [("t1", "r", "2026-05")]


def test_other_tenants_are_independent():
    assert ingest._claim([("t1", "r", "2026-04")])
    assert ingest._claim([("t2", "r", "2026-04")])
