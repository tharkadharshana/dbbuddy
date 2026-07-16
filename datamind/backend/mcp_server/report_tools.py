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
from datetime import date, timedelta
from typing import Any, List, Optional

from fastmcp import FastMCP

from logger import get_logger
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


def _window_start(history_months: int) -> date:
    return date.today() - timedelta(days=(history_months or 3) * 30)


def _plan_limit_error(history_months: int) -> ValueError:
    """The refusal the model relays when a question needs data older than the
    plan window — always an upgrade prompt, never a technical limit message."""
    months = history_months or 3
    return ValueError(
        f"The user's current subscription includes the last {months} months of "
        f"data (from {_window_start(history_months).isoformat()}). Tell the user, "
        f"in a friendly tone: their current plan covers the last {months} months "
        "of insights, and to unlock insights beyond that they can upgrade to a "
        "higher subscription plan. If part of the asked range IS within the "
        f"{months}-month window, offer to answer for that part.")


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
            raise ValueError(f"Unknown report '{report_id}'. Call list_reports first.")
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if start < _window_start(ctx.history_months):
            raise _plan_limit_error(ctx.history_months)
        result = answer_metric_query(
            ctx.conn, rctx.tenant_id, report_id, start, end,
            shop_id=_resolve_shop(rctx, shop), cashier=cashier,
            metrics=metrics, token=rctx.token,
            number_format=rctx.number_format, top_n=top_n)
        _set_last_result(rctx, f"report:{report_id} {start_date}..{end_date}",
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
            raise ValueError(f"Unknown report '{report_id}'. Call list_reports first.")
        if not rctx.token:
            raise ValueError("Row detail needs a live POS session, which isn't "
                             "available right now — offer the cached totals instead.")
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        if start < _window_start(ctx.history_months):
            raise _plan_limit_error(ctx.history_months)
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
        rows = rows[:ctx.row_limit]
        _set_last_result(rctx, f"report-detail:{report_id} {start_date}..{end_date}",
                         rows, {})
        return rows

    return mcp


def report_system_prompt(rctx: ReportToolContext) -> str:
    """Extra system-prompt guidance when report tools are available."""
    shops = ", ".join(s["shop_name"] for s in rctx.shops) or "one shop"
    live = ("Live POS fetches are available for current/open periods."
            if rctx.token else
            "No live POS session right now — only cached past months are "
            "available; say so if asked about today/this month.")
    months = rctx.business.history_months or 3
    return (
        f"Today's date is {date.today().isoformat()}. "
        "PREFER the report tools (list_reports -> get_report_metrics) over SQL for "
        "any sales/revenue/refunds/taxes/products/categories/staff question — their "
        "numbers exactly match the merchant's POS dashboard, which raw SQL does not. "
        "Use SQL tools only for questions no report covers (e.g. specific record "
        "lookups). Never mix numbers from reports and SQL in one answer. "
        f"The merchant's shops are: {shops}. {live} "
        f"The user's subscription covers the last {months} months of data. If they "
        "ask about anything older, do not just say the data is unavailable — tell "
        f"them their current plan includes {months} months of insights and that "
        "upgrading to a higher subscription plan unlocks more history, then answer "
        "for the part of the range their plan covers if any. "
        "Never mention tools, reports, SQL, queries, databases, or caching to the "
        "user — you are their business analyst, not a query system.")
