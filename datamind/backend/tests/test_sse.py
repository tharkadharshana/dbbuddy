"""
tests/test_sse.py — PLAN 07 SSE event contract.

Covers the wire format (`sse_event`, `chunk_text`) and a fake loop that emits
step→token→data→done, parsed back into structured events — the same contract the
frontend consumes.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache import sse


def test_sse_event_format():
    out = sse.sse_event("step", {"label": "Choosing report", "status": "running"})
    assert isinstance(out, bytes)
    text = out.decode("utf-8")
    assert text.startswith("event: step\n")
    assert "\ndata: " in text
    assert text.endswith("\n\n")
    # the data line round-trips as JSON
    data_line = text.split("\ndata: ", 1)[1].rstrip("\n")
    assert json.loads(data_line) == {"label": "Choosing report", "status": "running"}


def test_sse_event_serializes_non_json_types():
    from datetime import date
    out = sse.sse_event("meta", {"as_of": date(2026, 7, 14)}).decode("utf-8")
    assert "2026-07-14" in out            # default=str kept the stream alive


def test_sse_event_none_payload():
    out = sse.sse_event("done", None).decode("utf-8")
    assert json.loads(out.split("\ndata: ", 1)[1].rstrip("\n")) == {}


def test_chunk_text_reassembles():
    text = "Your net profit last month was 12,340 dollars up nicely"
    chunks = list(sse.chunk_text(text, words_per_chunk=3))
    assert len(chunks) >= 2
    assert "".join(chunks) == text        # chunks reassemble to the original exactly


def test_chunk_text_empty():
    assert list(sse.chunk_text("")) == []


def _parse_stream(raw: bytes):
    """Parse a concatenated SSE byte stream back into [(event, data_dict)],
    ignoring keep-alive comments."""
    events = []
    for block in raw.decode("utf-8").split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        lines = block.split("\n")
        ev = next((l[len("event: "):] for l in lines if l.startswith("event: ")), None)
        data = next((l[len("data: "):] for l in lines if l.startswith("data: ")), "{}")
        events.append((ev, json.loads(data)))
    return events


def test_fake_loop_emits_wellformed_stream():
    """A fake producer emitting the canonical sequence yields a stream that parses
    back to step→token→data→done in order (mirrors the real endpoint's output)."""
    emitted = []

    def emit(event, payload=None):
        emitted.append(sse.sse_event(event, payload))

    emit(sse.STEP, {"label": "Understanding your question", "status": "done"})
    emit(sse.STEP, {"label": "Getting your figures", "status": "running"})
    emit(sse.STEP, {"label": "Getting your figures", "status": "done"})
    for chunk in sse.chunk_text("Net profit was 5,000.", words_per_chunk=2):
        emit(sse.TOKEN, {"text": chunk})
    emit(sse.DATA, {"columns": ["net_profit"], "rows": [{"net_profit": 5000}], "provenance": "from_cache"})
    emit(sse.META, {"conversation_id": "c1", "route": "business_data"})
    emit(sse.DONE, {"ok": True})

    parsed = _parse_stream(b"".join(emitted))
    kinds = [e for e, _ in parsed]
    assert kinds[0] == "step" and kinds[-1] == "done"
    assert "token" in kinds and "data" in kinds

    # tokens reassemble to the narrative
    narrative = "".join(p["text"] for e, p in parsed if e == "token")
    assert narrative == "Net profit was 5,000."
    # the data event carries the table + provenance
    data_ev = next(p for e, p in parsed if e == "data")
    assert data_ev["provenance"] == "from_cache"
    assert data_ev["rows"] == [{"net_profit": 5000}]


def test_stream_endpoint_end_to_end(monkeypatch):
    """Drive the real /v1/query/stream endpoint (queue + producer task +
    StreamingResponse + keep-alive plumbing) with the producer mocked, and assert
    the client receives a well-formed step→token→data→done stream."""
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(main._sse, "SSE_ENABLED", True)

    async def fake_produce(request, req, user, emit):
        emit(main._sse.STEP, {"label": "Understanding your question", "status": "done"})
        emit(main._sse.TOKEN, {"text": "Hello "})
        emit(main._sse.TOKEN, {"text": "world"})
        emit(main._sse.DATA, {"columns": [], "rows": [], "provenance": None})

    monkeypatch.setattr(main, "_produce_stream_answer", fake_produce)
    main.app.dependency_overrides[main.current_user] = lambda: {"email": "u@x.com", "settings": {}}
    try:
        client = TestClient(main.app)
        r = client.post("/v1/query/stream", json={"question": "hi"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        parsed = _parse_stream(r.content)
        kinds = [e for e, _ in parsed]
        assert kinds[-1] == "done"
        assert kinds.count("token") == 2
        assert "".join(p["text"] for e, p in parsed if e == "token") == "Hello world"
    finally:
        main.app.dependency_overrides.clear()


def test_stream_endpoint_404_when_flag_off(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    monkeypatch.setattr(main._sse, "SSE_ENABLED", False)
    main.app.dependency_overrides[main.current_user] = lambda: {"email": "u@x.com", "settings": {}}
    try:
        r = TestClient(main.app).post("/v1/query/stream", json={"question": "hi"})
        assert r.status_code == 404
    finally:
        main.app.dependency_overrides.clear()
