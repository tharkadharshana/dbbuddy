"""The registry must match the SalesPlay report API exactly.

Every Report.endpoint is a live HTTP path. A wrong one does not fail loudly at
import — it 404s at answer time, the tool errors, and the merchant is told their
data is unavailable. Before this test the registry had FIVE endpoints that do
not exist (/pickme_sales, /inventory_valuation, /stock_summary,
/backdate_stock, /product_wise_cash_refunds) and was missing five that do.

Parsed from the vendored Laravel routes at
docs/salesplay-internal-api-v2/routes/app.php. If that file is absent (it is a
reference copy, not a dependency) the endpoint checks skip rather than fail.
"""
import os
import re

import pytest

from report_cache.registry import CORE_REPORTS, REPORTS

_BS = chr(92)
_ROUTES = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "docs", "salesplay-internal-api-v2", "routes", "app.php")

_ROUTE_RE = re.compile(
    r"Route::(?:get|post|match)\(\s*(?:\[[^\]]*\]\s*,\s*)?'(/[a-z0-9_" + _BS + r"-/]+)'"
    r"\s*,\s*\[App" + _BS * 2 + r"Http" + _BS * 2 + r"Controllers" + _BS * 2 + r"App"
    + _BS * 2 + r"Reports" + _BS * 2 + r"([A-Za-z" + _BS * 2 + r"]+)::class\s*,\s*'([a-zA-Z]+)'\]")


def _live_endpoints():
    if not os.path.exists(_ROUTES):
        pytest.skip("vendored salesplay routes/app.php not present")
    text = open(_ROUTES, encoding="utf-8", errors="ignore").read()
    out = {}
    for m in _ROUTE_RE.finditer(text):
        path, ctrl, method = m.group(1), m.group(2), m.group(3)
        if method == "index" and "/grid-data" not in path:
            out[path] = ctrl.split(_BS)[-1]
    return out


def test_every_registry_endpoint_exists_in_the_api():
    live = _live_endpoints()
    broken = sorted(r.endpoint for r in REPORTS.values() if r.endpoint not in live)
    assert not broken, (
        "These endpoints are in the registry but not served by the API — "
        f"they would 404 at answer time: {broken}")


def test_every_api_report_is_in_the_registry():
    live = _live_endpoints()
    ours = {r.endpoint for r in REPORTS.values()}
    missing = sorted(set(live) - ours)
    assert not missing, f"Report endpoints the API serves but we never expose: {missing}"


# ── invariants that hold regardless of the vendored routes file ───────────────

def test_endpoint_matches_id_except_documented_hyphen_cases():
    """Endpoint is '/<id>' for every report. stock-history and inventory-expiry
    genuinely use hyphens in the API — that asymmetry is the kind of thing that
    silently breaks, so it is asserted rather than assumed."""
    for rid, r in REPORTS.items():
        assert r.endpoint == f"/{rid}", (rid, r.endpoint)


def test_core_reports_all_exist_and_are_cacheable():
    """CORE_REPORTS drives the onboarding backfill — a typo there means a
    report is never warmed and every ask for it goes live forever."""
    for rid in CORE_REPORTS:
        assert rid in REPORTS, f"CORE_REPORTS names '{rid}' which is not a report"
        assert REPORTS[rid].cacheable, f"core report '{rid}' must be cacheable"


def test_ratio_metrics_reference_real_metric_keys():
    """aggregate() recomputes ratios from num/den. A typo'd num or den silently
    yields None instead of a wrong number, so it hides rather than crashes."""
    for rid, r in REPORTS.items():
        keys = {m.key for m in r.metrics}
        for m in r.metrics:
            if m.agg == "ratio":
                assert m.num in keys, f"{rid}.{m.key}: num '{m.num}' is not a metric"
                assert m.den in keys, f"{rid}.{m.key}: den '{m.den}' is not a metric"


def test_dimensional_reports_declare_their_dimension():
    for rid, r in REPORTS.items():
        if r.kind == "dimensional":
            assert r.dim_fields, f"dimensional report '{rid}' has no dim_fields"


def test_no_duplicate_metric_keys_within_a_report():
    for rid, r in REPORTS.items():
        keys = [m.key for m in r.metrics]
        assert len(keys) == len(set(keys)), f"{rid} has duplicate metric keys"


def test_only_sales_summary_is_day_grain():
    """report_cache.answer treats grain='day' specially (read_daily_facts).
    Adding another day-grain report needs that path checked first."""
    day = {rid for rid, r in REPORTS.items() if r.grain == "day"}
    assert day == {"sales_summary"}, day
