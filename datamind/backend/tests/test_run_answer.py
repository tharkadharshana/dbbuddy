"""
tests/test_run_answer.py — Phase 1 of AI_Answer_Quality_Fix_Plan.md:
the analyst answer pass. _format_result_context is pure; _run_answer's
prompt/coverage wiring is tested with the LLM stubbed (no network).
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main


COLS = ["product_id", "product_name", "total_quantity_sold", "net_sales"]
ROWS = [{"product_id": 5, "product_name": "Latte",
         "total_quantity_sold": 30, "net_sales": 1500.0}]


# ── _format_result_context ───────────────────────────────────────────────────

def test_strips_id_columns_from_context():
    hint, header, _, _ = main._format_result_context(COLS, ROWS, "LKR")
    assert "product_id" not in header
    assert "product_id" not in hint

def test_money_vs_value_tagging():
    hint, _, _, _ = main._format_result_context(COLS, ROWS, "LKR")
    assert "net_sales=MONEY" in hint
    assert "total_quantity_sold=VALUE" in hint   # count, not money

def test_truncation_note_only_when_over_50():
    _, _, _, note = main._format_result_context(COLS, ROWS, "LKR")
    assert note == ""
    _, _, _, note = main._format_result_context(COLS, ROWS * 60, "LKR")
    assert "60 rows" in note


# ── _run_answer prompt wiring ────────────────────────────────────────────────

def _stub(monkeypatch):
    cap = {}
    def fake(prompt, system, *a, **k):
        cap["prompt"], cap["system"], cap["kw"] = prompt, system, k
        return "ok"
    monkeypatch.setattr(main, "call_llm", fake)
    return cap

def test_no_coverage_line_when_period_absent(monkeypatch):
    cap = _stub(monkeypatch)
    main._run_answer("q", COLS, ROWS, "openai", "k", None, currency="LKR")
    assert "This data covers" not in cap["prompt"]
    assert cap["kw"]["max_tokens"] == 700          # analyst budget, not 200

def test_coverage_line_when_period_given(monkeypatch):
    cap = _stub(monkeypatch)
    main._run_answer("q", COLS, ROWS, "openai", "k", None, currency="LKR",
                     period_label="last 90 days (Apr 17 – Jul 16, 2026)")
    assert "last 90 days" in cap["prompt"]

def test_upsell_line_only_when_over_range(monkeypatch):
    cap = _stub(monkeypatch)
    main._run_answer("q", COLS, ROWS, "openai", "k", None, currency="LKR",
                     period_label="last 90 days", history_months=3, over_range=False)
    assert "higher tier" not in cap["prompt"]
    cap = _stub(monkeypatch)
    main._run_answer("q", COLS, ROWS, "openai", "k", None, currency="LKR",
                     period_label="last 90 days", history_months=3, over_range=True)
    assert "higher tier" in cap["prompt"]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
