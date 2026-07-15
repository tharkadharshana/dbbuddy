"""
progress.py — request-scoped progress events for SSE streaming.

A ContextVar holds the current request's emitter callback (set by the
/v1/query/stream worker thread, absent everywhere else). Code anywhere in the
query pipeline — main.py's steps list, the MCP tool-calling loop — calls
emit() without knowing whether anyone is listening; on the non-streaming
endpoint it's a no-op. ContextVars are per-thread/per-task, so concurrent
requests can't see each other's emitters, and asyncio.run() inside the worker
thread inherits the thread's context so tool-loop events flow too.
"""

from contextvars import ContextVar
from typing import Callable, Optional

_emitter: ContextVar[Optional[Callable]] = ContextVar("progress_emitter", default=None)


def set_emitter(cb: Callable):
    """Install the emitter for this thread/task. Returns a reset token."""
    return _emitter.set(cb)


def reset_emitter(token) -> None:
    _emitter.reset(token)


def emit(event: str, payload: dict) -> None:
    """Fire an event to the current request's listener, if any. Never raises."""
    cb = _emitter.get()
    if cb is None:
        return
    try:
        cb(event, payload)
    except Exception:
        pass


class Steps(list):
    """Drop-in for the steps list in the query pipeline: every append also
    streams a 'step' event. Status mutations on existing entries don't emit —
    the next step (or the final payload) implies the previous one finished."""

    def append(self, step) -> None:
        super().append(step)
        if isinstance(step, dict) and step.get("label"):
            emit("step", {"label": step["label"], "status": step.get("status", "running")})
