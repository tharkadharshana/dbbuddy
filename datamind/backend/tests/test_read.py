"""
tests/test_read.py
====================
Unit tests for report_cache/read.py, using tests/fakedb.py's in-memory fake
and report_cache/store.py's real upsert functions to seed data (so these
tests exercise the real write->read round-trip, not a separate mock of
read.py's own SQL).
Run: cd datamind/backend && pytest tests/test_read.py -q
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fakedb import FakeConn

from report_cache.read import coverage, get_daily_facts, get_dim_facts
from report_cache.store import upsert_daily_fact, upsert_dim_fact

_TODAY = date.today()


def _seed_days(conn, tenant_id, report_id, shop_id, days_and_status):
    for business_date, status in days_and_status:
        upsert_daily_fact(conn, tenant_id, report_id, shop_id, business_date,
                           {"net_sales": 100.0}, status)


# ── get_daily_facts ──────────────────────────────────────────────────────────

def test_get_daily_facts_returns_rows_in_range_only():
    conn = FakeConn()
    upsert_daily_fact(conn, "t1", "sales_summary", "all", date(2026, 4, 5), {"net_sales": 10.0}, "closed")
    upsert_daily_fact(conn, "t1", "sales_summary", "all", date(2026, 4, 6), {"net_sales": 20.0}, "closed")
    upsert_daily_fact(conn, "t1", "sales_summary", "all", date(2026, 5, 1), {"net_sales": 30.0}, "closed")

    rows = get_daily_facts(conn, "t1", "sales_summary", date(2026, 4, 1), date(2026, 4, 30))

    assert [r["business_date"] for r in rows] == [date(2026, 4, 5), date(2026, 4, 6)]
    assert rows[0]["metrics"] == {"net_sales": 10.0}  # parsed back from JSON, not a raw string


def test_get_daily_facts_scoped_by_shop():
    conn = FakeConn()
    upsert_daily_fact(conn, "t1", "sales_summary", "1072", date(2026, 4, 5), {"net_sales": 10.0}, "closed")
    upsert_daily_fact(conn, "t1", "sales_summary", "all", date(2026, 4, 5), {"net_sales": 999.0}, "closed")

    rows = get_daily_facts(conn, "t1", "sales_summary", date(2026, 4, 1), date(2026, 4, 30), shop_id="1072")

    assert len(rows) == 1
    assert rows[0]["metrics"]["net_sales"] == 10.0


# ── get_dim_facts ─────────────────────────────────────────────────────────────

def test_get_dim_facts_scoped_to_month():
    conn = FakeConn()
    upsert_dim_fact(conn, "t1", "sales_by_products", "all", date(2026, 4, 1), "product",
                     "P1", "Widget", {"qty": 5}, "closed")
    upsert_dim_fact(conn, "t1", "sales_by_products", "all", date(2026, 5, 1), "product",
                     "P1", "Widget", {"qty": 7}, "closed")

    rows = get_dim_facts(conn, "t1", "sales_by_products", date(2026, 4, 1))

    assert len(rows) == 1
    assert rows[0]["metrics"] == {"qty": 5}


def test_get_dim_facts_normalizes_period_month_to_first_of_month():
    conn = FakeConn()
    upsert_dim_fact(conn, "t1", "sales_by_products", "all", date(2026, 4, 1), "product",
                     "P1", "Widget", {"qty": 5}, "closed")

    # querying with a mid-month date should still find the row (both get normalized to day=1)
    rows = get_dim_facts(conn, "t1", "sales_by_products", date(2026, 4, 15))
    assert len(rows) == 1


# ── coverage ─────────────────────────────────────────────────────────────────

def test_coverage_fully_covered_range():
    conn = FakeConn()
    _seed_days(conn, "t1", "sales_summary", "all", [
        (date(2026, 4, 5), "closed"), (date(2026, 4, 6), "closed"), (date(2026, 4, 7), "closed"),
    ])

    result = coverage(conn, "t1", "sales_summary", date(2026, 4, 5), date(2026, 4, 7))

    assert result["covered"] is True
    assert result["missing_days"] == []


def test_coverage_reports_missing_days():
    conn = FakeConn()
    _seed_days(conn, "t1", "sales_summary", "all", [
        (date(2026, 4, 5), "closed"), (date(2026, 4, 7), "closed"),
    ])

    result = coverage(conn, "t1", "sales_summary", date(2026, 4, 5), date(2026, 4, 7))

    assert result["covered"] is False
    assert result["missing_days"] == [date(2026, 4, 6)]


def test_coverage_has_open_when_a_present_day_is_open():
    conn = FakeConn()
    _seed_days(conn, "t1", "sales_summary", "all", [
        (date(2026, 4, 5), "closed"), (date(2026, 4, 6), "open"),
    ])

    result = coverage(conn, "t1", "sales_summary", date(2026, 4, 5), date(2026, 4, 6))

    assert result["has_open"] is True


def test_coverage_has_open_when_today_in_range_even_if_missing():
    conn = FakeConn()  # nothing seeded at all — today's row hasn't been ingested yet

    result = coverage(conn, "t1", "sales_summary", _TODAY - timedelta(days=1), _TODAY)

    assert result["has_open"] is True
    assert result["covered"] is False


def test_coverage_not_open_for_a_fully_past_closed_range():
    conn = FakeConn()
    _seed_days(conn, "t1", "sales_summary", "all", [
        (date(2020, 1, 1), "closed"), (date(2020, 1, 2), "closed"),
    ])

    result = coverage(conn, "t1", "sales_summary", date(2020, 1, 1), date(2020, 1, 2))

    assert result["has_open"] is False
    assert result["covered"] is True


def test_coverage_rejects_dimensional_report():
    conn = FakeConn()
    with pytest.raises(ValueError, match="scalar reports only"):
        coverage(conn, "t1", "sales_by_products", date(2026, 4, 1), date(2026, 4, 30))


def test_coverage_rejects_start_after_end():
    conn = FakeConn()
    with pytest.raises(ValueError):
        coverage(conn, "t1", "sales_summary", date(2026, 4, 30), date(2026, 4, 1))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
