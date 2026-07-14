"""
tests/test_registry.py
========================
Structural checks on report_cache/registry.py — catches typos in metric
additivity tags or ratio num/den references before they reach the aggregation
layer (PLAN 05).
Run: cd datamind/backend && pytest tests/test_registry.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.registry import REPORTS

_VALID_AGG = {"sum", "ratio", "non_additive"}
_EXPECTED_REPORT_IDS = {
    "sales_summary", "receipts", "refunds", "credit_notes",
    "taxes", "charges", "sales_by_products", "sales_by_category",
}


def test_all_8_reports_present():
    assert set(REPORTS.keys()) == _EXPECTED_REPORT_IDS
    assert len(REPORTS) == 8


def test_every_metric_agg_is_valid():
    for report in REPORTS.values():
        for metric in report.metrics:
            assert metric.agg in _VALID_AGG, f"{report.id}.{metric.key} has invalid agg {metric.agg!r}"


def test_every_ratio_metric_names_existing_num_den():
    for report in REPORTS.values():
        metric_keys = {m.key for m in report.metrics}
        for metric in report.metrics:
            if metric.agg != "ratio":
                continue
            assert metric.num in metric_keys, f"{report.id}.{metric.key}: num={metric.num!r} not a declared metric"
            assert metric.den in metric_keys, f"{report.id}.{metric.key}: den={metric.den!r} not a declared metric"


def test_dimensional_reports_have_dim_type():
    for report in REPORTS.values():
        if report.kind == "dimensional":
            assert report.dim_type in ("product", "category")
        else:
            assert report.dim_type is None


def test_every_report_has_endpoint_and_metrics():
    for report in REPORTS.values():
        assert report.endpoint.startswith("/")
        assert len(report.metrics) > 0
        assert len(report.params) > 0


def test_endpoint_does_not_repeat_app_prefix():
    """
    Regression guard: report_cache/client.py resolves its base URL from
    SALESPLAY_EMBED_PROXY_BASE, which already ends in ".../public/app"
    (routes/app.php is mounted at Route::prefix('app') — confirmed in
    RouteServiceProvider.php). If Report.endpoint also started with "/app/",
    the client would build a broken ".../public/app/app/sales_summary" URL.
    """
    for report in REPORTS.values():
        assert not report.endpoint.startswith("/app/"), (
            f"{report.id}.endpoint={report.endpoint!r} repeats the '/app' prefix "
            f"already present in SALESPLAY_EMBED_PROXY_BASE"
        )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
