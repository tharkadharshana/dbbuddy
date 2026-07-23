"""
tests/test_followup_rewrite.py — PLAN_09 S1.
Pure/stubbed tests for the follow-up rewriter and clarification guard helper.
No network: call_llm is monkeypatched.
"""

import os
import sys
import json

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm as llm_mod
from llm import rewrite_followup, last_assistant_was_clarification


def _stub(monkeypatch, reply):
    def fake_call_llm(prompt, system, *a, **k):
        return reply
    monkeypatch.setattr(llm_mod, "call_llm", fake_call_llm)


def _rewrite(**kw):
    return rewrite_followup("q", "User: sales for Pasta\nAssistant: Found it.",
                            "openai", "key", None, **kw)


# ── last_assistant_was_clarification ──────────────────────────────────────────

def test_clarification_detected():
    h = "User: sales\nAssistant: Please specify which period you'd like?"
    assert last_assistant_was_clarification(h) is True

def test_normal_answer_not_clarification():
    h = "User: sales last month\nAssistant: Found 1 result. total = 44,991.00"
    assert last_assistant_was_clarification(h) is False

def test_answer_with_offer_question_not_clarification():
    # an answer that ends with an offer question but no clarify markers → False
    h = "User: sales\nAssistant: You did 4,200 today. Want a breakdown by hour?"
    assert last_assistant_was_clarification(h) is False

def test_only_last_assistant_turn_counts():
    h = ("User: a\nAssistant: Please specify which metric?\n"
         "User: sales\nAssistant: Found 1 result. total = 10.00")
    assert last_assistant_was_clarification(h) is False

def test_empty_history_is_false():
    assert last_assistant_was_clarification("") is False


# ── rewrite_followup ──────────────────────────────────────────────────────────

def test_empty_history_returns_original():
    # no history → no call, original unchanged
    out = rewrite_followup("for this week", "", "openai", "key", None)
    assert out == {"standalone": "for this week", "resolved": True, "carried": [], "changed": False}

def test_standalone_passes_through(monkeypatch):
    _stub(monkeypatch, json.dumps({"standalone": "q", "resolved": True,
                                   "carried": [], "changed": False}))
    out = _rewrite()
    assert out["standalone"] == "q" and out["changed"] is False

def test_ellipsis_is_resolved(monkeypatch):
    _stub(monkeypatch, json.dumps({
        "standalone": "What were the sales for Fried Rice for Pasta's period?",
        "resolved": True, "carried": ["period", "metric"], "changed": True}))
    out = rewrite_followup("then fried rice?", "User: sales for Pasta this week\nAssistant: ...",
                           "openai", "key", None)
    assert "Fried Rice" in out["standalone"]
    assert out["changed"] is True and "period" in out["carried"]

def test_malformed_json_falls_back(monkeypatch):
    _stub(monkeypatch, "not json at all")
    out = _rewrite()
    assert out["standalone"] == "q" and out["resolved"] is True and out["changed"] is False

def test_exception_falls_back(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")
    monkeypatch.setattr(llm_mod, "call_llm", boom)
    out = _rewrite()
    assert out["standalone"] == "q" and out["resolved"] is True

def test_code_fenced_json_parsed(monkeypatch):
    _stub(monkeypatch, "```json\n" + json.dumps({
        "standalone": "resolved question", "resolved": True,
        "carried": [], "changed": True}) + "\n```")
    out = _rewrite()
    assert out["standalone"] == "resolved question"

def test_unresolved_flag_preserved(monkeypatch):
    _stub(monkeypatch, json.dumps({"standalone": "q", "resolved": False,
                                   "carried": [], "changed": False}))
    out = _rewrite()
    assert out["resolved"] is False
