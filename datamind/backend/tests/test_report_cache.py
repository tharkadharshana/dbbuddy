"""
tests/test_report_cache.py — registry sanity, normalization (separator-aware),
and period helpers for the report cache.
"""

import os
import sys
from datetime import date

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from report_cache import normalize
from report_cache.ingest import closed_months, month_end, month_start
from report_cache.registry import CORE_REPORTS, REPORTS


# ── registry sanity ──────────────────────────────────────────────────────────

def test_core_reports_all_registered():
    for rid in CORE_REPORTS:
        assert rid in REPORTS


def test_every_ratio_metric_resolves():
    for report in REPORTS.values():
        for m in report.metrics:
            if m.agg == "ratio":
                assert report.metric(m.num), f"{report.id}.{m.key}: bad num {m.num}"
                assert report.metric(m.den), f"{report.id}.{m.key}: bad den {m.den}"


def test_no_endpoint_repeats_app_prefix():
    for report in REPORTS.values():
        assert not report.endpoint.startswith("/app/"), report.id


def test_dimensional_reports_declare_dim_fields():
    for report in REPORTS.values():
        if report.kind == "dimensional":
            assert report.dim_fields, report.id


# ── parse_number (separator-aware) ───────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("201,852.00", 201852.0),
    ("0.00", 0.0),
    ("1,234.56", 1234.56),
    ("(500.00)", -500.0),
    ("", None), ("-", None), (None, None),
    (42, 42.0), (3.5, 3.5),
    ("Rs. 1,000.50", 1000.5),
])
def test_parse_number_default_separators(raw, expected):
    assert normalize.parse_number(raw) == expected


def test_parse_number_european_separators():
    # decimal_separator=',' -> thousands '.', per NumberFormatRepository
    assert normalize.parse_number("1.234,56", ",", ".") == 1234.56
    assert normalize.parse_number("201.852,00", ",", ".") == 201852.0


def test_separators_from_profile_json():
    nf = '{"decimal_separator": ",", "thousond_separator": ".", "number_of_decimel": "2"}'
    assert normalize.separators(nf) == (",", ".")
    assert normalize.separators(None) == (".", ",")
    assert normalize.separators("not json") == (".", ",")


# ── normalize rows ───────────────────────────────────────────────────────────

SS = REPORTS["sales_summary"]


def test_normalize_metrics_resolves_aliases_and_skips_ratios():
    # summary block uses 'tips'/'surcharge'; table rows use tips_amount/surcharge_amount
    out = normalize.normalize_metrics(SS, {
        "gross_sales": "1,000.00", "tips": "25.50", "surcharge": "5.00"})
    assert out["gross_sales"] == 1000.0
    assert out["tips_amount"] == 25.5
    assert out["surcharge_amount"] == 5.0
    assert "gross_margin_pct" not in out  # ratios recomputed at read time


def test_normalize_daily_rows():
    rows = [
        {"date": "2026-06-01", "gross_sales": "100.00", "net_sales": "90.00"},
        {"date": "Jun 02,2026", "gross_sales": "200.00"},
        {"date": "not a date", "gross_sales": "999.00"},   # skipped
    ]
    out = normalize.normalize_daily_rows(SS, rows)
    assert [d for d, _ in out] == [date(2026, 6, 1), date(2026, 6, 2)]
    assert out[0][1]["net_sales"] == 90.0


def test_normalize_dim_rows_category_composite_key():
    report = REPORTS["sales_by_category"]
    rows = [
        {"product_category": "Drinks", "sub_category": "Hot", "net_sales": "1,500.00"},
        {"product_category": "Drinks", "sub_category": "", "net_sales": "300.00"},
        {"product_category": "", "sub_category": "", "net_sales": "9.99"},  # skipped
    ]
    out = normalize.normalize_dim_rows(report, rows)
    assert [k for k, _, _ in out] == ["drinks / hot", "drinks"]
    assert out[0][2]["net_sales"] == 1500.0


# ── periods ──────────────────────────────────────────────────────────────────

def test_month_helpers():
    assert month_start("2026-06") == date(2026, 6, 1)
    assert month_end("2026-06") == date(2026, 6, 30)
    assert month_end("2024-02") == date(2024, 2, 29)  # leap year


def test_closed_months_excludes_current():
    out = closed_months(3, today=date(2026, 7, 15))
    assert out == ["2026-06", "2026-05", "2026-04"]


def test_closed_months_year_boundary():
    assert closed_months(2, today=date(2026, 1, 10)) == ["2025-12", "2025-11"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
