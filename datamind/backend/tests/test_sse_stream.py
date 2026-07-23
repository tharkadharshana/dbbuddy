"""
tests/test_sse_stream.py — the SSE streaming endpoint machinery: event
ordering (step -> thinking -> token -> data -> done), the progress bridge,
error degradation, and the flag-off 404. The pipeline itself is stubbed —
its own behavior is covered elsewhere.
"""

import json
import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import progress


# ── progress bridge ──────────────────────────────────────────────────────────

def test_emit_is_noop_without_listener():
    progress.emit("step", {"label": "x"})   # must not raise


def test_steps_list_emits_on_append():
    got = []
    token = progress.set_emitter(lambda ev, p: got.append((ev, p)))
    try:
        steps = progress.Steps()
        steps.append({"label": "Generating SQL query", "status": "running"})
        steps.append({"not_a_step": True})          # no label -> no event
        steps[-1]["status"] = "done"                # mutation -> no event
    finally:
        progress.reset_emitter(token)
    assert got == [("step", {"label": "Generating SQL query", "status": "running"})]
    assert len(steps) == 2                          # still a real list


def test_emitter_isolated_between_contexts():
    import threading
    got = []
    token = progress.set_emitter(lambda ev, p: got.append(ev))

    def other_thread():
        progress.emit("step", {"label": "leak?"})   # no emitter in this thread

    t = threading.Thread(target=other_thread)
    t.start(); t.join()
    progress.reset_emitter(token)
    assert got == []


def test_emitter_survives_asyncio_run():
    # the MCP tool loop runs under asyncio.run() inside the worker thread
    import asyncio
    got = []
    token = progress.set_emitter(lambda ev, p: got.append(p["label"]))

    async def loop_body():
        progress.emit("step", {"label": "inside loop"})

    try:
        asyncio.run(loop_body())
    finally:
        progress.reset_emitter(token)
    assert got == ["inside loop"]


# ── the streaming endpoint (pipeline stubbed) ────────────────────────────────

@pytest.fixture()
def client(monkeypatch):
    from dotenv import load_dotenv
    load_dotenv()
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(main, "_SSE_STREAMING_ENABLED", True)
    main.app.dependency_overrides[main.current_user] = lambda: {
        "email": "t@example.com", "settings": {}}

    def fake_impl(request, req, user):
        progress.emit("step", {"label": "Working", "status": "running"})
        progress.emit("thinking", {"text": "Let me check the sales report."})
        return {"success": True, "type": "data", "message": None,
                "analysis": "June net sales were Rs. 2,700 across 42 receipts.",
                "columns": ["net_sales"], "data": [{"net_sales": 2700}],
                "row_count": 1, "steps": [], "conversation_id": None}

    monkeypatch.setattr(main, "_natural_language_query_impl", fake_impl)
    with TestClient(main.app) as tc:
        yield tc
    main.app.dependency_overrides.clear()


def _parse_sse(body: str):
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines() if ": " in l)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines.get("data", "{}"))))
    return events


def test_stream_event_order_and_content(client):
    r = client.post("/v1/query/stream", json={"question": "sales last month?"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]

    assert kinds[0] == "step" and events[0][1]["label"] == "Working"
    assert kinds[1] == "thinking"
    assert "token" in kinds and kinds[-2] == "data" and kinds[-1] == "done"
    # tokens reassemble into the exact answer
    answer = "".join(p["text"] for e, p in events if e == "token")
    assert answer.strip() == "June net sales were Rs. 2,700 across 42 receipts."
    # the data event carries the full plain-endpoint payload
    data = dict(events)[ "data"]
    assert data["data"] == [{"net_sales": 2700}]


def test_stream_hides_internal_mechanics(client, monkeypatch):
    """The user must never see query-tool mechanics — internal pipeline labels
    are rewritten to AI-feel copy, and purely internal steps are suppressed."""
    import main

    def impl(request, req, user):
        progress.emit("step", {"label": "Generating SQL query", "status": "running"})
        progress.emit("step", {"label": "Answered via MCP tool-calling", "status": "done"})
        progress.emit("step", {"label": "Running query 2: SELECT net_sales FROM sp_receipts", "status": "running"})
        return {"success": True, "type": "data", "message": "Done.", "analysis": None,
                "columns": [], "data": [], "row_count": 0, "steps": [], "conversation_id": None}

    monkeypatch.setattr(main, "_natural_language_query_impl", impl)
    r = client.post("/v1/query/stream", json={"question": "x"})
    steps = [p["label"] for e, p in _parse_sse(r.text) if e == "step"]
    assert steps == ["Thinking through your question", "Analyzing your data"]
    for word in ("SQL", "query", "MCP", "SELECT", "sp_receipts"):
        assert word not in " ".join(steps)


def test_stream_error_degrades_gracefully(client, monkeypatch):
    import main

    def broken_impl(request, req, user):
        progress.emit("step", {"label": "Working", "status": "running"})
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "_natural_language_query_impl", broken_impl)
    r = client.post("/v1/query/stream", json={"question": "x"})
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert "error" in kinds and kinds[-1] == "done"
    assert "boom" not in r.text                    # internals never leak


def test_stream_404_when_flag_off(client, monkeypatch):
    import main
    monkeypatch.setattr(main, "_SSE_STREAMING_ENABLED", False)
    r = client.post("/v1/query/stream", json={"question": "x"})
    assert r.status_code == 404                    # clients fall back to /v1/query


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
