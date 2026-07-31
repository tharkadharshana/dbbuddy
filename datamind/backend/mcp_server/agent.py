"""
mcp_server/agent.py — the pure agent loop (docs/16_Pure_Agent_Architecture.md).

This is the whole answering system. One model, one conversation, tools
attached. The user's question goes to the model untouched; the model decides
whether it needs the merchant's POS data, its own knowledge, or both, calls as
many tools as it wants, and writes the final answer itself.

What is deliberately NOT here, and why:

  - No question classifier and no route table. Every open defect in the July
    benchmark lived in that layer and none in the tools or aggregation.
  - No follow-up rewriter. The model resolves its own follow-ups against real
    message history; a separate rewriter never saw the tool results, so it
    could not resolve "the second one" against a table it never read.
  - No narrator pass. orchestrator.py throws the model's own answer away and
    regenerates it from a bare (columns, data) table with a prompt that never
    saw the question's context — the single largest reason answers read as
    unintelligent. Here the model that did the thinking writes the answer.
  - No forced table or chart. The model writes a markdown table when one helps
    (the UI renders GFM tables already) or a ```chart block when a picture
    helps, exactly as it would in any chat client. Most questions are answered
    better in a sentence.
  - No arithmetic by the model. Tools return computed values: report metrics
    are summed in Python with ratios recomputed from summed num/den, and
    aggregate SQL is computed by MySQL. Nothing here hands the model 50 rows
    and asks it to total them.

The guards all sit inside the tools, where the model cannot route around them
because it never executes anything itself: AST SQL safety and tenant scoping
(safety.py), the plan's history window (clamped, not refused), and plan
feature entitlement — which is enforced by simply not registering a tool the
merchant's plan does not include. A Starter merchant's model cannot see
`forecast`, so there is no prompt rule to jailbreak.
"""

import os
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

from fastmcp import Client

from logger import get_logger
from progress import emit as _progress_emit

from .business_tools import ToolContext, build_business_mcp
from .llm_tool_calling import call_with_tools

log = get_logger(__name__)

_MAX_ITERATIONS = int(os.getenv("MCP_MAX_TOOL_ITERATIONS", "8"))
_MAX_ATTEMPTS = int(os.getenv("AGENT_MAX_ATTEMPTS", "2"))

# User-facing progress labels per tool (SSE 'step' events). Deliberately vague
# and business-flavoured: the merchant should feel an analyst working, never
# see the mechanics (tool names, report ids, SQL, date params).
_TOOL_LABELS = {
    "get_schema": "Looking at your business data",
    "get_sample_rows": "Looking closer at your data",
    "run_select_query": "Analyzing your data",
    "get_date_range": "Checking your data coverage",
    "list_reports": "Thinking about the best way to answer",
    "get_report_metrics": "Analyzing your business performance",
    "get_report_detail": "Gathering the details",
    "forecast": "Projecting what's ahead",
    "detect_anomalies": "Looking for unusual activity",
}

_SYSTEM_PROMPT = """You are a senior business and marketing analyst working for a retail merchant.
You have direct access to their point-of-sale data through your tools.
Answer their questions the way a sharp, experienced analyst would.

Today is {today}{tz}. Amounts are in {currency}. Their shops are: {shops}.
Their subscription covers data from {window_start} onward.

Decide for yourself what each question needs — their data, your own knowledge,
or a mix. Many good questions about pricing, marketing and strategy need no
data at all; answer those directly. Use as many tool calls as you need. If a
result looks wrong or empty for a question that should have data, correct your
approach and try again — you do not need permission to retry.

Never guess a table or column name. Before you write any SQL, inspect the
schema so you are working from names you have actually seen, and check a few
sample rows before filtering on a column's value so you match the real stored
format (for example 'COMPLETED' vs 'completed'). A pre-built report is better
still where one covers the question — its figures match the merchant's own
dashboard, which hand-written SQL does not.

For totals, counts, averages and comparisons, ask the tools for the computed
figure — write aggregate SQL and let the database do the arithmetic, or use a
report's metrics. Never add up rows yourself.

If a query comes back with no rows, that may simply be the truth — but confirm
it before you say so, because reporting "you have no data" to a merchant who
does is far worse than taking one more tool call to check.

When a table or chart would genuinely help the merchant see something, include
one; when it wouldn't, don't. For a table, write a normal markdown table. For a
chart, emit a fenced ```chart block containing JSON like
{{"kind": "bar", "title": "Sales by month", "rows": [{{"month": "April", "sales": 120}}]}}
where kind is line, bar or pie. Skip both for single-figure answers.

Talk about the business, never the plumbing: never mention tables, columns,
SQL, queries, tools, reports-as-systems, caches or databases. Say "your
receipts", not the name of anything internal.
"""

_NO_FORECAST_NOTE = (
    "\nYou have no forecasting tool on this merchant's plan. If they ask for a "
    "forecast or projection, say plainly that forecasting is available on a "
    "higher plan — do not try to eyeball a trend instead."
)


@dataclass
class AgentResult:
    text: str
    columns: List[str] = field(default_factory=list)
    data: list = field(default_factory=list)
    tool_calls: List[str] = field(default_factory=list)
    sources: set = field(default_factory=set)
    attempts: int = 1


class AgentFailed(Exception):
    """The loop could not produce an answer. Deliberately NOT a signal to fall
    back to a different architecture — main.py returns an honest transient
    error instead. Silently dropping to the one-shot query_to_sql guesser meant
    the same question was answered by two different products depending on
    whether a 429 happened to land, which is the direct cause of "sometimes
    right, sometimes wrong"."""


def build_system_prompt(currency: str, shops: str, window_start,
                        timezone: str = "", can_forecast: bool = True,
                        extra: str = "") -> str:
    prompt = _SYSTEM_PROMPT.format(
        today=date.today().isoformat(),
        tz=f" ({timezone})" if timezone and timezone != "UTC" else "",
        currency=currency or "$",
        shops=shops or "their shop",
        window_start=window_start.isoformat() if hasattr(window_start, "isoformat")
        else window_start,
    )
    if not can_forecast:
        prompt += _NO_FORECAST_NOTE
    if extra:
        prompt += "\n" + extra.strip()
    return prompt


def _register_ml_tools(mcp, rctx, entitlements: dict) -> None:
    """Plan-gated ML tools. Registered ONLY when the plan includes them — the
    model is never told it can't forecast, it simply has no such tool."""
    if rctx is None:
        return
    from report_cache.answer import answer_metric_query
    from report_cache.registry import REPORTS

    def _daily_series(metric: str, months: int):
        """Per-day (date, value) pairs from the daily sales report, over the
        plan window. sales_summary is the one grain='day' report."""
        from billing import window_start as _ws
        start, end = _ws(months), date.today()
        result = answer_metric_query(
            rctx.business.conn, rctx.tenant_id, "sales_summary", start, end,
            token=rctx.token, number_format=rctx.number_format)
        rows = result.get("rows") or []
        series = [(r["date"], r[metric]) for r in rows
                  if r.get("date") and r.get(metric) is not None]
        if len(series) < 5:
            raise ValueError(
                "There isn't enough daily sales history yet to project from — "
                "at least a couple of weeks of sales are needed.")
        return series

    if entitlements.get("forecast"):
        @mcp.tool()
        def forecast(days_ahead: int = 30, metric: str = "gross_sales") -> dict:
            """Project future daily sales from this merchant's own history.
            `days_ahead` is how far forward to project (default 30). Returns the
            projected values with a confidence range. Use this for "what will my
            sales be", "will next month be better", planning and target
            questions."""
            from analytics import run_forecast
            months = rctx.business.history_months
            return run_forecast(_daily_series(metric, months),
                                periods=max(1, min(int(days_ahead), 365)))

    if entitlements.get("anomaly_detection"):
        @mcp.tool()
        def detect_anomalies(metric: str = "gross_sales") -> dict:
            """Find unusual days in this merchant's sales history — spikes and
            drops that stand out from their normal pattern. Use this for "was
            anything strange", "any unusual days", "what happened on"."""
            from analytics import run_anomaly_detection
            months = rctx.business.history_months
            return run_anomaly_detection(_daily_series(metric, months), has_date=True)

    _ = REPORTS  # registry import keeps report ids validated at build time


def build_agent_mcp(ctx: ToolContext, report_ctx=None, entitlements: dict = None):
    """The tool surface for one request. Report tools when the tenant has a
    synced profile, SQL tools always (the long-tail fallback), ML tools only
    when the plan includes them."""
    if report_ctx is not None:
        from .report_tools import build_report_mcp
        mcp = build_report_mcp(report_ctx)
    else:
        mcp = build_business_mcp(ctx)
    _register_ml_tools(mcp, report_ctx, entitlements or {})
    return mcp


async def _run_once(question: str, mcp, system_prompt: str, history: list,
                    llm: str, api_key: str, user_email: Optional[str],
                    ctx: ToolContext, on_text=None) -> AgentResult:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": question})

    called: List[str] = []
    sources: set = set()
    final_text = ""

    async with Client(mcp) as client:
        tools = await client.list_tools()

        for _ in range(_MAX_ITERATIONS):
            turn = call_with_tools(llm, messages, tools, api_key, user_email,
                                   max_tokens=1600)
            if not turn.tool_calls:
                # The model's own words. This IS the answer — nothing
                # regenerates it, nothing rewrites it.
                final_text = (turn.text or "").strip()
                break

            messages.append({
                "role": "assistant",
                "content": turn.text,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in turn.tool_calls
                ],
            })
            for tc in turn.tool_calls:
                called.append(tc.name)
                _progress_emit("step", {
                    "label": _TOOL_LABELS.get(tc.name, "Working on your answer"),
                    "status": "running"})
                result = await client.call_tool(tc.name, tc.arguments,
                                                raise_on_error=False)
                if result.is_error:
                    # Surfaced to the model verbatim so it can self-correct —
                    # that is the entire point of the loop. A tool error is not
                    # a request failure.
                    text = " ".join(getattr(c, "text", "")
                                    for c in (result.content or [])).strip()
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                        "content": text or "Tool call failed.", "result": None})
                else:
                    if isinstance(result.data, dict) and result.data.get("source"):
                        sources.add(result.data["source"])
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                        "content": _json(result.data), "result": result.data})

    if not final_text:
        raise AgentFailed(
            f"The model stopped without an answer after {len(called)} tool calls.")
    if on_text:
        on_text(final_text)

    last = ctx.last_result or {}
    return AgentResult(text=final_text, columns=last.get("columns") or [],
                       data=last.get("data") or [], tool_calls=called,
                       sources=sources)


def _json(value) -> str:
    import json
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


async def answer(question: str, ctx: ToolContext, llm: str, api_key: str,
                 user_email: Optional[str], report_ctx=None,
                 entitlements: dict = None, history: list = None,
                 currency: str = "$", shops: str = "", window_start=None,
                 timezone: str = "", extra_prompt: str = "",
                 on_text=None) -> AgentResult:
    """Run the agent loop and return the model's own answer.

    Retries the SAME architecture on failure (a fresh key comes from llm.py's
    pool each attempt) and then raises. It never falls through to a different,
    weaker answering path — see AgentFailed.
    """
    entitlements = entitlements or {}
    mcp = build_agent_mcp(ctx, report_ctx, entitlements)
    system_prompt = build_system_prompt(
        currency=currency, shops=shops, window_start=window_start,
        timezone=timezone, can_forecast=bool(entitlements.get("forecast")),
        extra=extra_prompt)

    last_error = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = await _run_once(question, mcp, system_prompt, history, llm,
                                     api_key, user_email, ctx, on_text=on_text)
            result.attempts = attempt
            return result
        except Exception as exc:
            last_error = exc
            log.warning("Agent attempt failed", attempt=attempt,
                        user=user_email, error=str(exc))
            ctx.last_result = None
    raise AgentFailed(str(last_error))
