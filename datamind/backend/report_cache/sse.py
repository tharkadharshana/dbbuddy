"""
report_cache/sse.py
====================
Server-Sent Events contract + helpers for /v1/query/stream (PLAN 07). Defined
once here so the endpoint, the loop, and the tests all agree on the wire format.

Event types (data is always JSON):
  step   {"label": "...", "status": "running" | "done"}   — live progress
  token  {"text": "..."}                                    — incremental answer text
  data   {"columns": [...], "rows": [...], "provenance": "from_cache"|"from_live"}
  meta   {"conversation_id": ..., "data_as_of": ..., "report_id": ..., "route": ...}
  error  {"message": "...", "recoverable": true|false}
  done   {"ok": true}

A `: keep-alive` comment is sent during idle gaps so proxies/browsers don't drop
an in-progress stream (doc: PLAN 07 Step 1).
"""

import json
import os

from logger import get_logger

log = get_logger(__name__)

SSE_ENABLED = os.getenv("SSE_ENABLED", "").lower() == "true"
log.info("SSE streaming flag", enabled=SSE_ENABLED)

# Tunables (read once).
KEEPALIVE_SECONDS = float(os.getenv("SSE_KEEPALIVE_SECONDS", "15"))
DEADLINE_SECONDS = float(os.getenv("SSE_DEADLINE_SECONDS", "90"))   # overall wall-clock cap
TOKEN_CHUNK_WORDS = int(os.getenv("SSE_TOKEN_CHUNK_WORDS", "4"))    # words per token event (Option-A chunking)
TOKEN_CHUNK_DELAY = float(os.getenv("SSE_TOKEN_CHUNK_DELAY", "0.02"))

# Event names.
STEP, TOKEN, DATA, META, ERROR, DONE = "step", "token", "data", "meta", "error", "done"

KEEPALIVE = b": keep-alive\n\n"

# Response headers that disable buffering along the path to the browser.
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",   # nginx: don't buffer the stream
}


def sse_event(event: str, payload) -> bytes:
    """Serialize one SSE message. `payload` is JSON-encoded (default=str so dates
    etc. never blow up a stream mid-flight)."""
    data = json.dumps(payload if payload is not None else {}, default=str)
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


def chunk_text(text: str, words_per_chunk: int = TOKEN_CHUNK_WORDS):
    """Yield small text chunks for `token` events (Option-A narrative streaming —
    the final text split into word groups, so the client renders it progressively
    even when the LLM call itself wasn't streamed). Whitespace is preserved by
    re-attaching the trailing space to each chunk."""
    if not text:
        return
    words = text.split(" ")
    for i in range(0, len(words), max(1, words_per_chunk)):
        chunk = " ".join(words[i:i + words_per_chunk])
        # keep a trailing space between chunks so the reassembled text reads naturally
        if i + words_per_chunk < len(words):
            chunk += " "
        yield chunk
