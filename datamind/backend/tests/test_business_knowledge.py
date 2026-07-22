"""
tests/test_business_knowledge.py — PLAN_09 S4.
The business_knowledge / hybrid classifier route (D2). call_llm stubbed — we
test the prompt we send and the accepted-type routing, no network.
"""

import os
import sys
import json

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm as llm_mod
from llm import classify_question


def _stub(monkeypatch, reply):
    cap = {}
    def fake(prompt, system, *a, **k):
        cap["system"] = system
        return reply
    monkeypatch.setattr(llm_mod, "call_llm", fake)
    return cap


# ── prompt gating ────────────────────────────────────────────────────────────

def test_knowledge_block_absent_when_flag_off(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    classify_question("q", "t", "openai", "k", None, business_knowledge=False)
    assert "business_knowledge" not in cap["system"]

def test_knowledge_block_present_when_flag_on(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    classify_question("q", "t", "openai", "k", None, smart_answers=True, business_knowledge=True)
    assert "business_knowledge" in cap["system"]
    assert "hybrid" in cap["system"]
    # the real failing questions are steered in as few-shots
    assert "net and gross sales" in cap["system"]
    assert "average order value" in cap["system"]
    # coding stays out_of_scope
    assert "python script" in cap["system"]

def test_out_of_scope_narrowed_when_flag_on(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    classify_question("q", "t", "openai", "k", None, smart_answers=True, business_knowledge=True)
    assert "ALL in scope" in cap["system"]


# ── accepted-type routing ────────────────────────────────────────────────────

def test_business_knowledge_accepted_when_flag_on(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "business_knowledge"}))
    out = classify_question("define AOV", "t", "openai", "k", None, business_knowledge=True)
    assert out["type"] == "business_knowledge"

def test_hybrid_accepted_when_flag_on(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "hybrid"}))
    out = classify_question("explain my net vs gross", "t", "openai", "k", None, business_knowledge=True)
    assert out["type"] == "hybrid"

def test_business_knowledge_coerced_when_flag_off(monkeypatch):
    # flag off → the type isn't in the accepted set → falls back to data_query
    _stub(monkeypatch, json.dumps({"type": "business_knowledge"}))
    out = classify_question("define AOV", "t", "openai", "k", None, business_knowledge=False)
    assert out["type"] == "data_query"

def test_coding_still_out_of_scope(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "out_of_scope", "response": "no code help"}))
    out = classify_question("write a python script", "t", "openai", "k", None,
                            smart_answers=True, business_knowledge=True)
    assert out["type"] == "out_of_scope"
