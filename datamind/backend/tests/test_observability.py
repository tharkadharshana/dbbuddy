"""
tests/test_observability.py — PLAN 08 Step 3 metrics/counters.

The Langfuse export is optional; here we verify the always-on parts: counters,
cache-hit-rate math, and that recording never raises when Langfuse is absent.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import observability as obs


def setup_function():
    obs.reset_metrics()


def test_cache_event_counters_and_hit_rate():
    obs.record_cache_event("from_cache", tenant="t1", report="sales_summary")
    obs.record_cache_event("from_cache", tenant="t1", report="sales_summary")
    obs.record_cache_event("from_live", tenant="t1", report="receipts", api_ms=1200)
    snap = obs.metrics_snapshot()
    assert snap["cache_hits"] == 2
    assert snap["cache_live"] == 1
    assert snap["cache_hit_rate"] == round(2 / 3, 4)
    assert snap["pos_api_calls"]["t1"] == 1   # a live answer implies one POS call


def test_llm_call_counters():
    obs.record_llm_call("route", model="gpt", tokens=50)
    obs.record_llm_call("route", model="gpt", tokens=30)
    obs.record_llm_call("persona", model="gpt", tokens=100)
    snap = obs.metrics_snapshot()
    assert snap["llm_calls"] == {"route": 2, "persona": 1}
    assert snap["llm_tokens"]["route"] == 80


def test_hit_rate_none_when_empty():
    assert obs.metrics_snapshot()["cache_hit_rate"] is None


def test_recording_never_raises_without_langfuse():
    # No LANGFUSE_* env in the test env → export is a no-op, must not raise.
    obs.record_cache_event("from_live", tenant="t2")
    obs.record_llm_call("insight", tokens=1)
