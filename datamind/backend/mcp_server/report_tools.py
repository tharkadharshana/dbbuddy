"""
mcp_server/report_tools.py
===========================
Report-cache MCP tools (PLAN 05 Step 3) + the tool-calling loop that drives
them. These are the model's PRIMARY analytics tools for SalesPlay tenants: the
8 registry reports are a ready-made, known-correct semantic layer (doc 07
Part 1) — the model picks a report and fills in dates instead of writing blind
SQL. The generic get_schema/run_select_query tools are still registered as a
FALLBACK for long-tail questions no report covers (doc 07 Part 3, plan Step 3).

Identity stays server-side (doc 08 §3.6): tenant_id / token / tier / allowed
shops live on the ReportToolContext closure — never model-visible parameters.
A `shop` name the model passes is resolved AND authorized against the tenant's
own shops before any fetch (report_cache.lookups.resolve_shop / is_shop_allowed).
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, List, Optional

from fastmcp import Client, FastMCP

from llm import fix_currency_symbol
from logger import get_logger
from report_cache import lookups
from report_cache.answer import answer_metric_query
from report_cache.client import ReportAPIClient
from report_cache.registry import REPORTS
from .business_tools import ToolContext, build_business_mcp
from .llm_tool_calling import call_with_tools

log = get_logger(__name__)

_DETAIL_MAX_PAGES = 5


class NoReportAnswer(Exception):
    """Raised when the report loop ends without any report tool producing a
    result — the caller (main.py) should fall back to the legacy pipeline."""


@dataclass
class ReportToolContext:
    """Per-request context for the report tools. `business` is the generic-tool
    ToolContext (the SQL fallback), reused so identity/scoping stay identical."""
    business: ToolContext
    tenant_id: str
    token: Optional[str]
    tier: str
    currency: str
    shops: List[dict]                          # [{shop_id, shop_name}]
    last_result: Optional[dict] = field(default=None)   # data table for the final answer


def _resolve_shop(rctx: ReportToolContext, shop: Optional[str]) -> str:
    """A shop name/id the model passed -> an authorized shop_id, or 'all'."""
    if not shop or shop.strip().lower() == "all":
        return "all"
    shop_id = lookups.resolve_shop(rctx.tenant_id, shop)
    if not shop_id or not lookups.is_shop_allowed(rctx.tenant_id, shop_id):
        raise ValueError(
            f"I couldn't find a shop matching '{shop}'. Your shops are: "
            + ", ".join(s["shop_name"] for s in rctx.shops) + ". Which one did you mean?"
        )
    return shop_id


def _rank_reports(query: str) -> List[dict]:
    q = (query or "").lower()
    scored = []
    for report in REPORTS.values():
        hay = " ".join([report.title, report.description, " ".join(report.answers)]).lower()
        score = sum(1 for w in set(q.split()) if len(w) > 2 and w in hay)
        score += sum(2 for kw in report.answers if kw in q)
        if score:
            scored.append((score, report))
    scored.sort(key=lambda t: t[0], reverse=True)
    ranked = [r for _, r in scored] or list(REPORTS.values())
    return [{"id": r.id, "title": r.title, "description": r.description,
             "metrics": [m.key for m in r.metrics]} for r in ranked[:5]]


def build_report_mcp(rctx: ReportToolContext) -> FastMCP:
    mcp = build_business_mcp(rctx.business)   # generic get_schema/get_sample_rows/run_select_query fallback

    @mcp.tool()
    def list_reports(query: str) -> list:
        """List the pre-built, known-correct reports available for this merchant,
        ranked for the question. PREFER these over writing SQL. Returns each
        report's id, title, description and metric keys — pick one and call
        get_report_metrics with it."""
        return _rank_reports(query)

    @mcp.tool()
    def get_report_metrics(report_id: str, start_date: str, end_date: str,
                           shop: Optional[str] = None, metrics: Optional[list] = None,
                           top_n: Optional[int] = None) -> dict:
        """PRIMARY analytics tool. Return correct, dashboard-matching metrics for a
        report over a date range (YYYY-MM-DD). `report_id` from list_reports.
        `shop` is an optional shop name (omit for all shops). `metrics` optionally
        narrows which metric keys to return. `top_n` limits rows for product/
        category reports (e.g. top 5). Numbers come from the report cache when
        the range is fully covered and closed, else a live report-API fetch —
        either way they match the merchant's POS dashboard."""
        if report_id not in REPORTS:
            raise ValueError(f"Unknown report '{report_id}'. Call list_reports first.")
        shop_id = _resolve_shop(rctx, shop)
        result = answer_metric_query(
            rctx.business.conn, rctx.tenant_id, report_id, metrics,
            date.fromisoformat(start_date), date.fromisoformat(end_date),
            shop_id, rctx.token, rctx.tier, top_n=top_n,
        )
        rctx.last_result = result
        if result.get("refusal"):
            return {"refusal": result["refusal"]}
        return {"metrics": result.get("summary") or result["data"],
                "rows": result["data"], "provenance": result["provenance"]}

    @mcp.tool()
    def get_report_detail(report_id: str, start_date: str, end_date: str,
                          shop: Optional[str] = None) -> list:
        """Return individual row detail (e.g. the transactions/receipts) for a
        report over a date range — for "show me the ..." asks. Live, bounded to a
        few pages. Use get_report_metrics for totals/aggregates instead."""
        if report_id not in REPORTS:
            raise ValueError(f"Unknown report '{report_id}'. Call list_reports first.")
        if not rctx.token:
            raise ValueError("Live detail is unavailable right now — try asking for the totals instead.")
        shop_id = _resolve_shop(rctx, shop)
        raw = ReportAPIClient(rctx.token).fetch_report_all_pages(
            report_id, start_date=start_date, end_date=end_date, shop_id=shop_id,
            max_pages=_DETAIL_MAX_PAGES,
        )
        rows = raw.get("table_data", [])
        rctx.last_result = {"report_id": report_id, "provenance": "from_live",
                            "source": "report_api_detail", "columns": list(rows[0].keys()) if rows else [],
                            "data": rows, "summary": None}
        return rows

    return mcp


def _system_prompt(rctx: ReportToolContext) -> str:
    from report_cache.prompts import build_persona_system_prompt
    today = date.today().isoformat()
    return (
        build_persona_system_prompt(None, rctx.currency) + " "
        + f"Today is {today}. When the user gives a relative period (last month, this quarter, "
        "past 6 weeks), compute explicit start_date and end_date as YYYY-MM-DD yourself. "
        "Prefer the report tools (list_reports, then get_report_metrics) over writing SQL — "
        "they return numbers that match the merchant's POS dashboard. Use run_select_query only "
        "for questions no report covers. Once you have the numbers, reply with a short, clear "
        "answer and stop calling tools."
    )


async def answer_report_question(question: str, rctx: ReportToolContext, llm: str,
                                 api_key: str, user_email: Optional[str],
                                 conversation_history: str = "", max_iterations: int = 5) -> dict:
    """Run the report-tool loop. Returns {answer, columns, data, provenance,
    source, refusal}. Raises NoReportAnswer if no report tool ever produced a
    result (caller falls back). Mirrors orchestrator.answer_business_question but
    keeps the model's OWN final narrative (report numbers are already correct —
    no separate Think-Mode regeneration needed) plus the last tool's data table."""
    mcp = build_report_mcp(rctx)
    messages = [
        {"role": "system", "content": _system_prompt(rctx)},
        {"role": "user", "content": (
            f"[Previous conversation]\n{conversation_history}\n\nQuestion: {question}"
            if conversation_history else question)},
    ]

    final_text = ""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        for _ in range(max_iterations):
            turn = call_with_tools(llm, messages, tools, api_key, user_email)
            if not turn.tool_calls:
                final_text = turn.text or final_text
                break
            messages.append({
                "role": "assistant", "content": turn.text,
                "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                               for tc in turn.tool_calls],
            })
            for tc in turn.tool_calls:
                res = await client.call_tool(tc.name, tc.arguments, raise_on_error=False)
                if res.is_error:
                    content = " ".join(getattr(c, "text", "") for c in (res.content or [])).strip() \
                        or "Tool call failed."
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                     "content": content, "result": None})
                else:
                    import json as _json
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name,
                                     "content": _json.dumps(res.data, default=str), "result": res.data})

    result = rctx.last_result
    if result is None:
        raise NoReportAnswer("No report tool produced a result.")
    if result.get("refusal"):
        return {"answer": result["refusal"], "columns": [], "data": [],
                "provenance": None, "source": "tier_refusal", "refusal": True}

    return {
        "answer": fix_currency_symbol(final_text, rctx.currency),
        "columns": result.get("columns", []),
        "data": result.get("data", []),
        "provenance": result.get("provenance"),
        "source": result.get("source"),
        "refusal": False,
    }
