"""AI_FLOW=agent — the pure agent loop (docs/16_Pure_Agent_Architecture.md).

The behaviours worth locking down:
  - the model's OWN final text is the answer (orchestrator.py discards it and
    regenerates from a bare table — the single largest reason answers read as
    unintelligent)
  - a tool error is handed back to the model to self-correct, not raised
  - plan entitlement is enforced by NOT REGISTERING the tool, so there is no
    prompt rule to jailbreak
  - failure raises rather than silently falling through to a weaker answering
    path, which is what made the same question return different answers
"""
import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from mcp_server import agent
from mcp_server.agent import AgentFailed, build_agent_mcp, build_system_prompt
from mcp_server.business_tools import ToolContext
from mcp_server.llm_tool_calling import ToolCallRequest, ToolCallTurn


def _ctx():
    return ToolContext(conn=None, schemas={"sp_receipts": []}, fkeys=[],
                       tenant_id="t1", row_limit=100, history_months=3,
                       set_query_timeout=lambda c: None)


@dataclass
class _FakeReportCtx:
    business: object
    tenant_id: str = "t1"
    token: str = "tok"
    shops: tuple = ()
    number_format: object = None


def _tools_of(mcp):
    from fastmcp import Client

    async def _go():
        async with Client(mcp) as c:
            return {t.name for t in await c.list_tools()}
    return asyncio.run(_go())


# ── the system prompt ─────────────────────────────────────────────────────────

def test_prompt_states_the_plan_window_and_no_capability_list():
    p = build_system_prompt(currency="LKR", shops="Main",
                            window_start=date(2026, 4, 20))
    assert "2026-04-20" in p
    assert "LKR" in p
    # No scope rules, no deflection rules, no capability list — those existed to
    # compensate for classification, and classification is gone.
    for banned in ("out of scope", "I cannot", "capabilities", "classify"):
        assert banned.lower() not in p.lower()


def test_unentitled_plan_gets_an_honest_upsell_line_not_an_eyeballed_trend():
    assert "higher plan" in build_system_prompt(
        currency="$", shops="", window_start=date(2026, 4, 20),
        can_forecast=False)
    assert "higher plan" not in build_system_prompt(
        currency="$", shops="", window_start=date(2026, 4, 20),
        can_forecast=True)


# ── entitlement by tool registration ──────────────────────────────────────────

def test_forecast_tool_is_absent_for_an_unentitled_plan():
    ctx = _ctx()
    mcp = build_agent_mcp(ctx, _FakeReportCtx(business=ctx),
                          {"forecast": False, "anomaly_detection": False})
    names = _tools_of(mcp)
    assert "forecast" not in names
    assert "detect_anomalies" not in names
    assert "get_report_metrics" in names        # analytics still fully available


def test_forecast_tool_is_present_for_an_entitled_plan():
    ctx = _ctx()
    mcp = build_agent_mcp(ctx, _FakeReportCtx(business=ctx),
                          {"forecast": True, "anomaly_detection": True})
    names = _tools_of(mcp)
    assert "forecast" in names
    assert "detect_anomalies" in names


# ── the loop ──────────────────────────────────────────────────────────────────

def _run(ctx, monkeypatch, turns):
    """Drive the loop with a scripted sequence of model turns."""
    seq = iter(turns)
    seen = {"messages": None}

    def _fake(llm, messages, tools, api_key, user_email, **kw):
        seen["messages"] = list(messages)
        nxt = next(seq)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(agent, "call_with_tools", _fake)
    result = asyncio.run(agent.answer(
        "how were sales?", ctx, "openai", "k", "u@e.com",
        report_ctx=None, entitlements={}, history=[],
        currency="$", shops="Main", window_start=date(2026, 4, 20)))
    return result, seen


def test_the_models_own_text_is_the_answer(monkeypatch):
    result, _ = _run(_ctx(), monkeypatch, [
        ToolCallTurn(text="Sales rose 12% — driven by weekends.", tool_calls=[]),
    ])
    assert result.text == "Sales rose 12% — driven by weekends."


def test_history_is_sent_as_real_messages(monkeypatch):
    ctx = _ctx()
    seq = iter([ToolCallTurn(text="done", tool_calls=[])])
    seen = {}

    def _fake(llm, messages, tools, api_key, user_email, **kw):
        seen["messages"] = list(messages)
        return next(seq)

    monkeypatch.setattr(agent, "call_with_tools", _fake)
    asyncio.run(agent.answer(
        "and last month?", ctx, "openai", "k", "u@e.com",
        history=[{"role": "user", "content": "how were sales?"},
                 {"role": "assistant", "content": "Up 12%."}],
        currency="$", shops="", window_start=date(2026, 4, 20)))
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    # No rewriter rephrased it — the model sees the follow-up verbatim.
    assert seen["messages"][-1]["content"] == "and last month?"


def test_a_tool_error_is_returned_to_the_model_to_self_correct(monkeypatch):
    ctx = _ctx()
    result, seen = _run(ctx, monkeypatch, [
        ToolCallTurn(text=None, tool_calls=[
            ToolCallRequest(id="1", name="get_sample_rows",
                            arguments={"table": "not_a_table"})]),
        ToolCallTurn(text="Recovered and answered.", tool_calls=[]),
    ])
    assert result.text == "Recovered and answered."
    tool_msgs = [m for m in seen["messages"] if m["role"] == "tool"]
    assert tool_msgs and "Unknown table" in tool_msgs[0]["content"]


def test_failure_raises_instead_of_degrading_to_another_path(monkeypatch):
    with pytest.raises(AgentFailed):
        _run(_ctx(), monkeypatch, [
            RuntimeError("provider 429"),
            RuntimeError("provider 429"),
        ])


def test_a_transient_failure_is_retried_on_the_same_architecture(monkeypatch):
    result, _ = _run(_ctx(), monkeypatch, [
        RuntimeError("provider 429"),
        ToolCallTurn(text="Answered on the retry.", tool_calls=[]),
    ])
    assert result.text == "Answered on the retry."
    assert result.attempts == 2


def test_an_empty_final_turn_is_a_failure_not_a_blank_answer(monkeypatch):
    with pytest.raises(AgentFailed):
        _run(_ctx(), monkeypatch, [
            ToolCallTurn(text="", tool_calls=[]),
            ToolCallTurn(text="   ", tool_calls=[]),
        ])
