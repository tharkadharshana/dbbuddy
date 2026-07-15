"""
observability.py
=================
Lightweight tracing + metrics for the report-cache/AI paths (PLAN 08 Step 3,
doc 06 Part 4.7). Two goals, both cheap and always-on:

  1. Structured trace events — every LLM call and every cache decision is logged
     with the fields a trace needs (operation, model, tokens, latency, tenant,
     provenance, POS-API ms). A log aggregator or Langfuse can ingest these; they
     are useful on their own without any external service.
  2. In-process counters — cache-hit rate and POS-API call count per tenant, for
     a cheap `/metrics`-style snapshot and cost monitoring.

Langfuse export is OPTIONAL and defensive: enabled only when LANGFUSE_* env is
set AND the package imports; every call to it is wrapped so a version/API
mismatch can never break a request. Billing is unchanged — this is additive.
"""

import os
import threading
from collections import defaultdict

from logger import get_logger

log = get_logger(__name__)

_LANGFUSE_CONFIGURED = bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))
log.info("Observability flag", langfuse_configured=_LANGFUSE_CONFIGURED)

_lock = threading.Lock()
_counters = {
    "cache_hit": defaultdict(int),     # tenant -> count
    "cache_live": defaultdict(int),    # tenant -> count (cache miss / open / non-additive)
    "pos_api_calls": defaultdict(int), # tenant -> count
    "llm_calls": defaultdict(int),     # operation -> count
    "llm_tokens": defaultdict(int),    # operation -> total tokens
}

_langfuse = None


def _client():
    """Lazily build the Langfuse client, or None. Never raises."""
    global _langfuse
    if not _LANGFUSE_CONFIGURED:
        return None
    if _langfuse is None:
        try:
            from langfuse import Langfuse
            _langfuse = Langfuse()          # reads LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST
        except Exception as exc:
            log.warning("Langfuse init failed — structured logs only", error=str(exc))
            _langfuse = False
    return _langfuse or None


def _export(name: str, payload: dict) -> None:
    """Best-effort Langfuse export. Version-tolerant + fully swallowed — a
    version/API mismatch or a network hiccup can never break a request."""
    client = _client()
    if not client:
        return
    try:
        if hasattr(client, "trace"):          # langfuse v2 top-level API
            client.trace(name=name, metadata=payload)
        elif hasattr(client, "create_event"):  # newer variants
            client.create_event(name=name, metadata=payload)
    except Exception:
        pass


def record_llm_call(operation: str, *, model: str = "", tokens: int = 0,
                    latency_ms: int = 0, tenant: str = None, user: str = None) -> None:
    """Trace one LLM call (called next to billing in llm.call_llm)."""
    with _lock:
        _counters["llm_calls"][operation] += 1
        _counters["llm_tokens"][operation] += int(tokens or 0)
    log.info("llm_call", operation=operation, model=model, tokens=tokens,
             latency_ms=latency_ms, tenant=tenant or "-", user=user or "-")
    _export("llm_call", {"operation": operation, "model": model, "tokens": tokens,
                         "latency_ms": latency_ms, "tenant": tenant, "user": user})


def record_cache_event(provenance: str, *, tenant: str = None, report: str = None,
                       source: str = None, api_ms: int = None) -> None:
    """Trace one cache decision: provenance 'from_cache' | 'from_live'. A live
    answer also implies a POS API call (counted for cost monitoring)."""
    with _lock:
        if provenance == "from_cache":
            _counters["cache_hit"][tenant or "-"] += 1
        elif provenance == "from_live":
            _counters["cache_live"][tenant or "-"] += 1
            _counters["pos_api_calls"][tenant or "-"] += 1
    log.info("cache_event", provenance=provenance, tenant=tenant or "-",
             report=report or "-", source=source or "-", api_ms=api_ms)
    _export("cache_event", {"provenance": provenance, "tenant": tenant,
                            "report": report, "source": source, "api_ms": api_ms})


def metrics_snapshot() -> dict:
    """Point-in-time counters. `cache_hit_rate` is hits / (hits + live)."""
    with _lock:
        hits = sum(_counters["cache_hit"].values())
        live = sum(_counters["cache_live"].values())
        total = hits + live
        return {
            "cache_hits": hits,
            "cache_live": live,
            "cache_hit_rate": round(hits / total, 4) if total else None,
            "pos_api_calls": dict(_counters["pos_api_calls"]),
            "llm_calls": dict(_counters["llm_calls"]),
            "llm_tokens": dict(_counters["llm_tokens"]),
        }


def reset_metrics() -> None:
    """Test helper — clear all counters."""
    with _lock:
        for c in _counters.values():
            c.clear()
