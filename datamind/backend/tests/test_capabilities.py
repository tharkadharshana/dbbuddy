"""
tests/test_capabilities.py — Round 2 Issue C: curated capabilities reply +
classifier subtype detection (gated behind smart_answers).
"""

import os
import sys
import json

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm as llm_mod
from llm import classify_question, CAPABILITIES_MESSAGE


def _stub(monkeypatch, reply):
    cap = {}
    def fake(prompt, system, *a, **k):
        cap["system"] = system
        return reply
    monkeypatch.setattr(llm_mod, "call_llm", fake)
    return cap


def test_message_formats_with_app_and_provider():
    out = CAPABILITIES_MESSAGE.format(app="SalesPlay AI", provider="SalesPlay")
    assert "SalesPlay AI" in out and "SalesPlay" in out
    assert "**Track performance**" in out          # markdown bullets present

def test_capabilities_instruction_only_in_smart_mode(monkeypatch):
    cap = _stub(monkeypatch, '{"type":"conversational"}')
    classify_question("what can you do", "t", "openai", "k", None, smart_answers=True)
    assert "capabilities" in cap["system"]
    cap2 = _stub(monkeypatch, '{"type":"conversational"}')
    classify_question("what can you do", "t", "openai", "k", None, smart_answers=False)
    assert "capabilities" not in cap2["system"]

def test_subtype_preserved(monkeypatch):
    _stub(monkeypatch, json.dumps({"type": "conversational", "subtype": "capabilities"}))
    out = classify_question("what can you do", "t", "openai", "k", None, smart_answers=True)
    assert out["type"] == "conversational" and out["subtype"] == "capabilities"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
