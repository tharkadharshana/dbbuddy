"""
report_cache/answer.py
=======================
The read-through answer resolver (PLAN 05 Step 2) — the orchestration the MCP
report tools call. Turns a (report, metrics, range, shop) request into a
dashboard-correct answer, cache-first, live-on-miss with write-through.

The doc-09 rules it enforces, in order:
  - Part 5: tier window — refuse (with upsell) a range older than the tenant's
    plan allows, before any fetch.
  - C6/C4/C3 (via aggregate.needs_live_fetch): calculated KPIs never come from
    raw sp_* tables; open periods and non-additive metrics and cache misses all
    force a live exact-range fetch from the report API.
  - The fast path (sum cached daily facts) is used ONLY for daily_cacheable
    reports (sales_summary today) over a covered, closed, additive range —
    everything else is a live exact-range summary fetch, which is always
    dashboard-correct (the summary block IS the number the POS report shows).

Token note (PLAN 02/04 open item): a live fetch needs the v2.0 report-API
token. In the chat path only the stored v1.0 token is available, which 401s —
answer_metric_query lets that raise so the caller (main.py) falls back to the
existing SQL pipeline. Cache-hit answers need no token and always work.
"""

from datetime import date
from typing import List, Optional

from logger import get_logger
from report_cache import aggregate, tiers
from report_cache.client import ReportAPIClient
from report_cache.normalize import normalize_dim_rows, normalize_summary
from report_cache.periods import daterange_to_months
from report_cache.read import coverage as read_coverage
from report_cache.read import get_daily_facts, get_dim_facts
from report_cache.registry import REPORTS

log = get_logger(__name__)

_TIER_UPSELL = {
    "basic": "Your current plan includes the last 3 months of history. Upgrade to Growth (12 months) or Pro (full history) to ask about older periods.",
    "standard": "Your current plan includes the last 12 months of history. Upgrade to Pro for full history to ask about older periods.",
}


def _require_report(report_id: str):
    report = REPORTS.get(report_id)
    if report is None:
        raise ValueError(f"Unknown report_id: {report_id!r}")
    return report


def answer_metric_query(conn, tenant_id: str, report_id: str, metrics: Optional[List[str]],
                        start: date, end: date, shop_id: str, token: Optional[str],
                        tier: str, top_n: Optional[int] = None) -> dict:
    """Resolve one metric/report question. Returns a result dict with keys
    {report_id, provenance, source, columns, data, summary, start, end,
    shop_id} — or {refusal: <message>} if the range is outside the tier window."""
    report = _require_report(report_id)
    if start > end:
        start, end = end, start

    # ── Part 5: tier window enforcement (refuse + upsell) ────────────────────
    win_start = tiers.window_start(tenant_id)
    if start < win_start:
        log.info("answer_metric_query: range before tier window — refusing",
                 tenant=tenant_id, report=report_id, start=start, window_start=win_start)
        return {"refusal": _TIER_UPSELL.get(tier, _TIER_UPSELL["basic"]),
                "window_start": win_start.isoformat()}

    if report.kind == "scalar":
        return _answer_scalar(conn, tenant_id, report, metrics, start, end, shop_id, token)
    if report.kind == "dimensional":
        return _answer_dim(conn, tenant_id, report, start, end, shop_id, token, top_n)
    raise ValueError(f"Unknown report kind: {report.kind!r}")


# ── scalar ──────────────────────────────────────────────────────────────────

def _answer_scalar(conn, tenant_id, report, metrics, start, end, shop_id, token) -> dict:
    # Coverage is only meaningful for daily-cacheable reports; others always go live.
    cov = read_coverage(conn, tenant_id, report.id, start, end, shop_id=shop_id) \
        if report.daily_cacheable else None
    if not aggregate.needs_live_fetch(report.id, metrics, cov):
        facts = get_daily_facts(conn, tenant_id, report.id, start, end, shop_id=shop_id)
        agg = aggregate.aggregate_scalar(facts, report.id, metrics)
        return _scalar_result(report, agg["metrics"], start, end, shop_id,
                              provenance="from_cache", source="cache_daily_sum")

    # live: fetch the exact range once; the summary block IS the dashboard number
    metric_values = _live_scalar_summary(report, token, start, end, shop_id)
    if metrics:
        metric_values = {k: v for k, v in metric_values.items() if k in set(metrics)}
    _warm_cache_async(tenant_id, report, start, end, shop_id, token)
    return _scalar_result(report, metric_values, start, end, shop_id,
                          provenance="from_live", source="report_api_range")


def _live_scalar_summary(report, token, start, end, shop_id) -> dict:
    if not token:
        raise ValueError(f"No usable report API token — cannot live-fetch {report.id}")
    raw = ReportAPIClient(token).fetch_report_all_pages(
        report.id, start_date=start.isoformat(), end_date=end.isoformat(), shop_id=shop_id,
    )
    return normalize_summary(report.id, raw.get("summary", {}))


def _scalar_result(report, metric_values, start, end, shop_id, provenance, source) -> dict:
    return {
        "report_id": report.id,
        "provenance": provenance,
        "source": source,
        "columns": list(metric_values.keys()),
        "data": [dict(metric_values)] if metric_values else [],
        "summary": metric_values,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "shop_id": shop_id,
    }


# ── dimensional ───────────────────────────────────────────────────────────────

def _answer_dim(conn, tenant_id, report, start, end, shop_id, token, top_n) -> dict:
    months = daterange_to_months(start, end)
    cached, all_closed = _read_cached_dim(conn, tenant_id, report.id, months, shop_id)

    if cached and all_closed:
        rows = aggregate.aggregate_dim(cached, report.id, top_n=top_n)
        return _dim_result(report, rows, start, end, shop_id,
                           provenance="from_cache", source="cache_dim")

    # live: the dimensional API returns per-member rows already aggregated over
    # the requested range — normalize then rank/cap.
    if not token:
        raise ValueError(f"No usable report API token — cannot live-fetch {report.id}")
    raw = ReportAPIClient(token).fetch_report_all_pages(
        report.id, start_date=start.isoformat(), end_date=end.isoformat(), shop_id=shop_id,
    )
    dim_rows = normalize_dim_rows(report.id, raw.get("table_data", []))
    rows = aggregate.aggregate_dim(dim_rows, report.id, top_n=top_n)
    return _dim_result(report, rows, start, end, shop_id,
                       provenance="from_live", source="report_api_range")


def _read_cached_dim(conn, tenant_id, report_id, months, shop_id):
    """Return (facts, all_closed): every month's cached dim facts concatenated,
    and whether every month is present AND none is still 'open'. A missing month
    or an open month means the cache can't answer the range."""
    facts = []
    all_closed = True
    for month in months:
        month_facts = get_dim_facts(conn, tenant_id, report_id, month, shop_id=shop_id)
        if not month_facts:
            return [], False
        if any(f.get("status") == "open" for f in month_facts):
            all_closed = False
        facts.extend(month_facts)
    return facts, all_closed


def _dim_result(report, rows, start, end, shop_id, provenance, source) -> dict:
    data = [{"name": r["dim_name"], **r["metrics"]} for r in rows]
    columns = ["name"] + (list(rows[0]["metrics"].keys()) if rows else [])
    return {
        "report_id": report.id,
        "provenance": provenance,
        "source": source,
        "columns": columns,
        "data": data,
        "summary": None,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "shop_id": shop_id,
    }


def _warm_cache_async(tenant_id, report, start, end, shop_id, token) -> None:
    """Best-effort: enqueue a background backfill so the next identical question
    hits the cache (only worth it for daily_cacheable reports). Never raises —
    a live answer was already produced."""
    if not report.daily_cacheable:
        return
    try:
        from report_cache.jobs.enqueue import request_backfill
        request_backfill(tenant_id, report.id, start, end, shop_id=shop_id, token=token)
    except Exception as exc:
        log.debug("cache warm enqueue skipped", tenant=tenant_id, report=report.id, error=str(exc))
