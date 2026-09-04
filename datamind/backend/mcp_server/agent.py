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
import re
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
    "export_data": "Preparing your file",
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

# Emitted by the model on its own line when it turns something down because the
# plan does not include it, and stripped from the answer before the merchant
# sees it. The frontend turns it into an upgrade button, so a refusal offers a
# way forward instead of a dead end.
#
# A marker rather than matching the prose: the model words the refusal freshly
# every time ("available on a higher plan", "isn't included in your trial"), so
# any phrase list would both miss real refusals and fire on answers that merely
# discuss pricing.
UPGRADE_MARKER = "[[UPGRADE]]"

_UPGRADE_NOTE = (
    "\nWhen you turn something down because their plan does not include it, end "
    "your reply with " + UPGRADE_MARKER + " on its own line. It is removed "
    "before they see it and becomes an upgrade button. Use it ONLY for a plan "
    "refusal, never on an ordinary answer."
)

_NO_EXPORT_NOTE = (
    "\nYou cannot send this merchant files on their plan. If they ask to "
    "download or export the figures, or to be sent a spreadsheet, say plainly "
    "that file downloads are available on a higher plan."
)

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
    # Present only when the merchant asked for a file this turn.
    export: Optional[dict] = None
    # Last SQL the agent executed (None for knowledge-only answers).
    last_sql: Optional[str] = None
    # True when the answer was a plan refusal, so the UI can offer a way to
    # subscribe rather than leaving the merchant at a dead end.
    upgrade_offer: bool = False


class AgentFailed(Exception):
    """The loop could not produce an answer. Deliberately NOT a signal to fall
    back to a different architecture — main.py returns an honest transient
    error instead. Silently dropping to the one-shot query_to_sql guesser meant
    the same question was answered by two different products depending on
    whether a 429 happened to land, which is the direct cause of "sometimes
    right, sometimes wrong"."""


def build_system_prompt(currency: str, shops: str, window_start,
                        timezone: str = "", can_forecast: bool = True,
                        can_export: bool = True, extra: str = "") -> str:
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
    if not can_export:
        prompt += _NO_EXPORT_NOTE
    # Only worth asking for the marker when there is something to refuse.
    if not can_forecast or not can_export:
        prompt += _UPGRADE_NOTE
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


_EXPORT_FORMATS = ("excel", "csv", "chart", "document")

# A merchant's own POS issues their tax documents; this one is built from synced
# sales records that carry no per-line tax, no billing address and no
# registration numbers, so it must never present itself as one.
_TAX_INVOICE_RE = re.compile(r"tax\s*invoice", re.IGNORECASE)


def _percent_point_columns(columns: list) -> list:
    """Which of `columns` hold a FRACTION that a file should print as percentage
    points.

    report_cache/answer.py computes every ratio metric as num/den, so a 23.12%
    margin sits in the row as 0.2312. The chat is unaffected -- the model reads
    the fraction and writes "23.12%" itself -- but a file renderer has no such
    judgement, and printed "0.2312%" beside a chat that said 23.12%.

    Read from the registry rather than guessed from the column name: a value
    below 1 is equally consistent with a fraction and with a genuinely small
    percentage, and guessing between them is what produced the bug. A SQL
    column merely NAMED "..._pct" is left alone, because nothing guarantees it
    is a fraction.
    """
    try:
        from report_cache.registry import REPORTS
    except Exception:
        return []
    keys = {m.key for r in REPORTS.values() for m in (r.metrics or ())
            if m.agg == "ratio" and m.label.rstrip().endswith("%")}
    return [c for c in (columns or []) if c in keys]


def _money_columns(columns: list) -> list:
    """Which columns are monetary, by main.py's own rule.

    Imported lazily: main imports this module, so a module-level import would
    be circular. Falls back to an empty list, which just means the frontend
    uses its own heuristic — the same path older payloads already take.
    """
    try:
        from main import _is_money_column
        return [c for c in (columns or []) if _is_money_column(c)]
    except Exception:
        return []


def _clean_document_spec(spec: dict, columns: list) -> dict:
    """Validate the model's document LAYOUT against the columns actually queried.

    The model chooses the shape of the document — its title, which fields make
    up the header, which columns are line items. It does not supply any values:
    every figure is read from the rows by the renderer. This is the whole safety
    property of the feature. A model retyping monetary figures into a document
    is how a wrong total reaches a merchant's customer, so the spec carries
    column NAMES and the data stays where the database put it.

    A name that is not in `columns` is dropped rather than rendered blank — a
    printed page with 'undefined' on it is worse than one field short.
    """
    spec = spec or {}
    known = set(columns or [])

    header = {label: col for label, col in (spec.get("header_fields") or {}).items()
              if col in known}
    lines = [c for c in (spec.get("line_columns") or []) if c in known]
    totals = [c for c in (spec.get("total_columns") or []) if c in lines]

    asked = [c for c in (spec.get("line_columns") or [])]
    if not lines:
        raise ValueError(
            "None of those columns are in the figures you pulled. Use the "
            "column names from the result you already have, or run the query "
            "that has them first.")
    # Dropping a stray column is fine; dropping MOST of the layout means the
    # model is describing a different result than the one loaded -- usually a
    # figure from an earlier report that is no longer the current result. That
    # once rendered as a one-column page under a full summary's title, which
    # looks like a working document rather than a failure. Make it correctable
    # instead of silent.
    if len(lines) < len(asked) / 2:
        missing = ", ".join(c for c in asked if c not in known)
        raise ValueError(
            f"These columns are not in the figures currently loaded: {missing}. "
            f"Available: {', '.join(sorted(known))}. Re-run the query that has "
            "the columns you want in this same reply, then call export_data.")

    title = str(spec.get("title") or "Sales Document").strip()
    if _TAX_INVOICE_RE.search(title):
        title = "Sales Document"

    return {
        "title": title,
        "subtitle": str(spec.get("subtitle") or "").strip() or None,
        "header_fields": header,
        "line_columns": lines,
        "total_columns": totals,
        "notes": str(spec.get("notes") or "").strip() or None,
    }


def _register_export_tool(mcp, ctx: ToolContext, entitlements: dict) -> None:
    """Plan-gated file export. Registered ONLY when the plan includes it, so an
    unentitled merchant's model has no such tool and cannot be talked into one.

    Deliberately separate from _register_ml_tools: export needs no report
    context, so it must not inherit that function's `rctx is None` bail-out —
    a SQL-only tenant can export just as well.

    Nothing is written to disk and nothing is stored. The tool only marks the
    rows the model has already pulled as requested for download; the browser
    builds the actual file from the response and the merchant keeps it. There
    is no re-download later — asking again re-runs the question."""
    if not entitlements.get("download_export"):
        return

    @mcp.tool()
    def export_data(format: str = "excel", document: dict = None) -> dict:
        """Give the merchant a downloadable file of the figures you just pulled.
        `format` is "excel" for a spreadsheet, "csv" for a plain data file,
        "chart" for a picture of the chart, or "document" for a printable page
        they can save as a PDF. Call this ONLY when they ask to download,
        export, save, print or be sent the figures as a file — never on your own
        initiative. Returns confirmation only; the file reaches them on its own,
        so just tell them it is ready.

        IMPORTANT — run the query again in this same reply before calling this,
        even when you already showed those figures a moment ago. Only the query
        you run right now can be exported; earlier answers are not still loaded.
        So: run the query, then call export_data, then say it is ready.

        For "document", describe the LAYOUT you want in `document` — you choose
        the shape, the page is filled in from the figures you already pulled, so
        never type any amounts, names or dates yourself:
          title          - what the document is called, e.g. "Sales Document"
          subtitle       - optional line under the title
          header_fields  - {label: column} pairs for the top block, e.g.
                           {"Receipt": "receipt_number", "Date": "created_at"}
          line_columns   - the columns that make up the item table, in order
          total_columns  - which of those to total at the bottom (they are
                           added up for you)
          notes          - an optional closing line
        Every value must be a column name from the figures you pulled. Use it
        for an invoice-style page, a statement, a summary — whatever they asked
        for. It is built from their sales records, so it is not a tax invoice
        and must not be called one; their POS issues those."""
        last = ctx.last_result or {}
        rows = last.get("data") or []
        if not rows:
            # The usual cause is a follow-up: the merchant asks to download
            # what they were just shown, but those rows belonged to the previous
            # request's context and are gone. Say so precisely — a vague error
            # sends the model exploring the schema again and it burns the
            # iteration budget before it ever writes an answer.
            raise ValueError(
                "Nothing is loaded to export in this reply. If they are asking "
                "for figures you gave earlier, run that query again now — then "
                "call export_data straight after it.")
        fmt = (format or "excel").strip().lower()
        if fmt not in _EXPORT_FORMATS:
            fmt = "excel"
        # Same ceiling the SQL flow applies to what it sends (main.py row_limit),
        # so an export can never be larger than an on-screen result.
        capped = rows[:ctx.row_limit] if ctx.row_limit else rows
        columns = last.get("columns") or list(capped[0].keys())
        # Validate BEFORE marking the export, so a rejected layout leaves no
        # half-formed request behind for the response to pick up.
        spec = _clean_document_spec(document, columns) if fmt == "document" else None
        ctx.export_request = {
            "format": fmt,
            "columns": columns,
            "data": capped,
            # Same money/count decision the on-screen table uses, so a printed
            # figure formats identically to the one in the chat.
            "money_cols": _money_columns(columns),
            # Fractions the renderer must scale to percentage points. Named
            # explicitly so no file has to infer a unit from a column name.
            "percent_cols": _percent_point_columns(columns),
        }
        if spec:
            ctx.export_request["document"] = spec
        # The rows deliberately do NOT go back into the model's context — they
        # are already in ctx, and re-serialising them into a tool message would
        # cost the whole result set in tokens for no gain.
        return {"ready": True, "format": fmt, "row_count": len(capped)}


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
    _register_export_tool(mcp, ctx, entitlements or {})
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
    # Strip the marker BEFORE on_text: that callback streams the answer, so a
    # marker left in here is a marker the merchant watches appear. It must not
    # reach the saved message or the answer sanitiser either.
    upgrade = UPGRADE_MARKER in final_text
    if upgrade:
        final_text = final_text.replace(UPGRADE_MARKER, "").strip()

    if on_text:
        on_text(final_text)

    last = ctx.last_result or {}
    return AgentResult(text=final_text, columns=last.get("columns") or [],
                       data=last.get("data") or [], tool_calls=called,
                       sources=sources, export=ctx.export_request,
                       last_sql=last.get("sql") or None,
                       upgrade_offer=upgrade)


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
        can_export=bool(entitlements.get("download_export")),
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
            ctx.export_request = None
    raise AgentFailed(str(last_error))
