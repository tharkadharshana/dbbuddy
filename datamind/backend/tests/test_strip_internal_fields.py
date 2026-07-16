"""
tests/test_strip_internal_fields.py — llm.strip_internal_fields, the row
hygiene filter applied to raw report-API rows before they reach the user
(get_report_detail) or the model's final answer.
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import strip_internal_fields


def test_strips_surrogate_id_columns():
    out = strip_internal_fields([{"id": 1, "product_id": 5, "name": "Latte"}])
    assert out == [{"name": "Latte"}]


def test_strips_report_api_internal_fields():
    out = strip_internal_fields([{
        "key": "TERM-001", "app_key": "abc123", "terminal_key": "T9",
        "device_id": "D1", "invoice_key": "INV-1", "master_username": "owner",
        "user_name": "cashier1", "receipt_no": "R-100", "total": "50.00",
    }])
    assert out == [{"receipt_no": "R-100", "total": "50.00"}]


def test_strips_sensitive_columns():
    out = strip_internal_fields([{"api_key": "secret", "password": "x", "total": 10}])
    assert out == [{"total": 10}]


def test_empty_list_passthrough():
    assert strip_internal_fields([]) == []


def test_leaves_business_columns_untouched():
    row = {"receipt_no": "R-1", "total": "10.00", "date": "2026-06-01"}
    assert strip_internal_fields([row]) == [row]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
