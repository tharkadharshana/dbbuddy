"""
tests/test_insight.py — PLAN 06 provenance guard + grounded insight synthesis.

The critical property: the generated advice must not cite figures that weren't in
the fact pack. `unsupported_numbers` is the pure check; `generate_insight` is
tested with a mock LLM so no network/model is needed.
"""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.insights import insight as insight_mod
from report_cache.insights import provenance as prov


# ── provenance (pure) ─────────────────────────────────────────────────────────

def test_extract_and_unsupported_numbers():
    allowed = {400.0, 12.5, 1234.56}
    # every figure grounded (12.5% and $1,234.56 formatting variants)
    assert prov.unsupported_numbers("Sales were 400, margin 12.5%, total $1,234.56", allowed) == []
    # 999 is not in the pack → flagged
    assert prov.unsupported_numbers("We also made 999 somewhere", allowed) == [999.0]


def test_small_integers_not_flagged():
    # dates / 'top 5' / horizons shouldn't be treated as fabricated KPIs
    assert prov.unsupported_numbers("Top 5 products over the last 30 days on day 7", set()) == []


def test_collect_numbers_walks_pack():
    pack = {"months": [{"value": 600.0, "mom_pct": 100.0}], "note": "avg 42.5"}
    nums = prov.collect_numbers(pack)
    assert {600.0, 100.0, 42.5} <= nums


# ── generate_insight (mock LLM) ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    insight_mod._pack_cache.clear()
    yield
    insight_mod._pack_cache.clear()


def _pack(monkeypatch, facts, numbers):
    monkeypatch.setattr(insight_mod, "_build_insight_pack",
                        lambda *a, **k: {"facts": facts, "numbers": set(numbers),
                                         "table_columns": ["month", "value", "mom_pct"],
                                         "table_data": [{"month": "2026-06", "value": 600.0, "mom_pct": 100.0}]})


def test_generate_insight_cites_only_pack_numbers(monkeypatch):
    _pack(monkeypatch, ["June net sales = 600 (100% MoM)"], {600.0, 100.0})
    grounded = "Your data shows June net sales of 600, up 100% MoM. I'd suggest keeping that momentum."
    monkeypatch.setattr(insight_mod, "call_llm", lambda *a, **k: grounded)
    monkeypatch.setattr(insight_mod, "fix_currency_symbol", lambda t, c: t)

    out = insight_mod.generate_insight(
        conn=None, tenant_id="t1", question="how am I doing?", token=None, tier="basic",
        currency="$", tenant_profile=None, llm="openai", api_key="k", user_email="u@x.com",
    )
    assert out["source"] == "insight"
    assert out["data"]                                 # growth table passed through
    assert prov.unsupported_numbers(out["answer"], {600.0, 100.0}) == []   # no fabrication


def test_generate_insight_empty_pack_is_graceful(monkeypatch):
    _pack(monkeypatch, [], set())
    called = {"llm": False}
    monkeypatch.setattr(insight_mod, "call_llm",
                        lambda *a, **k: called.__setitem__("llm", True) or "should not run")
    out = insight_mod.generate_insight(
        conn=None, tenant_id="t1", question="suggestions?", token=None, tier="basic",
        currency="$", tenant_profile=None, llm="openai", api_key="k", user_email="u@x.com",
    )
    assert called["llm"] is False                      # no LLM call on empty pack (no fabrication risk)
    assert "enough sales history" in out["answer"]
