"""
tests/test_classifier.py — Phase 2 of AI_Answer_Quality_Fix_Plan.md:
the smart_answers gate on classify_question. Stubs the LLM so we test the
prompt we send (scope gating) and the routing of what comes back, with no
network calls.
"""

import os
import sys
import json

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm as llm_mod
from llm import classify_question, OUT_OF_SCOPE_SCOPES


def _stub(monkeypatch, reply):
    """Capture the system prompt sent to the LLM and return a canned reply."""
    captured = {}
    def fake_call_llm(prompt, system, *a, **k):
        captured["system"] = system
        captured["prompt"] = prompt
        return reply
    monkeypatch.setattr(llm_mod, "call_llm", fake_call_llm)
    return captured


def _classify(**kw):
    return classify_question("q", "sp_receipts", "openai", "key", None, **kw)


# ── prompt gating ────────────────────────────────────────────────────────────

def test_scope_block_absent_when_flag_off(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    _classify(smart_answers=False)
    assert "out_of_scope" not in cap["system"]

def test_scope_block_present_when_flag_on(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    _classify(smart_answers=True)
    assert "out_of_scope" in cap["system"]
    # every editable scope line is injected into the prompt
    for scope in OUT_OF_SCOPE_SCOPES:
        assert scope in cap["system"]

def test_business_reasoning_marked_not_out_of_scope(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"data_query"}')
    _classify(smart_answers=True)
    assert "world economy" in cap["system"]  # explicitly steered to data_query


# ── routing of returned type ─────────────────────────────────────────────────

def test_out_of_scope_type_passes_validation(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "out_of_scope", "response": "nope"}))
    out = _classify(smart_answers=True)
    assert out["type"] == "out_of_scope" and out["response"] == "nope"

def test_advice_routed_to_data_query(monkeypatch):
    # model returns data_query for "how do I increase sales" — must be preserved
    _stub(monkeypatch, '{"type":"data_query"}')
    assert _classify(smart_answers=True)["type"] == "data_query"

def test_unknown_type_falls_back_to_data_query(monkeypatch):
    _stub(monkeypatch, '{"type":"banana"}')
    assert _classify(smart_answers=True)["type"] == "data_query"

def test_malformed_json_falls_back(monkeypatch):
    _stub(monkeypatch, "not json at all")
    assert _classify(smart_answers=True)["type"] == "data_query"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
