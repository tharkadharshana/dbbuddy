"""
tests/test_empty_result.py — PLAN_09 S3.
Empty-result narrative (D5) + deflection template (D4). Pure assertions,
no network.
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import llm as llm_mod
from llm import classify_question, OUT_OF_SCOPE_DEFLECTION


# ── D5: empty-result narrative ────────────────────────────────────────────────

def test_empty_narrative_never_blank():
    out = main._empty_result_narrative("show me sales")
    assert out and out.strip()

def test_empty_narrative_names_period():
    out = main._empty_result_narrative("today how many shawarma sales?")
    assert "today" in out.lower()

def test_empty_narrative_offers_next_step():
    out = main._empty_result_narrative("sales for this month")
    assert "this month" in out.lower()
    # a concrete forward path, not a closed door
    assert any(w in out.lower() for w in ("try", "ask me", "wider", "different"))

def test_empty_narrative_no_period_still_offers():
    out = main._empty_result_narrative("net sales")
    assert out.strip() and ("try" in out.lower() or "ask me" in out.lower())


# ── D4: deflection template ───────────────────────────────────────────────────

def test_canned_deflection_has_offer():
    # (a) a "can't" clause and (b) a concrete "can" offer
    low = OUT_OF_SCOPE_DEFLECTION.lower()
    assert "can't" in low or "cannot" in low
    assert any(w in low for w in ("ask me", "sales", "products", "customers", "trends"))

def _stub_system(monkeypatch):
    cap = {}
    def fake(prompt, system, *a, **k):
        cap["system"] = system
        return '{"type":"data_query"}'
    monkeypatch.setattr(llm_mod, "call_llm", fake)
    return cap

def test_deflection_rule_in_prompt(monkeypatch):
    cap = _stub_system(monkeypatch)
    classify_question("q", "sp_receipts", "openai", "k", None, smart_answers=True)
    assert "DEFLECTION RULE" in cap["system"]
    assert "you CAN do" in cap["system"]

def test_date_and_memory_awareness_in_prompt(monkeypatch):
    cap = _stub_system(monkeypatch)
    classify_question("q", "sp_receipts", "openai", "k", None, smart_answers=True)
    assert "current date/time" in cap["system"]
    assert "summarise the conversation" in cap["system"]
