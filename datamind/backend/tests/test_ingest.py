"""
tests/test_ingest.py
======================
Unit tests for report_cache/ingest.py. No DB/network: report_daily_fact/
report_dim_fact/report_sync_state writes go through tests/fakedb.py's
in-memory fake; ReportAPIClient.fetch_report_all_pages is monkeypatched to
return fixture data instead of making an HTTP call.
Run: cd datamind/backend && pytest tests/test_ingest.py -q
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fakedb import FakeConn

import report_cache.ingest as ingest
from report_cache.client import ReportAPIClient

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

# Fixture dates are Apr 5-7, 2026 — safely in the past relative to any real
# run of this suite, so status_for() naturally computes 'closed' without
# needing to mock date.today().
_PAST_WINDOW_START = date(2026, 1, 1)
_FUTURE_WINDOW_START = date(2026, 12, 1)  # after the fixture's dates — forces a "before window" skip


def _load_sales_summary_fixture():
    with open(os.path.join(FIXTURES_DIR, "sales_summary_sample.json"), encoding="utf-8") as f:
        payload = json.load(f)
    return {
        "summary": payload["data"]["summary"],
        "table_data": payload["data"]["table_data"],
        "pagination_meta": {},
    }


@pytest.fixture(autouse=True)
def _patch_common(monkeypatch):
    """Every test: allow all shops by default, and don't hit the network."""
    monkeypatch.setattr(ingest, "is_shop_allowed", lambda tenant_id, shop_id: True)
    monkeypatch.setattr(ingest.tiers, "window_start", lambda tenant_id, today=None: _PAST_WINDOW_START)


def _patch_fetch(monkeypatch, return_value):
    monkeypatch.setattr(ReportAPIClient, "fetch_report_all_pages", lambda self, report_id, **params: return_value)


# ── ingest_scalar_report ─────────────────────────────────────────────────────

def test_ingest_scalar_report_writes_one_row_per_day(monkeypatch):
    _patch_fetch(monkeypatch, _load_sales_summary_fixture())
    conn = FakeConn()

    result = ingest.ingest_scalar_report(
        conn, "sp_tenant1", "sales_summary", "fake-token",
        date(2026, 4, 1), date(2026, 4, 30),
    )

    assert result == {"ingested": 3, "skipped_reason": None}
    rows = conn.daily_fact_rows()
    assert len(rows) == 3
    dates = sorted(r["business_date"] for r in rows)
    assert dates == [date(2026, 4, 5), date(2026, 4, 6), date(2026, 4, 7)]

    row5 = next(r for r in rows if r["business_date"] == date(2026, 4, 5))
    metrics = json.loads(row5["metrics"])
    assert metrics["gross_sales"] == 201852.0
    assert row5["status"] == "closed"  # April 2026 is in the past relative to any real run


def test_ingest_scalar_report_sets_sync_state_for_full_month_only(monkeypatch):
    _patch_fetch(monkeypatch, _load_sales_summary_fixture())
    conn = FakeConn()

    # full calendar month -> sync_state row IS written
    ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                 date(2026, 4, 1), date(2026, 4, 30))
    assert len(conn.sync_state_rows()) == 1
    state = conn.sync_state_rows()[0]
    assert state["period"] == date(2026, 4, 1)
    assert state["grain"] == "day"
    assert state["status"] == "closed"


def test_ingest_scalar_report_partial_month_range_skips_sync_state(monkeypatch):
    _patch_fetch(monkeypatch, _load_sales_summary_fixture())
    conn = FakeConn()

    # range doesn't cover the whole of April -> no sync_state claim of full coverage
    ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                 date(2026, 4, 5), date(2026, 4, 20))
    assert conn.sync_state_rows() == []
    # but the days actually returned are still written as facts
    assert len(conn.daily_fact_rows()) == 3


def test_ingest_scalar_report_is_idempotent(monkeypatch):
    _patch_fetch(monkeypatch, _load_sales_summary_fixture())
    conn = FakeConn()

    ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                 date(2026, 4, 1), date(2026, 4, 30))
    ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                 date(2026, 4, 1), date(2026, 4, 30))

    assert len(conn.daily_fact_rows()) == 3  # no duplicates
    assert conn.sync_state_rows()[0]["attempts"] == 2  # but the attempt was recorded twice


def test_ingest_scalar_report_skips_entirely_before_window(monkeypatch):
    monkeypatch.setattr(ingest.tiers, "window_start", lambda tenant_id, today=None: _FUTURE_WINDOW_START)
    fetch_calls = []
    monkeypatch.setattr(ReportAPIClient, "fetch_report_all_pages",
                         lambda self, report_id, **params: fetch_calls.append(params) or {})
    conn = FakeConn()

    result = ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                          date(2026, 4, 1), date(2026, 4, 30))

    assert result == {"ingested": 0, "skipped_reason": "before_window"}
    assert fetch_calls == []  # never even called the API
    assert conn.daily_fact_rows() == []


def test_ingest_scalar_report_clips_start_to_window(monkeypatch):
    monkeypatch.setattr(ingest.tiers, "window_start", lambda tenant_id, today=None: date(2026, 4, 6))
    captured = {}

    def fake_fetch(self, report_id, **params):
        captured.update(params)
        return _load_sales_summary_fixture()
    monkeypatch.setattr(ReportAPIClient, "fetch_report_all_pages", fake_fetch)
    conn = FakeConn()

    ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                 date(2026, 4, 1), date(2026, 4, 30))

    assert captured["start_date"] == "2026-04-06"  # clipped forward from Apr 1
    # Apr 5 row from the fixture is dropped — it's before the clipped start
    assert date(2026, 4, 5) not in {r["business_date"] for r in conn.daily_fact_rows()}


def test_ingest_scalar_report_rejects_disallowed_shop(monkeypatch):
    monkeypatch.setattr(ingest, "is_shop_allowed", lambda tenant_id, shop_id: False)
    conn = FakeConn()

    with pytest.raises(ValueError, match="not allowed"):
        ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                     date(2026, 4, 1), date(2026, 4, 30), shop_id="9999")


def test_ingest_scalar_report_rejects_dimensional_report_id(monkeypatch):
    conn = FakeConn()
    with pytest.raises(ValueError, match="dimensional"):
        ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_by_products", "fake-token",
                                     date(2026, 4, 1), date(2026, 4, 30))


def test_ingest_scalar_report_records_error_and_reraises_on_fetch_failure(monkeypatch):
    def _raise(self, report_id, **params):
        raise RuntimeError("Report API HTTP 500")
    monkeypatch.setattr(ReportAPIClient, "fetch_report_all_pages", _raise)
    conn = FakeConn()

    with pytest.raises(RuntimeError, match="HTTP 500"):
        ingest.ingest_scalar_report(conn, "sp_tenant1", "sales_summary", "fake-token",
                                     date(2026, 4, 1), date(2026, 4, 30))

    assert conn.daily_fact_rows() == []
    state = conn.sync_state_rows()[0]
    assert state["status"] == "error"
    assert "HTTP 500" in state["last_error"]


# ── ingest_dimensional_report ────────────────────────────────────────────────

def _dim_fetch_result(n_products: int):
    table_data = [
        {
            "product_code": f"P{i}", "product_name": f"Product {i}",
            "qty": str(10 + i), "net_sale": str(1000.0 * (n_products - i)),  # descending sales size
            "cost": "10.00", "gross_sale": "1000.00", "discount": "0.00",
            "refund": "0.00", "refund_qty": "0", "product_gross_profit": "100.00",
            "product_cost": "5.00", "product_price": "50.00", "profit_margin": "10.00",
        }
        for i in range(n_products)
    ]
    return {"summary": {}, "table_data": table_data, "pagination_meta": {}}


def test_ingest_dimensional_report_writes_one_row_per_product(monkeypatch):
    _patch_fetch(monkeypatch, _dim_fetch_result(3))
    conn = FakeConn()

    result = ingest.ingest_dimensional_report(conn, "sp_tenant1", "sales_by_products", "fake-token",
                                                date(2026, 4, 1))

    assert result == {"ingested": 3, "skipped_reason": None}
    rows = conn.dim_fact_rows()
    assert len(rows) == 3
    assert {r["dim_key"] for r in rows} == {"P0", "P1", "P2"}
    assert all(r["period_month"] == date(2026, 4, 1) for r in rows)
    assert all(r["dim_type"] == "product" for r in rows)


def test_ingest_dimensional_report_top_n_cap_creates_other_row(monkeypatch):
    _patch_fetch(monkeypatch, _dim_fetch_result(5))
    conn = FakeConn()

    result = ingest.ingest_dimensional_report(conn, "sp_tenant1", "sales_by_products", "fake-token",
                                                date(2026, 4, 1), top_n=2)

    assert result == {"ingested": 3, "skipped_reason": None}  # 2 kept + 1 'other'
    rows = conn.dim_fact_rows()
    kept = [r for r in rows if r["dim_key"] != "__other__"]
    other = [r for r in rows if r["dim_key"] == "__other__"]
    assert len(kept) == 2
    assert len(other) == 1
    # kept rows are the top-2 by net_sale (P0 and P1 have the highest, per _dim_fetch_result)
    assert {r["dim_key"] for r in kept} == {"P0", "P1"}
    other_metrics = json.loads(other[0]["metrics"])
    # 'other' sums the sum-additive metrics of the 3 overflow rows (P2, P3, P4)
    assert other_metrics["qty"] == (10 + 2) + (10 + 3) + (10 + 4)


def test_ingest_dimensional_report_skips_before_window(monkeypatch):
    monkeypatch.setattr(ingest.tiers, "window_start", lambda tenant_id, today=None: _FUTURE_WINDOW_START)
    fetch_calls = []
    monkeypatch.setattr(ReportAPIClient, "fetch_report_all_pages",
                         lambda self, report_id, **params: fetch_calls.append(params) or {})
    conn = FakeConn()

    result = ingest.ingest_dimensional_report(conn, "sp_tenant1", "sales_by_products", "fake-token",
                                                date(2026, 4, 1))

    assert result == {"ingested": 0, "skipped_reason": "before_window"}
    assert fetch_calls == []


def test_ingest_dimensional_report_is_idempotent(monkeypatch):
    _patch_fetch(monkeypatch, _dim_fetch_result(3))
    conn = FakeConn()

    ingest.ingest_dimensional_report(conn, "sp_tenant1", "sales_by_products", "fake-token", date(2026, 4, 1))
    ingest.ingest_dimensional_report(conn, "sp_tenant1", "sales_by_products", "fake-token", date(2026, 4, 1))

    assert len(conn.dim_fact_rows()) == 3
    assert len(conn.sync_state_rows()) == 1
    assert conn.sync_state_rows()[0]["attempts"] == 2


# ── ingest_period dispatcher ──────────────────────────────────────────────────

def test_ingest_period_dispatches_to_scalar(monkeypatch):
    _patch_fetch(monkeypatch, _load_sales_summary_fixture())
    conn = FakeConn()

    result = ingest.ingest_period(conn, "sp_tenant1", "sales_summary", "fake-token", date(2026, 4, 15))

    assert result["ingested"] == 3
    assert len(conn.daily_fact_rows()) == 3


def test_ingest_period_dispatches_to_dimensional(monkeypatch):
    _patch_fetch(monkeypatch, _dim_fetch_result(2))
    conn = FakeConn()

    result = ingest.ingest_period(conn, "sp_tenant1", "sales_by_products", "fake-token", date(2026, 4, 15))

    assert result["ingested"] == 2
    assert len(conn.dim_fact_rows()) == 2


def test_ingest_period_unknown_report_raises():
    conn = FakeConn()
    with pytest.raises(ValueError, match="Unknown report_id"):
        ingest.ingest_period(conn, "sp_tenant1", "not_a_real_report", "fake-token", date(2026, 4, 15))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
