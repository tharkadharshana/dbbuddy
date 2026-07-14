"""
tests/test_aggregate.py — PLAN 05 additivity aggregation (the correctness core).

The critical property: additive base metrics SUM across periods; ratios are
recomputed from the summed components (never averaged); non_additive metrics
are refused (must live-fetch), never fabricated. Pure functions — no DB, no HTTP.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.aggregate import aggregate_dim, aggregate_scalar, needs_live_fetch


def _daily(net, gp):
    return {"metrics": {"net_sales": net, "gross_profit": gp}}


def test_scalar_sums_additive_and_derives_ratio_not_mean():
    # daily margins 0.40 and 0.20; the CORRECT quarter margin is Σgp/Σnet = 100/400
    # = 0.25, NOT the mean of daily margins (0.30). This is the additivity trap.
    facts = [_daily(100.0, 40.0), _daily(300.0, 60.0)]
    agg = aggregate_scalar(facts, "sales_summary")

    assert agg["metrics"]["net_sales"] == 400.0
    assert agg["metrics"]["gross_profit"] == 100.0
    assert abs(agg["metrics"]["gross_margin_pct"] - 0.25) < 1e-9
    assert agg["days"] == 2
    assert agg["provenance"] == "from_cache"


def test_scalar_metric_filter():
    facts = [_daily(100.0, 40.0), _daily(300.0, 60.0)]
    agg = aggregate_scalar(facts, "sales_summary", metrics=["net_sales"])
    assert agg["metrics"] == {"net_sales": 400.0}


def test_scalar_non_additive_is_skipped_not_summed():
    facts = [{"metrics": {"net_sales": 100.0, "operating_expenses": 5.0}},
             {"metrics": {"net_sales": 200.0, "operating_expenses": 7.0}}]
    agg = aggregate_scalar(facts, "sales_summary", metrics=["operating_expenses"])
    assert "operating_expenses" not in agg["metrics"]     # never summed from parts
    assert agg["non_additive_skipped"] == ["operating_expenses"]


def test_ratio_zero_denominator_is_omitted():
    facts = [{"metrics": {"net_sales": 0.0, "gross_profit": 0.0}}]
    agg = aggregate_scalar(facts, "sales_summary")
    assert "gross_margin_pct" not in agg["metrics"]        # no divide-by-zero


def test_needs_live_fetch_rules():
    covered_closed = {"covered": True, "has_open": False}
    # daily-cacheable, additive, fully covered, closed → cache is fine
    assert needs_live_fetch("sales_summary", ["net_sales"], covered_closed) is False
    # non-additive metric requested → live
    assert needs_live_fetch("sales_summary", ["operating_expenses"], covered_closed) is True
    # missing days → live
    assert needs_live_fetch("sales_summary", ["net_sales"], {"covered": False, "has_open": False}) is True
    # open period in range → live
    assert needs_live_fetch("sales_summary", ["net_sales"], {"covered": True, "has_open": True}) is True
    # a scalar report whose daily facts aren't a valid per-day breakdown → always live
    assert needs_live_fetch("receipts", ["receipt_count"], covered_closed) is True


def _dim(key, name, qty, net, gp, month):
    return {"dim_key": key, "dim_name": name,
            "metrics": {"qty": qty, "net_sale": net, "product_gross_profit": gp},
            "period_month": month}


def test_dim_combines_across_months_ranks_and_caps():
    facts = [
        _dim("A", "Apple", 10, 100.0, 40.0, "2026-05-01"),
        _dim("A", "Apple", 5, 50.0, 10.0, "2026-06-01"),
        _dim("B", "Banana", 1, 1000.0, 500.0, "2026-05-01"),
    ]
    rows = aggregate_dim(facts, "sales_by_products")
    assert [r["dim_key"] for r in rows] == ["B", "A"]     # ranked by net_sale desc
    apple = next(r for r in rows if r["dim_key"] == "A")
    assert apple["metrics"]["qty"] == 15.0                # summed across months
    assert abs(apple["metrics"]["profit_margin"] - (50.0 / 150.0)) < 1e-9

    top1 = aggregate_dim(facts, "sales_by_products", top_n=1)
    assert [r["dim_key"] for r in top1] == ["B"]
