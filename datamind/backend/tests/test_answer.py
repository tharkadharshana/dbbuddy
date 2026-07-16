"""
tests/test_answer.py — additivity-correct aggregation and the cache-vs-live
decision tree in report_cache/answer.py.
"""

import os
import sys
from datetime import date

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from report_cache import answer
from report_cache.registry import REPORTS

RECEIPTS = REPORTS["receipts"]
SS = REPORTS["sales_summary"]


# ── aggregate: the additivity trap ───────────────────────────────────────────

def test_sum_metrics_are_summed():
    out, dropped = answer.aggregate(RECEIPTS, [
        {"receipt_count": 10, "total_sum": 100.0},
        {"receipt_count": 30, "total_sum": 500.0},
    ], ["receipt_count", "total_sum"])
    assert out == {"receipt_count": 40, "total_sum": 600.0}
    assert dropped == []


def test_ratio_recomputed_from_sums_not_averaged():
    # avg of avgs would be (10.0 + 16.667)/2 = 13.33 — WRONG.
    # correct: (100+500)/(10+30) = 15.0
    out, _ = answer.aggregate(RECEIPTS, [
        {"receipt_count": 10, "total_sum": 100.0},
        {"receipt_count": 30, "total_sum": 500.0},
    ], ["avg_receipt_value"])
    assert out["avg_receipt_value"] == 15.0


def test_non_additive_dropped_across_periods():
    out, dropped = answer.aggregate(RECEIPTS, [
        {"total_customers": 17, "receipt_count": 10},
        {"total_customers": 12, "receipt_count": 30},
    ], ["total_customers", "receipt_count"])
    assert dropped == ["total_customers"]         # summing distinct counts is wrong
    assert out["receipt_count"] == 40


def test_non_additive_exact_for_single_period():
    out, dropped = answer.aggregate(RECEIPTS, [{"total_customers": 17}],
                                    ["total_customers"])
    assert out["total_customers"] == 17 and dropped == []


def test_none_values_ignored_in_sums():
    out, _ = answer.aggregate(RECEIPTS, [
        {"total_sum": 100.0}, {"total_sum": None}, {}], ["total_sum"])
    assert out["total_sum"] == 100.0


def test_ratio_none_when_denominator_zero():
    out, _ = answer.aggregate(RECEIPTS, [
        {"receipt_count": 0, "total_sum": 0.0}], ["avg_receipt_value"])
    assert out["avg_receipt_value"] is None


# ── _is_empty_row / dimensional row hygiene ──────────────────────────────────

def test_empty_row_all_zero_dropped():
    assert answer._is_empty_row({"name": "Ghost Product", "qty": 0, "net_sale": 0.0})


def test_empty_row_all_none_dropped():
    assert answer._is_empty_row({"name": "Ghost Product", "qty": None, "net_sale": None})


def test_empty_row_no_metrics_dropped():
    assert answer._is_empty_row({"name": "Ghost Product"})


def test_nonempty_row_kept():
    assert not answer._is_empty_row({"name": "Real Product", "qty": 3, "net_sale": 0.0})


def test_merge_dim_rows_drops_zero_activity_products():
    report = REPORTS["sales_by_products"]
    facts = [
        ("2026-06", "coffee", "Coffee", {"qty": 10, "net_sale": 500.0}),
        ("2026-06", "ghost", "Ghost Product", {"qty": 0, "net_sale": 0.0}),
    ]
    rows = answer._merge_dim_rows(report, facts)
    assert [r["name"] for r in rows] == ["Coffee"]


def test_undeclared_metrics_never_combined():
    voucher = REPORTS["voucher_receipts"]          # long-tail: no declarations
    assert not answer._requested_all_combinable(voucher, None, 2)
    out, dropped = answer.aggregate(voucher, [{"total": 5}, {"total": 7}], ["total"])
    assert dropped == ["total"]


# ── months_covering / cache-vs-live decision ─────────────────────────────────

def test_months_covering():
    assert answer.months_covering(date(2025, 11, 3), date(2026, 2, 10)) == \
        ["2025-11", "2025-12", "2026-01", "2026-02"]


def _decision(report_id, start, end, shop_id="all", cashier=None, metrics=None,
              monkeypatch=None):
    """True if answer_metric_query takes the cache path (stubbed I/O)."""
    took = {"cache": False}

    def fake_live(*a, **k):
        return {"metrics": {}, "rows": [], "source": "live"}

    def fake_cached(conn, tenant, rid, ym, shop_id="all"):
        took["cache"] = True
        return True

    monkeypatch.setattr(answer, "_answer_live", fake_live)
    monkeypatch.setattr(answer, "is_month_cached", fake_cached)
    monkeypatch.setattr(answer.read, "read_month_facts", lambda *a, **k: {})
    monkeypatch.setattr(answer.read, "read_daily_facts", lambda *a, **k: [])
    monkeypatch.setattr(answer.read, "read_dim_facts", lambda *a, **k: [])
    answer.answer_metric_query(None, "t", report_id, start, end,
                               shop_id=shop_id, cashier=cashier, metrics=metrics)
    return took["cache"]


TODAY = date.today()
LAST_CLOSED_END = answer.last_closed_day(TODAY)
LAST_CLOSED_START = LAST_CLOSED_END.replace(day=1)


def test_closed_month_all_shops_uses_cache(monkeypatch):
    assert _decision("receipts", LAST_CLOSED_START, LAST_CLOSED_END,
                     monkeypatch=monkeypatch) is True


def test_open_month_goes_live(monkeypatch):
    assert _decision("receipts", TODAY.replace(day=1), TODAY,
                     monkeypatch=monkeypatch) is False


def test_shop_filter_goes_live(monkeypatch):
    assert _decision("receipts", LAST_CLOSED_START, LAST_CLOSED_END,
                     shop_id="101", monkeypatch=monkeypatch) is False


def test_cashier_filter_goes_live(monkeypatch):
    assert _decision("receipts", LAST_CLOSED_START, LAST_CLOSED_END,
                     cashier="Kasun", monkeypatch=monkeypatch) is False


def test_partial_month_range_goes_live_for_month_grain(monkeypatch):
    assert _decision("receipts", LAST_CLOSED_START,
                     LAST_CLOSED_START.replace(day=10), monkeypatch=monkeypatch) is False


def test_partial_range_uses_cache_for_daily_grain(monkeypatch):
    # sales_summary has daily facts — any closed range works from cache
    assert _decision("sales_summary", LAST_CLOSED_START,
                     LAST_CLOSED_START.replace(day=10), monkeypatch=monkeypatch) is True


def test_live_only_report_never_cached(monkeypatch):
    assert _decision("stock_summary", LAST_CLOSED_START, LAST_CLOSED_END,
                     monkeypatch=monkeypatch) is False


def test_non_additive_metric_multi_month_goes_live(monkeypatch):
    start = date(LAST_CLOSED_START.year if LAST_CLOSED_START.month > 1 else LAST_CLOSED_START.year - 1,
                 LAST_CLOSED_START.month - 1 if LAST_CLOSED_START.month > 1 else 12, 1)
    assert _decision("receipts", start, LAST_CLOSED_END,
                     metrics=["total_customers"], monkeypatch=monkeypatch) is False


def test_unknown_report_raises():
    with pytest.raises(ValueError, match="Unknown report"):
        answer.answer_metric_query(None, "t", "nope", TODAY, TODAY)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
