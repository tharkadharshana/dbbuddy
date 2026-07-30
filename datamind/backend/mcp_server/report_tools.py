"""
mcp_server/report_tools.py — report-API MCP tools for SalesPlay tenants.

These are the model's PRIMARY analytics tools: every report in
report_cache.registry is a ready-made, known-correct semantic layer — the
model picks a report and fills in dates instead of writing blind SQL. The
generic get_schema/run_select_query tools remain registered as the FALLBACK
for long-tail questions no report covers (name lookups, unusual joins).

Answers are cache-first (closed months, additivity-correct aggregation in
report_cache.answer) with live report-API fetches — via the session token
stashed at widget-open — for open periods, shop/cashier filters and uncached
scopes, write-through caching what they fetch.

SECURITY: same pattern as business_tools — a fresh FastMCP per request, with
tenant_id/token baked into the closure. The model never sees or sets identity;
a `shop` it passes is resolved and authorized against the tenant's own shops.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, List, Optional

from fastmcp import FastMCP

from llm import strip_internal_fields
from logger import get_logger
from report_cache.answer import NO_SESSION_MSG as _NO_SESSION_MSG
from report_cache.answer import answer_metric_query
from report_cache.client import ReportAPIClient
from report_cache.registry import REPORTS

from .business_tools import ToolContext, build_business_mcp

log = get_logger(__name__)

_DETAIL_MAX_PAGES = 5


@dataclass
class ReportToolContext:
    business: ToolContext
    tenant_id: str
    token: Optional[str]            # stashed live-session aat, or None
    shops: List[dict]               # [{shop_id, shop_name}]
    number_format: Any = None       # tenant_profile.number_format (JSON str)


def load_report_context(conn, tenant_id: str, business: ToolContext) -> Optional["ReportToolContext"]:
    """Build a ReportToolContext from the synced tenant profile, or None if
    the tenant has no profile yet (report tools then simply aren't offered)."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT number_format FROM tenant_profile WHERE tenant_id = %s",
                   (tenant_id,))
    profile = cursor.fetchone()
    if not profile:
        return None
    cursor.execute(
        "SELECT shop_id, shop_name FROM tenant_shop WHERE tenant_id = %s "
        "AND is_enabled = 1 ORDER BY shop_name", (tenant_id,))
    shops = cursor.fetchall()
    from report_cache.profile import get_session_token
    return ReportToolContext(
        business=business, tenant_id=tenant_id,
        token=get_session_token(tenant_id), shops=shops,
        number_format=profile["number_format"])


def _resolve_shop(rctx: ReportToolContext, shop: Optional[str]) -> str:
    """Model-passed shop name/id -> authorized shop_id, or 'all'."""
    if not shop or shop.strip().lower() == "all":
        return "all"
    want = shop.strip().lower()
    by_id = {str(s["shop_id"]).lower(): s for s in rctx.shops}
    if want in by_id:
        return str(by_id[want]["shop_id"])
    exact = [s for s in rctx.shops if s["shop_name"].strip().lower() == want]
    if len(exact) == 1:
        return str(exact[0]["shop_id"])
    partial = [s for s in rctx.shops if want in s["shop_name"].lower()]
    if len(partial) == 1:
        return str(partial[0]["shop_id"])
    names = ", ".join(s["shop_name"] for s in rctx.shops) or "none"
    raise ValueError(
        f"No unique shop matches '{shop}'. This merchant's shops are: {names}. "
        "Ask the user which one they mean, or omit shop for all shops.")


def _rank_reports(query: str) -> list:
    q = (query or "").lower()
    words = {w for w in q.split() if len(w) > 2}
    scored = []
    for report in REPORTS.values():
        hay = " ".join([report.title, report.description, " ".join(report.answers)]).lower()
        score = sum(1 for w in words if w in hay)
        score += sum(2 for kw in report.answers if kw in q)
        if score:
            scored.append((score, report.id))
    scored.sort(reverse=True)
    ranked = [rid for _, rid in scored[:8]] or list(REPORTS)[:8]
    return [{"id": rid, "title": REPORTS[rid].title,
             "description": REPORTS[rid].description,
             "metrics": [m.key for m in REPORTS[rid].metrics]}
            for rid in ranked]


def _unknown_report_error(report_id: str) -> ValueError:
    """Actionable rejection for a hallucinated report_id (e.g. 'sales' instead of
    'sales_summary'): hand the model the closest VALID ids so it self-corrects in
    the next call instead of guessing again."""
    probe = (report_id or "").replace("_", " ")
    suggestions = [r["id"] for r in _rank_reports(probe)][:6] or list(REPORTS)[:6]
    return ValueError(
        f"Unknown report '{report_id}'. Use one of these exact report ids: "
        f"{', '.join(suggestions)}. Call list_reports to see all with descriptions.")


def _window_start(history_months: int) -> date:
    """First date the plan covers. Delegates to billing so the report path, the
    SQL path and cutoff_date can never drift apart again (they previously used
    three different computations, two of them `months * 30`)."""
    from billing import window_start
    return window_start(history_months)


def _plan_limit_error(history_months: int) -> ValueError:
    """Raised only when the ENTIRE requested range predates the plan window —
    a partial overlap is clamped instead (see _clamp_to_window). Plain fact; the
    model decides how to say it."""
    return ValueError(
        f"This merchant's subscription covers data from "
        f"{_window_start(history_months).isoformat()} onward ({history_months} "
        "months). The requested range is entirely before that, so there are no "
        "figures to return for it. A higher plan covers more history.")


def _clamp_to_window(history_months: int, start: date, end: date):
    """Apply the plan window by CLAMPING the range, not rejecting the call.

    Previously any range whose start fell outside the window raised, so a
    merchant on a 3-month plan asking about "April" when their window opened on
    April 20 got "no data" — even though April 20–30 was fully covered. The
    tool returned an error, so the model had no figures to offer and could only
    apologise. Now it gets the covered portion plus the clamp facts and explains
    the gap itself. Returns (effective_start, clamped)."""
    ws = _window_start(history_months)
    if end < ws:
        raise _plan_limit_error(history_months)
    return max(start, ws), start < ws


def _set_last_result(rctx: ReportToolContext, label: str, rows: list, metrics: dict):
    """Expose the report answer through the SAME slot business_tools uses, so
    the orchestrator's existing (sql, columns, data) recovery needs no change."""
    data = rows or ([metrics] if metrics else [])
    columns = list(data[0].keys()) if data else []
    rctx.business.last_result = {"sql": f"-- {label}", "columns": columns, "data": data}


def build_report_mcp(rctx: ReportToolContext) -> FastMCP:
    mcp = build_business_mcp(rctx.business)   # SQL fallback tools stay registered
    ctx = rctx.business

    @mcp.tool()
    def list_reports(query: str) -> list:
        """List the pre-built, dashboard-correct POS reports available for this
        merchant, ranked for the question. PREFER these over SQL for any
        sales/refunds/taxes/products/staff analytics question. Pick a report id
        and call get_report_metrics with it."""
        return _rank_reports(query)

    @mcp.tool()
    def get_report_metrics(report_id: str, start_date: str, end_date: str,
                           shop: Optional[str] = None,
                           cashier: Optional[str] = None,
                           metrics: Optional[list] = None,
                           top_n: Optional[int] = None) -> dict:
        """PRIMARY analytics tool: correct, dashboard-matching totals for a
        report over a date range (YYYY-MM-DD). `report_id` comes from
        list_reports. Optional: `shop` (name or id; omit = all shops),
        `cashier` (name), `metrics` to narrow which metric keys you need,
        `top_n` to limit rows for product/category style reports.
        Returns {metrics, rows, source} — 'source' says whether the numbers
        came from the monthly cache or a live POS fetch."""
        if report_id not in REPORTS:
            raise _unknown_report_error(report_id)
        requested, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        start, clamped = _clamp_to_window(ctx.history_months, requested, end)
        result = answer_metric_query(
            ctx.conn, rctx.tenant_id, report_id, start, end,
            shop_id=_resolve_shop(rctx, shop), cashier=cashier,
            metrics=metrics, token=rctx.token,
            number_format=rctx.number_format, top_n=top_n)
        if clamped:
            result["period"] = {
                "requested_start": requested.isoformat(),
                "effective_start": start.isoformat(),
                "clamped": True,
                "plan_months": ctx.history_months,
            }
        _set_last_result(rctx, f"report:{report_id} {start.isoformat()}..{end_date}",
                         result.get("rows"), result.get("metrics"))
        return result

    @mcp.tool()
    def get_report_detail(report_id: str, start_date: str, end_date: str,
                          shop: Optional[str] = None, search: Optional[str] = None) -> list:
        """Individual row detail for a report (e.g. the actual receipts/
        transactions) over a date range — for 'show me the ...' or 'list ...'
        asks. Live fetch, bounded to a few pages; use get_report_metrics for
        totals. Optional `search` filters rows server-side."""
        if report_id not in REPORTS:
            raise _unknown_report_error(report_id)
        if not rctx.token:
            raise ValueError(_NO_SESSION_MSG)
        requested, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        start, _clamped = _clamp_to_window(ctx.history_months, requested, end)
        client = ReportAPIClient(rctx.token)
        report = REPORTS[report_id]
        rows, page = [], 1
        while page <= _DETAIL_MAX_PAGES:
            params = {"start_date": start.isoformat(), "end_date": end.isoformat(),
                      "shop_id": _resolve_shop(rctx, shop), "page": page, "per_page": 100}
            if search:
                params["search"] = search
            payload = client.get(report.endpoint, params)
            data = payload.get("data") or {}
            rows.extend(data.get("table_data") or [])
            if not (payload.get("pagination") or {}).get("has_next_page"):
                break
            page += 1
        # Raw report-API rows carry internal POS fields (terminal/device keys,
        # surrogate ids) with no business meaning — strip before this reaches
        # the model's answer or any UI table.
        rows = strip_internal_fields(rows[:ctx.row_limit])
        _set_last_result(rctx, f"report-detail:{report_id} {start.isoformat()}..{end_date}",
                         rows, {})
        return rows

    return mcp


def report_system_prompt(rctx: ReportToolContext) -> str:
    """Extra system-prompt guidance when report tools are available."""
    shops = ", ".join(s["shop_name"] for s in rctx.shops) or "one shop"
    live = ("Live POS fetches are available for current/open periods."
            if rctx.token else
            "The merchant's POS connection has expired, so only fully closed "
            "months can be read right now. This is a connection problem, not "
            "missing data — never tell them their data does not exist.")
    months = rctx.business.history_months
    return (
        f"Today's date is {date.today().isoformat()}. "
        "PREFER the report tools (list_reports -> get_report_metrics) over SQL for "
        "any sales/revenue/refunds/taxes/products/categories/staff question — their "
        "numbers exactly match the merchant's POS dashboard, which raw SQL does not. "
        "Use SQL tools only for questions no report covers (e.g. specific record "
        "lookups). Never mix numbers from reports and SQL in one answer. "
        f"The merchant's shops are: {shops}. {live} "
        f"Their subscription covers data from {_window_start(months).isoformat()} "
        f"onward ({months} months). A range that starts before that is answered "
        "for the covered part automatically — when a result comes back with "
        "'clamped', state the range you actually covered and mention that a "
        "higher plan unlocks more history. Never just say the data is unavailable. "
        "Never mention tools, reports, SQL, queries, databases, or caching to the "
        "user — you are their business analyst, not a query system.")
