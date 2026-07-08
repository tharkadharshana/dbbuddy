"""
mcp_server/orchestrator.py
===========================
The MCP Client side: drives the tool-calling loop described in
docs/04_MCP_Architecture_And_Implementation_Guide.md Part 4.6 and
docs/05_MCP_Worked_Example_And_Comparison.md — give the model the 4
business-data tools, let it call them (possibly several times, self-
correcting on an empty/wrong result), and recover the final successful
query's columns/data once it stops calling tools.

No network hop: `build_business_mcp(ctx)` builds a fresh FastMCP instance for
this request only, and this module talks to it via FastMCP's in-memory
Client transport (see business_tools.py's module docstring for the full
security rationale for this choice over an HTTP mount).

The model's own final natural-language answer is intentionally discarded —
main.py's existing Think Mode step already generates narrative from the
final data/columns, unchanged. This loop's only job is to arrive at a
correct `sql`/`columns`/`data`, replacing query_to_sql's one blind guess.
"""

import json
import os
from typing import List, Optional, Tuple

from fastmcp import Client

from .business_tools import ToolContext, build_business_mcp
from .llm_tool_calling import call_with_tools


class NoQueryExecuted(Exception):
    """Raised when the tool-calling loop ends without the model ever
    successfully running a query — the caller should fall back to the
    legacy one-shot query_to_sql path rather than return no data."""


def _to_json_text(value) -> str:
    try:
        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _build_system_prompt(tenant_id: Optional[str], currency: str,
                          extra_hints: str, shop_timezone: str) -> str:
    parts = [
        "You are an expert data analyst answering a question about a merchant's own "
        "business data, using tools instead of writing one blind SQL query. "
        "Call get_schema first if you don't already know the tables/columns. "
        "Call get_sample_rows on a table before filtering on a column's value, to see "
        "the real format/casing actually stored (e.g. 'COMPLETED' vs 'completed') — do "
        "not guess. Call run_select_query to run a SELECT; if the result is empty or "
        "looks wrong for a question that should have data, call it again with a "
        "corrected query before answering — you do not need to ask permission to retry. "
        "Tenant scoping and the plan's history-window limit are applied automatically to "
        "every run_select_query call — do not add your own tenant filter. "
        "Once you have a result you're confident answers the question, reply with a short "
        "final answer and stop calling tools.",
        f"The user's currency is '{currency}' — use it for any monetary amount you mention.",
    ]
    if tenant_id:
        try:
            from providers.salesplay.canonical_metrics import SALESPLAY_LLM_RULES
            parts.append(SALESPLAY_LLM_RULES.format(timezone=shop_timezone or "UTC"))
        except Exception:
            pass
        parts.append(
            "Never SELECT a column named exactly 'id' or ending in '_id' "
            "(e.g. customer_id, product_id), and never select tenant_id or synced_at — "
            "these are internal database keys with no business meaning to the user."
        )
    if extra_hints:
        parts.append(extra_hints.strip())
    return " ".join(parts)


async def answer_business_question(
    question: str,
    ctx: ToolContext,
    llm: str,
    api_key: str,
    user_email: Optional[str],
    conversation_history: str = "",
    extra_hints: str = "",
    currency: str = "$",
    shop_timezone: str = "UTC",
    max_iterations: Optional[int] = None,
) -> Tuple[str, List[str], list]:
    """Run the tool-calling loop and return (sql, columns, data) from the last
    successful run_select_query call. Raises NoQueryExecuted if the model
    never ran one — callers should fall back to the legacy one-shot path."""
    if max_iterations is None:
        max_iterations = int(os.getenv("MCP_MAX_TOOL_ITERATIONS", "5"))

    mcp = build_business_mcp(ctx)
    system_prompt = _build_system_prompt(ctx.tenant_id, currency, extra_hints, shop_timezone)

    user_content = question
    if conversation_history:
        user_content = (
            f"[Previous conversation — use this to understand follow-up questions]\n"
            f"{conversation_history}\n\nQuestion: {question}"
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    async with Client(mcp) as client:
        tools = await client.list_tools()
        for _ in range(max_iterations):
            turn = call_with_tools(llm, messages, tools, api_key, user_email)
            if not turn.tool_calls:
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
                result = await client.call_tool(tc.name, tc.arguments, raise_on_error=False)
                if result.is_error:
                    error_text = " ".join(
                        getattr(c, "text", "") for c in (result.content or [])
                    ).strip() or "Tool call failed."
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                        "content": error_text, "result": None,
                    })
                else:
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "name": tc.name,
                        "content": _to_json_text(result.data),
                        "result": result.data,
                    })

    if ctx.last_result is None:
        raise NoQueryExecuted("The model never ran a successful query.")
    return ctx.last_result["sql"], ctx.last_result["columns"], ctx.last_result["data"]
