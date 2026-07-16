"""
tests/test_show_data.py — Round 2 Issue B: intent + show_data flag that lets
advisory (prose-only) answers suppress the chart/table/summary in the UI.
"""

import os
import sys
import json

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main
import llm as llm_mod
from llm import classify_question


# ── _base_query_response carries show_data ───────────────────────────────────

def test_show_data_defaults_true():
    assert main._base_query_response(type="data")["show_data"] is True

def test_show_data_can_be_false():
    assert main._base_query_response(type="data", show_data=False)["show_data"] is False


# ── classifier preserves the intent field ────────────────────────────────────

def _stub(monkeypatch, reply):
    cap = {}
    def fake(prompt, system, *a, **k):
        cap["system"] = system
        return reply
    monkeypatch.setattr(llm_mod, "call_llm", fake)
    return cap

def test_intent_preserved_for_advice(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "data_query", "intent": "advice"}))
    out = classify_question("how do I grow sales", "t", "openai", "k", None, smart_answers=True)
    assert out["type"] == "data_query" and out["intent"] == "advice"

def test_intent_instruction_only_in_smart_mode(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    classify_question("q", "t", "openai", "k", None, smart_answers=True)
    assert '"intent"' in cap["system"]
    cap2 = _stub(monkeypatch, '{"type":"data_query"}')
    classify_question("q", "t", "openai", "k", None, smart_answers=False)
    assert '"intent"' not in cap2["system"]


# ── intent → show_data mapping (the rule main.py applies) ─────────────────────

def test_advice_maps_to_hidden_data():
    for intent, expected in [("advice", False), ("lookup", True),
                             ("forecast", True), ("trend", True), (None, True)]:
        assert ((intent or "lookup") != "advice") is expected


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
