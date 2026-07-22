"""
tests/test_sanitise_answer.py — PLAN_09 S2.
The pure output sanitiser that keeps internal identifiers out of answers.
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import sanitise_answer, CAPABILITIES_MESSAGE

FALLBACK = CAPABILITIES_MESSAGE.format(app="DataMind", provider="business")


def test_clean_answer_untouched():
    txt = "Your sales last month were LKR 412,000, up 8% from the month before."
    out, found = sanitise_answer(txt, FALLBACK)
    assert out == txt and found == []

def test_sp_table_names_detected():
    txt = ("Here are the available tables: sp_receipts, sp_products, sp_customers. "
           "Your revenue was LKR 10,000.")
    out, found = sanitise_answer(txt, FALLBACK)
    assert any(f.startswith("sp_") for f in found)
    assert "sp_receipts" not in out
    assert "LKR 10,000" in out  # the clean sentence survives

def test_ly_table_names_detected():
    out, found = sanitise_answer("Data comes from ly_receipts and ly_products.", FALLBACK)
    assert found and "ly_receipts" not in out

def test_sql_detected():
    txt = "I ran SELECT SUM(total) FROM sp_receipts WHERE tenant_id = 'x'. Total is LKR 5."
    out, found = sanitise_answer(txt, FALLBACK)
    assert "SQL" in found or any(f.startswith("sp_") for f in found)
    assert "SELECT" not in out.upper()

def test_tool_name_detected():
    txt = "Let me call get_report_metrics for that. You made LKR 20 today."
    out, found = sanitise_answer(txt, FALLBACK)
    assert "get_report_metrics" in found
    assert "get_report_metrics" not in out

def test_underscore_report_slug_detected():
    txt = "I checked the sales_by_products report. Top item was Pasta."
    out, found = sanitise_answer(txt, FALLBACK)
    assert "sales_by_products" in found
    assert "sales_by_products" not in out

def test_plain_business_words_not_flagged():
    # 'receipts' and 'taxes' are legitimate words, not internal identifiers
    txt = "You had 42 receipts today and paid LKR 300 in taxes."
    out, found = sanitise_answer(txt, FALLBACK)
    assert found == [] and out == txt

def test_all_internal_collapses_to_fallback():
    txt = "Here are the available tables: sp_receipts, sp_products, sp_categories, sp_shops."
    out, found = sanitise_answer(txt, FALLBACK)
    assert found and out == FALLBACK

def test_empty_input():
    assert sanitise_answer("", FALLBACK) == ("", [])
    assert sanitise_answer(None, FALLBACK) == (None, [])
