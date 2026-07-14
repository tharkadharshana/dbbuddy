"""
report_cache/ingest.py
========================
The heart of the cache (PLAN_03 Step 3): fetch a report for a period,
normalize, upsert facts + sync-state, with correct grain and freshness
status. Called only from background jobs (PLAN 04) — never inline in a
chat request (doc 09 Part 7: reports are 90s-class calls).

Grain (doc 09 C1/C2): scalar reports are always stored at DAILY grain, even
when a month is requested — the API already returns daily rows in one call
(SalesSummaryController's table_data is GROUP BY DATE). Dimensional reports
are stored at MONTHLY grain (one row per dimension member per month) — doc
09 C7's top-N cap keeps huge product lists bounded.

Transaction ownership: like report_cache/store.py, every function here takes
an already-open `conn` and does NOT commit/rollback/close it — the caller
owns the transaction boundary (see PLAN_03's own manual-verification snippet:
`ingest_period(conn, ...); conn.commit()`, commit is explicit and external).
On failure, the fetch happens BEFORE any DB writes, so a fetch/normalize
error never leaves partial fact rows — only a best-effort error row in
report_sync_state, then the original exception propagates to the caller
(a background job's own retry/backoff logic, not a user-facing request).
"""

from datetime import date
from typing import Optional

from logger import get_logger
from report_cache import tiers
from report_cache.client import ReportAPIClient
from report_cache.lookups import is_shop_allowed
from report_cache.normalize import normalize_daily_rows, normalize_dim_rows
from report_cache.periods import daterange_to_months, month_bounds, status_for
from report_cache.registry import REPORTS
from report_cache.store import set_sync_state, upsert_daily_fact, upsert_dim_fact

log = get_logger(__name__)

# doc 09 C7: cap dimensional facts per (tenant, report, shop, month) — top-N by
# the report's own "size" metric (see _primary_ranking_metric), the rest
# collapsed into one 'other' aggregate row so a huge product catalog can't
# blow up report_dim_fact row counts.
_DEFAULT_TOP_N = 200
_OTHER_DIM_KEY = "__other__"
_OTHER_DIM_NAME = "Other"


def _require_report(report_id: str, expected_kind: str):
    report = REPORTS.get(report_id)
    if report is None:
        raise ValueError(f"Unknown report_id: {report_id!r}")
    if report.kind != expected_kind:
        raise ValueError(f"{report_id} is {report.kind!r}, not {expected_kind!r}")
    return report


def _check_shop_authorized(tenant_id: str, shop_id: str) -> None:
    """Defense-in-depth (PLAN_00 §0.6): identity/shop_id must always be
    validated against the tenant's own shops before any report call, even
    though the real trust boundary is the answer layer (PLAN 05) translating
    a model's request into a shop_id in the first place."""
    if not is_shop_allowed(tenant_id, shop_id):
        raise ValueError(f"shop_id {shop_id!r} is not allowed for tenant {tenant_id!r}")


def _primary_ranking_metric(report) -> Optional[str]:
    """The metric key to rank dimensional rows by for the top-N cap. Uses the
    denominator of the report's ratio metric (e.g. sales_by_products' 'net_sale',
    sales_by_category's 'net_sales') — generic across dimensional reports
    without hardcoding per-report field names, which genuinely differ (see
    report_cache/registry.py)."""
    for metric in report.metrics:
        if metric.agg == "ratio" and metric.den:
            return metric.den
    return None


def _cap_top_n(dim_rows: list, report, top_n: int):
    """Returns (kept_rows, other_metrics_or_None). Only sum-additive metrics
    are folded into the 'other' bucket (doc 09 C3) — ratio/non_additive
    metrics can't be validly aggregated across dimension members."""
    rank_key = _primary_ranking_metric(report)
    if rank_key is None or len(dim_rows) <= top_n:
        return dim_rows, None

    ranked = sorted(dim_rows, key=lambda r: r["metrics"].get(rank_key, 0.0), reverse=True)
    kept, overflow = ranked[:top_n], ranked[top_n:]

    other_metrics: dict = {}
    for row in overflow:
        for metric in report.metrics:
            if metric.agg != "sum":
                continue
            other_metrics[metric.key] = other_metrics.get(metric.key, 0.0) + row["metrics"].get(metric.key, 0.0)

    return kept, other_metrics


def ingest_scalar_report(conn, tenant_id: str, report_id: str, token: str,
                          start: date, end: date, shop_id: str = "all") -> dict:
    """For scalar (daily) reports. Fetches [start,end] via fetch_report_all_pages,
    normalizes table_data to daily rows, upserts one report_daily_fact per
    (day, shop). Records report_sync_state per FULLY-covered calendar month
    touched (grain stored is 'day' — matches the report's own registry grain;
    `period` is the month key, since one ingestion call always fetches whole
    months' worth of days in a single API round-trip per doc 09 C1). A
    partially-covered boundary month (start/end not month-aligned) does NOT
    get a sync_state row — its days are still written to report_daily_fact,
    but the month can't be claimed as fully cached yet.

    Window enforcement (doc 09 Part 5): a range entirely older than the
    tenant's tier window is skipped (not fetched); a range straddling the
    window boundary is clipped to start at window_start.
    """
    report = _require_report(report_id, "scalar")
    _check_shop_authorized(tenant_id, shop_id)
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end})")

    today = date.today()
    win_start = tiers.window_start(tenant_id, today=today)

    if end < win_start:
        log.info("ingest_scalar_report: range entirely before tenant window — skipped",
                  tenant=tenant_id, report=report_id, start=start, end=end, window_start=win_start)
        return {"ingested": 0, "skipped_reason": "before_window"}
    effective_start = max(start, win_start)

    try:
        client = ReportAPIClient(token)
        raw = client.fetch_report_all_pages(
            report_id, start_date=effective_start.isoformat(), end_date=end.isoformat(), shop_id=shop_id,
        )
        daily_rows = normalize_daily_rows(report_id, raw.get("table_data", []))
    except Exception as exc:
        log.error("ingest_scalar_report: fetch/normalize failed", tenant=tenant_id,
                   report=report_id, start=effective_start, end=end, error=str(exc))
        _record_error(conn, tenant_id, report_id, shop_id, effective_start, "day", str(exc))
        raise

    count = 0
    for row in daily_rows:
        business_date = date.fromisoformat(row["business_date"])
        if business_date < effective_start or business_date > end:
            continue  # defensive clip — API should already respect the requested range
        upsert_daily_fact(conn, tenant_id, report_id, shop_id, business_date,
                           row["metrics"], status_for(business_date, today, "day"))
        count += 1

    for month in daterange_to_months(effective_start, end):
        first, last = month_bounds(month)
        if first < effective_start or last > end:
            continue  # partial month at the range boundary — don't claim full coverage
        set_sync_state(conn, tenant_id, report_id, shop_id, month, "day",
                        status_for(month, today, "month"))

    log.info("Ingested scalar report", tenant=tenant_id, report=report_id,
              shop=shop_id, days=count, start=effective_start, end=end)
    return {"ingested": count, "skipped_reason": None}


def ingest_dimensional_report(conn, tenant_id: str, report_id: str, token: str,
                               period_month: date, shop_id: str = "all",
                               top_n: int = _DEFAULT_TOP_N) -> dict:
    """For dimensional (monthly) reports. Fetches the whole month once,
    normalizes dim rows, upserts one report_dim_fact per (dim_key, shop) —
    capped to the top N by sales size with an 'other' rollup row for the rest
    (doc 09 C7)."""
    report = _require_report(report_id, "dimensional")
    _check_shop_authorized(tenant_id, shop_id)

    period_month = period_month.replace(day=1)
    today = date.today()
    win_start = tiers.window_start(tenant_id, today=today).replace(day=1)

    if period_month < win_start:
        log.info("ingest_dimensional_report: month is before tenant window — skipped",
                  tenant=tenant_id, report=report_id, period_month=period_month, window_start=win_start)
        return {"ingested": 0, "skipped_reason": "before_window"}

    first, last = month_bounds(period_month)
    try:
        client = ReportAPIClient(token)
        raw = client.fetch_report_all_pages(
            report_id, start_date=first.isoformat(), end_date=last.isoformat(), shop_id=shop_id,
        )
        dim_rows = normalize_dim_rows(report_id, raw.get("table_data", []))
    except Exception as exc:
        log.error("ingest_dimensional_report: fetch/normalize failed", tenant=tenant_id,
                   report=report_id, period_month=period_month, error=str(exc))
        _record_error(conn, tenant_id, report_id, shop_id, period_month, "month", str(exc))
        raise

    kept_rows, other_metrics = _cap_top_n(dim_rows, report, top_n)
    status = status_for(period_month, today, "month")

    count = 0
    for row in kept_rows:
        upsert_dim_fact(conn, tenant_id, report_id, shop_id, period_month, report.dim_type,
                         row["dim_key"], row["dim_name"], row["metrics"], status)
        count += 1
    if other_metrics:
        upsert_dim_fact(conn, tenant_id, report_id, shop_id, period_month, report.dim_type,
                         _OTHER_DIM_KEY, _OTHER_DIM_NAME, other_metrics, status)
        count += 1

    set_sync_state(conn, tenant_id, report_id, shop_id, period_month, "month", status)

    log.info("Ingested dimensional report", tenant=tenant_id, report=report_id,
              shop=shop_id, period_month=period_month, rows=count,
              capped=other_metrics is not None, total_dims=len(dim_rows))
    return {"ingested": count, "skipped_reason": None}


def ingest_period(conn, tenant_id: str, report_id: str, token: str,
                   period: date, shop_id: str = "all") -> dict:
    """Dispatcher: picks scalar-day vs dimensional-month by REPORTS[report_id].grain.
    For scalar reports, `period` is interpreted as a month -> ingests all its days
    in one API call."""
    report = REPORTS.get(report_id)
    if report is None:
        raise ValueError(f"Unknown report_id: {report_id!r}")

    if report.kind == "scalar":
        first, last = month_bounds(period)
        return ingest_scalar_report(conn, tenant_id, report_id, token, first, last, shop_id=shop_id)
    if report.kind == "dimensional":
        return ingest_dimensional_report(conn, tenant_id, report_id, token, period, shop_id=shop_id)
    raise ValueError(f"Unknown report kind: {report.kind!r}")


def _record_error(conn, tenant_id: str, report_id: str, shop_id: str, period: date,
                   grain: str, error: str) -> None:
    """Best-effort error record — swallows its own failure so a broken DB
    write never masks the original fetch exception the caller is about to see."""
    try:
        set_sync_state(conn, tenant_id, report_id, shop_id, period, grain, "error", error=error[:512])
    except Exception as exc:
        log.error("Failed to record ingestion error to sync_state", tenant=tenant_id,
                   report=report_id, error=str(exc))
