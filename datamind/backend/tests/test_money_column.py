"""
tests/test_money_column.py — Phase 4 bugs from AI_Answer_Quality_Fix_Plan.md:
_is_money_column (counts must not be rendered as money) and _pick_summary_column
(the "…= X" answer-summary suffix must total the right column, never an ID).
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import _is_money_column, _pick_summary_column


# ── _is_money_column ─────────────────────────────────────────────────────────

def test_total_quantity_sold_not_money():
    assert _is_money_column("total_quantity_sold") is False

def test_total_revenue_is_money():
    assert _is_money_column("total_revenue") is True

def test_bare_count_columns_not_money():
    for c in ("total_customers", "receipt_count", "qty", "units_sold", "num_orders"):
        assert _is_money_column(c) is False, c

def test_money_words_still_match():
    for c in ("net_sales", "tax_amount", "discount", "profit", "avg_price", "total_paid"):
        assert _is_money_column(c) is True, c


# ── _pick_summary_column ─────────────────────────────────────────────────────

def test_prefers_money_over_quantity():
    row = {"product_id": 5, "qty": 3, "net_sales": 100.0}
    assert _pick_summary_column(["product_id", "qty", "net_sales"], row) == "net_sales"

def test_falls_back_to_quantity_when_no_money():
    row = {"product_id": 5, "qty": 3, "name": "Latte"}
    assert _pick_summary_column(["product_id", "qty", "name"], row) == "qty"

def test_skips_when_only_ids_or_non_numeric():
    row = {"product_id": 5, "name": "Latte"}
    assert _pick_summary_column(["product_id", "name"], row) is None

def test_never_sums_id_column():
    # an SKU-like numeric id must not be chosen even when it's the only number
    row = {"sku_id": 12345, "name": "Latte"}
    assert _pick_summary_column(["sku_id", "name"], row) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))


def test_refunds_and_singular_sale_are_money():
    """The report registry uses singular net_sale/gross_sale, and "refunds"
    contains no money fragment of its own. Both missed, so a document printed
    "1,263" beside a chat that said "LKR 1,263.05"."""
    for c in ("refunds", "refund", "net_sale", "gross_sale",
              "tips_amount", "surcharge_amount"):
        assert _is_money_column(c), c


def test_date_columns_are_not_money():
    """"sale" matches money, so sale_date needs an explicit guard."""
    for c in ("sale_date", "created_at", "sales_month", "order_year"):
        assert not _is_money_column(c), c


def test_count_columns_still_win_over_money():
    for c in ("refund_qty", "sold_qty", "receipt_count",
              "number_of_refunds_and_credit_note"):
        assert not _is_money_column(c), c
