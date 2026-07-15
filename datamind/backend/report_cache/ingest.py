"""
report_cache/ingest.py — fetch closed calendar months of a report from the
SalesPlay report API and cache them in report_fact / report_dim_fact.

Rules:
  - Only fully CLOSED months are cached (status 'final'). The current month
    mutates (refunds/voids hit past receipts) and is always answered live.
  - Everything is idempotent: already-final months are skipped, upserts
    replace on re-run.
  - Backfill depth comes from the user's plan (billing.get_plan_history_limit)
    — basic 3 months, growth 12, pro effectively unlimited (capped here).

ponytail: only shop_id='all' is cached; per-shop questions fetch live (step 4).
Add shop-scoped cache rows if per-shop asks turn out to dominate.
"""

import json
import os
import threading
import time
from calendar import monthrange
from datetime import date, datetime

from logger import get_logger
from pool import get_internal_conn

from .client import ReportAPIClient
from .normalize import (normalize_daily_rows, normalize_dim_rows,
                        normalize_metrics, separators)
from .registry import CORE_REPORTS, REPORTS

log = get_logger(__name__)

_MAX_PAGES = int(os.getenv("REPORT_API_MAX_PAGE_CAP", "50"))
_CALL_GAP = float(os.getenv("REPORT_CACHE_MIN_CALL_INTERVAL", "1.0"))
_BACKFILL_MONTHS_CAP = int(os.getenv("REPORT_CACHE_BACKFILL_MONTHS_CAP", "24"))


# ── periods ──────────────────────────────────────────────────────────────────

def month_start(ym: str) -> date:
    y, m = ym.split("-")
    return date(int(y), int(m), 1)


def month_end(ym: str) -> date:
    d = month_start(ym)
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def closed_months(n: int, today: date = None) -> list:
    """Last n fully closed months as 'YYYY-MM', most recent first."""
    today = today or date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return out


# ── fetch + store one month ──────────────────────────────────────────────────

def _fetch_pages(client: ReportAPIClient, report, ym: str, per_page: int) -> list:
    """All table_data rows for the month (page-capped). Returns [] on none."""
    rows, page = [], 1
    while page <= _MAX_PAGES:
        payload = client.get(report.endpoint, {
            "start_date": month_start(ym).isoformat(),
            "end_date": month_end(ym).isoformat(),
            "shop_id": "all", "page": page, "per_page": per_page,
        })
        data = payload.get("data") or {}
        rows.extend(data.get("table_data") or [])
        if not (payload.get("pagination") or {}).get("has_next_page"):
            break
        page += 1
        time.sleep(_CALL_GAP)
    return rows


def _fetch_summary(client: ReportAPIClient, report, ym: str) -> dict:
    payload = client.get(report.endpoint, {
        "start_date": month_start(ym).isoformat(),
        "end_date": month_end(ym).isoformat(),
        "shop_id": "all", "page": 1, "per_page": 1,
    })
    return (payload.get("data") or {}).get("summary") or {}


def is_month_cached(conn, tenant_id: str, report_id: str, ym: str,
                    shop_id: str = "all") -> bool:
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM report_sync_state WHERE tenant_id=%s AND report_id=%s "
        "AND shop_id=%s AND month=%s AND status='final'",
        (tenant_id, report_id, shop_id, month_start(ym)),
    )
    return cursor.fetchone() is not None


def ingest_month(conn, tenant_id: str, report_id: str, ym: str, token: str,
                 number_format=None) -> str:
    """Cache one closed month of one report. Returns 'cached'|'skipped'.
    Caller owns the connection; this commits on success."""
    report = REPORTS[report_id]
    today = date.today()
    if month_end(ym) >= today:
        raise ValueError(f"Month {ym} is not closed yet — current periods are answered live.")
    if is_month_cached(conn, tenant_id, report_id, ym):
        return "skipped"

    dec, thou = separators(number_format)
    client = ReportAPIClient(token)
    cursor = conn.cursor()
    now = datetime.utcnow()
    first = month_start(ym)

    if report.kind == "dimensional":
        rows = normalize_dim_rows(report, _fetch_pages(client, report, ym, 100), dec, thou)
        cursor.execute(
            "DELETE FROM report_dim_fact WHERE tenant_id=%s AND report_id=%s "
            "AND shop_id='all' AND month=%s", (tenant_id, report_id, first))
        if rows:
            cursor.executemany(
                "INSERT INTO report_dim_fact (tenant_id, report_id, shop_id, month, "
                "dim_key, dim_label, metrics, fetched_at) VALUES (%s,%s,'all',%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE dim_label=VALUES(dim_label), "
                "metrics=VALUES(metrics), fetched_at=VALUES(fetched_at)",
                [(tenant_id, report_id, first, k, lbl, json.dumps(m), now)
                 for k, lbl, m in rows])
    elif report.grain == "day":
        days = normalize_daily_rows(report, _fetch_pages(client, report, ym, 100), dec, thou)
        if days:
            cursor.executemany(
                "INSERT INTO report_fact (tenant_id, report_id, shop_id, grain, "
                "period_start, metrics, fetched_at) VALUES (%s,%s,'all','day',%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE metrics=VALUES(metrics), fetched_at=VALUES(fetched_at)",
                [(tenant_id, report_id, d, json.dumps(m), now) for d, m in days])
    else:
        metrics = normalize_metrics(report, _fetch_summary(client, report, ym), dec, thou)
        cursor.execute(
            "INSERT INTO report_fact (tenant_id, report_id, shop_id, grain, "
            "period_start, metrics, fetched_at) VALUES (%s,%s,'all','month',%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE metrics=VALUES(metrics), fetched_at=VALUES(fetched_at)",
            (tenant_id, report_id, first, json.dumps(metrics), now))

    cursor.execute(
        "INSERT INTO report_sync_state (tenant_id, report_id, shop_id, month, "
        "status, fetched_at) VALUES (%s,%s,'all',%s,'final',%s) "
        "ON DUPLICATE KEY UPDATE status='final', fetched_at=VALUES(fetched_at)",
        (tenant_id, report_id, first, now))
    conn.commit()
    return "cached"


# ── onboarding backfill (runs on a daemon thread, never a request thread) ────

_inflight: set = set()
_inflight_lock = threading.Lock()


def backfill(tenant_id: str, token: str, months: int) -> dict:
    """Cache the core reports for the tenant's plan window. Idempotent —
    cached months are skipped, so the widget-open trigger is cheap after the
    first run. Returns {cached, skipped, failed} counts."""
    months = min(int(months or 3), _BACKFILL_MONTHS_CAP)
    stats = {"cached": 0, "skipped": 0, "failed": 0}
    conn = get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT number_format FROM tenant_profile WHERE tenant_id=%s",
                       (tenant_id,))
        row = cursor.fetchone()
        nf = row["number_format"] if row else None
        for ym in closed_months(months):
            for report_id in CORE_REPORTS:
                try:
                    result = ingest_month(conn, tenant_id, report_id, ym, token, nf)
                    stats[result] += 1
                    if result == "cached":
                        time.sleep(_CALL_GAP)
                except Exception as e:
                    stats["failed"] += 1
                    log.warning("Backfill: month failed", tenant=tenant_id,
                                report=report_id, month=ym, error=str(e))
    finally:
        conn.close()
    log.info("Backfill finished", tenant=tenant_id, **stats)
    return stats


def start_backfill_async(tenant_id: str, token: str, months: int) -> bool:
    """Kick backfill on a daemon thread; at most one per tenant per process."""
    with _inflight_lock:
        if tenant_id in _inflight:
            return False
        _inflight.add(tenant_id)

    def _run():
        try:
            backfill(tenant_id, token, months)
        except Exception as e:
            log.error("Backfill crashed", tenant=tenant_id, error=str(e))
        finally:
            with _inflight_lock:
                _inflight.discard(tenant_id)

    threading.Thread(target=_run, name=f"report-backfill-{tenant_id}", daemon=True).start()
    return True
