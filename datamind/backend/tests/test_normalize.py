"""
tests/test_normalize.py
========================
Unit tests for report_cache/normalize.py — pure functions, no DB/network.
Run: cd datamind/backend && pytest tests/test_normalize.py -q
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.normalize import (
    parse_number, parse_api_date,
    normalize_summary, normalize_daily_rows, normalize_dim_rows,
)

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


# ── parse_number ────────────────────────────────────────────────────────────

def test_parse_number_comma_thousands():
    assert parse_number("201,852.00") == 201852.0


def test_parse_number_negative():
    assert parse_number("-500.00") == -500.0


def test_parse_number_parenthesized_negative():
    assert parse_number("(500.00)") == -500.0


def test_parse_number_empty_and_dash():
    assert parse_number("") is None
    assert parse_number("-") is None
    assert parse_number(None) is None


def test_parse_number_zero_is_not_none():
    assert parse_number("0.00") == 0.0


def test_parse_number_currency_symbol():
    assert parse_number("$1,234.56") == 1234.56


def test_parse_number_already_numeric():
    assert parse_number(42) == 42.0
    assert parse_number(3.14) == 3.14


def test_parse_number_unparseable_returns_none():
    assert parse_number("N/A") is None


# ── parse_api_date ───────────────────────────────────────────────────────────

def test_parse_api_date_month_name_format():
    assert parse_api_date("Apr 05,2026") == "2026-04-05"


def test_parse_api_date_iso_passthrough():
    assert parse_api_date("2026-04-05") == "2026-04-05"


def test_parse_api_date_slash_format():
    assert parse_api_date("05/04/2026") == "2026-04-05"


def test_parse_api_date_empty_returns_none():
    assert parse_api_date("") is None
    assert parse_api_date(None) is None


# ── normalize_summary / normalize_daily_rows against the real fixture ───────

def _load_fixture():
    with open(os.path.join(FIXTURES_DIR, "sales_summary_sample.json"), encoding="utf-8") as f:
        return json.load(f)


def test_normalize_summary_sales_summary_fixture():
    payload = _load_fixture()
    summary = normalize_summary("sales_summary", payload["data"]["summary"])

    assert summary["gross_sales"] == 251852.0
    assert summary["net_sales"] == 250101.5
    assert summary["operating_profit"] == 129101.5
    assert summary["net_profit"] == 123669.4
    # summary block spells these "tips"/"surcharge" but the registry's
    # canonical metric key is tips_amount/surcharge_amount — alias resolution
    assert summary["tips_amount"] == 225.25
    assert summary["surcharge_amount"] == 25.0


def test_normalize_daily_rows_sales_summary_fixture():
    payload = _load_fixture()
    rows = normalize_daily_rows("sales_summary", payload["data"]["table_data"])

    assert len(rows) == 3
    assert rows[0]["business_date"] == "2026-04-05"
    assert rows[0]["metrics"]["gross_sales"] == 201852.0
    # "-" in surcharge_amount must parse to absent/None, not crash or become 0
    assert "surcharge_amount" not in rows[0]["metrics"]

    assert rows[1]["business_date"] == "2026-04-06"
    assert rows[1]["metrics"]["net_sales"] == -500.0
    assert rows[1]["metrics"]["surcharge_amount"] == 0.0

    assert rows[2]["business_date"] == "2026-04-07"
    assert rows[2]["metrics"]["tips_amount"] == 75.25


# ── normalize_dim_rows ───────────────────────────────────────────────────────

def test_normalize_dim_rows_products():
    rows = [
        {"product_code": "P1", "product_name": "Widget", "qty": "10.00",
         "net_sale": "1,000.00", "product_price": "100.00"},
        {"product_code": "", "product_name": "Ignored — no code", "qty": "5.00"},
    ]
    out = normalize_dim_rows("sales_by_products", rows)

    assert len(out) == 1
    assert out[0]["dim_key"] == "P1"
    assert out[0]["dim_name"] == "Widget"
    assert out[0]["metrics"]["qty"] == 10.0
    assert out[0]["metrics"]["net_sale"] == 1000.0


def test_normalize_dim_rows_non_dimensional_report_returns_empty():
    assert normalize_dim_rows("sales_summary", [{"foo": "bar"}]) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
