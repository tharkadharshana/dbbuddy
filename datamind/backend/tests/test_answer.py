"""
tests/test_answer.py — PLAN 05 read-through answer resolver + shop resolution.

Hermetic: coverage/facts reads, tier window, and the report API client are all
monkeypatched. Verifies the doc-09 branch decisions: tier refusal, cache fast
path, non-additive → live, open period → live, and shop authorization.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache import answer as ans
from report_cache import tiers


class _FakeClient:
    """Stands in for ReportAPIClient — records that a live fetch happened and
    returns a canned summary/table_data."""
    calls = []

    def __init__(self, token):
        pass

    def fetch_report_all_pages(self, report_id, **kw):
        _FakeClient.calls.append((report_id, kw))
        return {
            "summary": {"net_sales": "999.00", "gross_profit": "100.00",
                        "operating_expenses": "50.00"},
            "table_data": [],
        }


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(ans, "ReportAPIClient", _FakeClient)
    yield


def _no_live(monkeypatch):
    """Make any live fetch fail loudly so a test asserting the cache path can't
    silently pass by going live."""
    class _Boom:
        def __init__(self, token): pass
        def fetch_report_all_pages(self, *a, **k): raise AssertionError("unexpected live fetch")
    monkeypatch.setattr(ans, "ReportAPIClient", _Boom)


def test_tier_refusal_out_of_window_no_fetch(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 5, 1))
    _no_live(monkeypatch)
    out = ans.answer_metric_query(
        conn=None, tenant_id="t1", report_id="sales_summary", metrics=["net_sales"],
        start=date(2025, 1, 1), end=date(2025, 2, 1), shop_id="all", token="tok", tier="basic",
    )
    assert "refusal" in out
    assert "3 months" in out["refusal"]


def test_cache_fast_path_when_covered_and_closed(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 1, 1))
    monkeypatch.setattr(ans, "read_coverage", lambda *a, **k: {"covered": True, "has_open": False})
    monkeypatch.setattr(ans, "get_daily_facts",
                        lambda *a, **k: [{"metrics": {"net_sales": 100.0}},
                                         {"metrics": {"net_sales": 300.0}}])
    _no_live(monkeypatch)
    out = ans.answer_metric_query(
        conn=None, tenant_id="t1", report_id="sales_summary", metrics=["net_sales"],
        start=date(2026, 6, 1), end=date(2026, 6, 30), shop_id="all", token="tok", tier="standard",
    )
    assert out["provenance"] == "from_cache"
    assert out["summary"]["net_sales"] == 400.0


def test_non_additive_forces_live_fetch(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 1, 1))
    monkeypatch.setattr(ans, "read_coverage", lambda *a, **k: {"covered": True, "has_open": False})
    out = ans.answer_metric_query(
        conn=None, tenant_id="t1", report_id="sales_summary", metrics=["operating_expenses"],
        start=date(2026, 6, 1), end=date(2026, 6, 30), shop_id="all", token="tok", tier="standard",
    )
    assert out["provenance"] == "from_live"
    assert out["summary"]["operating_expenses"] == 50.0
    assert _FakeClient.calls  # a live fetch actually happened


def test_open_period_forces_live_fetch(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 1, 1))
    monkeypatch.setattr(ans, "read_coverage", lambda *a, **k: {"covered": True, "has_open": True})
    out = ans.answer_metric_query(
        conn=None, tenant_id="t1", report_id="sales_summary", metrics=["net_sales"],
        start=date(2026, 7, 1), end=date(2026, 7, 14), shop_id="all", token="tok", tier="standard",
    )
    assert out["provenance"] == "from_live"
    assert _FakeClient.calls


def test_non_cacheable_report_always_live(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date(2026, 1, 1))
    # receipts is scalar but NOT daily_cacheable → coverage never consulted, straight to live
    monkeypatch.setattr(ans, "read_coverage",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("coverage should be skipped")))
    out = ans.answer_metric_query(
        conn=None, tenant_id="t1", report_id="receipts", metrics=["receipt_count"],
        start=date(2026, 6, 1), end=date(2026, 6, 30), shop_id="all", token="tok", tier="standard",
    )
    assert out["provenance"] == "from_live"


# ── shop resolution (report_tools) ───────────────────────────────────────────

def test_resolve_shop_known_and_unknown(monkeypatch):
    from mcp_server import report_tools as rt
    from report_cache import lookups

    rctx = rt.ReportToolContext(
        business=None, tenant_id="t1", token="tok", tier="basic", currency="$",
        shops=[{"shop_id": "1072", "shop_name": "Colombo"}],
    )
    monkeypatch.setattr(lookups, "resolve_shop",
                        lambda t, text: "1072" if "colombo" in text.lower() else None)
    monkeypatch.setattr(lookups, "is_shop_allowed", lambda t, sid: sid == "1072")

    assert rt._resolve_shop(rctx, "Colombo") == "1072"
    assert rt._resolve_shop(rctx, None) == "all"
    with pytest.raises(ValueError):
        rt._resolve_shop(rctx, "Atlantis")
