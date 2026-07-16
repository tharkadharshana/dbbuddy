"""
tests/test_stream_llm.py — Phase 5: real token-delta streaming.
Stubs requests.post to feed a canned SSE body and checks the parser fires
on_delta per chunk, accumulates the full text, reads usage, and fails safely.
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import llm as llm_mod
from llm import _stream_chat_completions, LLMTransientError


class FakeResp:
    def __init__(self, lines, ok=True, status=200, text=""):
        self._lines, self.ok, self.status_code, self.text = lines, ok, status, text
    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)
    def raise_for_status(self):
        raise RuntimeError("http error")


def _patch(monkeypatch, resp):
    monkeypatch.setattr(llm_mod.requests, "post", lambda *a, **k: resp)


OAI_LINES = [
    'data: {"choices":[{"delta":{"content":"Hello"}}],"model":"gpt-4o-mini"}',
    '',
    'data: {"choices":[{"delta":{"content":" world"}}]}',
    'data: {"choices":[{"delta":{}}],"usage":{"total_tokens":42}}',
    'data: [DONE]',
]


def _run(monkeypatch, resp):
    deltas = []
    text, tokens, model = _stream_chat_completions(
        "openai", "http://x", "gpt-4o-mini", "p", "s", 700, "key",
        deltas.append, None)
    return deltas, text, tokens, model


def test_streams_deltas_in_order(monkeypatch):
    _patch(monkeypatch, FakeResp(OAI_LINES))
    deltas, text, tokens, model = _run(monkeypatch, None)
    assert deltas == ["Hello", " world"]
    assert text == "Hello world"
    assert tokens == 42
    assert model == "gpt-4o-mini"

def test_estimates_tokens_when_usage_absent(monkeypatch):
    lines = ['data: {"choices":[{"delta":{"content":"abcd efgh"}}]}', 'data: [DONE]']
    _patch(monkeypatch, FakeResp(lines))
    _, text, tokens, _ = _run(monkeypatch, None)
    assert text == "abcd efgh" and tokens > 0     # estimated, not zero

def test_empty_stream_raises(monkeypatch):
    _patch(monkeypatch, FakeResp(['data: [DONE]']))
    with pytest.raises(LLMTransientError):
        _run(monkeypatch, None)

def test_auth_error_raises_valueerror(monkeypatch):
    _patch(monkeypatch, FakeResp([], ok=False, status=401, text="bad key"))
    with pytest.raises(ValueError):
        _run(monkeypatch, None)

def test_transient_status_raises_transient(monkeypatch):
    _patch(monkeypatch, FakeResp([], ok=False, status=429, text="slow down"))
    with pytest.raises(LLMTransientError):
        _run(monkeypatch, None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
