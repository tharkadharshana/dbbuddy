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
# Ops safety valve only — UNSET by default. It used to default to 24, which
# silently truncated every paid plan: Pro's window is 200 months, so
# report_tools happily accepted a month-40 query, the cache had nothing, and
# every single ask for that month fell through to a live fetch, forever. Depth
# belongs to plan_history_limits, not to a constant in this file.
# `or "0"` handles the var being present-but-empty (REPORT_CACHE_BACKFILL_MONTHS_CAP=
# in .env), which os.getenv returns as "" rather than the default — int("") raises
# and would crash the process at import.
_BACKFILL_MONTHS_CAP = int(os.getenv("REPORT_CACHE_BACKFILL_MONTHS_CAP", "").strip() or "0") or None
# Trailing closed months re-fetched on EVERY backfill run to catch late
# refunds/voids that mutate an already-closed month (C4 re-finalization).
_REFINALIZE_MONTHS = int(os.getenv("REPORT_CACHE_REFINALIZE_MONTHS", "2"))

# Deep re-finalization: any cached month whose figures are older than this is
# re-fetched too, however far back it sits in the plan window.
#
# _REFINALIZE_MONTHS=2 alone means a refund posted in July against an APRIL
# receipt is never picked up — April's cached total stays permanently too high
# and permanently disagrees with what the merchant sees in SalesPlay. Deriving
# staleness from report_sync_state.fetched_at needs no new state, self-heals,
# and naturally spreads the work: a month re-fetched yesterday is skipped.
_DEEP_REFINALIZE_DAYS = int(os.getenv("REPORT_CACHE_DEEP_REFINALIZE_DAYS", "7"))


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


def stale_months(conn, tenant_id: str, report_id: str, ym_list: list,
                 max_age_days: int = None) -> set:
    """Cached months whose figures were last fetched more than `max_age_days`
    ago. These get force-refetched so refunds/voids posted against an
    already-closed month eventually correct it."""
    max_age_days = _DEEP_REFINALIZE_DAYS if max_age_days is None else max_age_days
    if not ym_list or max_age_days <= 0:
        return set()
    starts = [month_start(ym) for ym in ym_list]
    cursor = conn.cursor()
    cursor.execute(
        "SELECT month FROM report_sync_state WHERE tenant_id=%s AND report_id=%s "
        "AND shop_id='all' AND status='final' "
        f"AND month IN ({','.join(['%s'] * len(starts))}) "
        "AND fetched_at < DATE_SUB(NOW(), INTERVAL %s DAY)",
        (tenant_id, report_id, *starts, max_age_days),
    )
    return {f"{r[0].year:04d}-{r[0].month:02d}" for r in cursor.fetchall()}


def ingest_month(conn, tenant_id: str, report_id: str, ym: str, token: str,
                 number_format=None, force: bool = False) -> str:
    """Cache one closed month of one report. Returns 'cached'|'skipped'.
    Caller owns the connection; this commits on success.
    force=True re-fetches an already-final month (trailing re-finalization to
    catch late refunds/voids)."""
    report = REPORTS[report_id]
    today = date.today()
    if month_end(ym) >= today:
        raise ValueError(f"Month {ym} is not closed yet — current periods are answered live.")
    if not force and is_month_cached(conn, tenant_id, report_id, ym):
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

# In-flight dedupe keys. Deliberately FINE-GRAINED: a backfill holds
# ("backfill", tenant) and a month-warm holds (tenant, report_id, ym).
#
# This used to be keyed on tenant_id alone, which meant that for the several
# minutes an onboarding backfill was running (it sleeps _CALL_GAP between every
# month × every core report), EVERY warm_months_async call from the answer path
# returned False and was dropped — no log, no retry, never queued. Two reports
# warming concurrently collided for the same reason. Combined with answer.py
# answering live and relying on the warm to populate the cache for next time,
# a month requested during a backfill could stay uncached indefinitely while
# every ask for it went live — and went live-and-failed once the POS token
# expired. Every drop is logged now.
_inflight: set = set()
_inflight_lock = threading.Lock()


def _claim(keys: list) -> list:
    """Atomically claim the keys not already in flight. Returns the claimed
    subset; logs the rest as drops."""
    with _inflight_lock:
        claimed = [k for k in keys if k not in _inflight]
        _inflight.update(claimed)
    dropped = [k for k in keys if k not in claimed]
    if dropped:
        log.info("Report cache: skipped already-in-flight work", keys=str(dropped))
    return claimed


def _release(keys) -> None:
    with _inflight_lock:
        for k in keys:
            _inflight.discard(k)


def backfill(tenant_id: str, token: str, months: int) -> dict:
    """Cache the core reports for the tenant's plan window. Idempotent —
    cached months are skipped, so the widget-open trigger is cheap after the
    first run. Returns {cached, skipped, failed} counts."""
    months = int(months)
    if _BACKFILL_MONTHS_CAP:
        months = min(months, _BACKFILL_MONTHS_CAP)
    stats = {"cached": 0, "skipped": 0, "failed": 0}
    # The most-recent closed months are re-fetched even if already final, to
    # catch late refunds/voids that mutate a closed month (C4 re-finalization).
    window = closed_months(months)
    trailing = set(closed_months(min(_REFINALIZE_MONTHS, months)))
    conn = get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT number_format FROM tenant_profile WHERE tenant_id=%s",
                       (tenant_id,))
        row = cursor.fetchone()
        nf = row["number_format"] if row else None
        for ym in window:
            for report_id in CORE_REPORTS:
                try:
                    # Deep pass: anything cached longer ago than
                    # _DEEP_REFINALIZE_DAYS is refreshed too, so a refund posted
                    # against a months-old receipt eventually corrects it.
                    stale = stale_months(conn, tenant_id, report_id, [ym])
                    result = ingest_month(conn, tenant_id, report_id, ym, token, nf,
                                          force=(ym in trailing or ym in stale))
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
    """Kick backfill on a daemon thread; at most one per tenant per process.
    Does NOT block month-warms for this tenant (see _inflight)."""
    key = ("backfill", tenant_id)
    if not _claim([key]):
        return False

    def _run():
        try:
            backfill(tenant_id, token, months)
        except Exception as e:
            log.error("Backfill crashed", tenant=tenant_id, error=str(e))
        finally:
            _release([key])

    threading.Thread(target=_run, name=f"report-backfill-{tenant_id}", daemon=True).start()
    return True


def warm_months_async(tenant_id: str, report_id: str, ym_list: list, token: str,
                      number_format=None) -> bool:
    """Background-cache specific closed months for one report — used by the
    answer path when a cached range has gaps, so the chat request is answered
    LIVE now and the cache is populated for next time (never fetched inline).
    Deduped per (tenant, report, month) so a running backfill — or another
    report warming at the same time — no longer swallows it."""
    ym_list = [ym for ym in (ym_list or [])]
    if not ym_list or not token:
        return False
    keys = _claim([(tenant_id, report_id, ym) for ym in ym_list])
    if not keys:
        return False
    months = [k[2] for k in keys]

    def _run():
        conn = get_internal_conn()
        try:
            nf = number_format
            if nf is None:
                cur = conn.cursor(dictionary=True)
                cur.execute("SELECT number_format FROM tenant_profile WHERE tenant_id=%s",
                            (tenant_id,))
                r = cur.fetchone()
                nf = r["number_format"] if r else None
            for ym in months:
                try:
                    ingest_month(conn, tenant_id, report_id, ym, token, nf)
                    time.sleep(_CALL_GAP)
                except Exception as e:
                    log.warning("Async month warm failed", tenant=tenant_id,
                                report=report_id, month=ym, error=str(e))
        except Exception as e:
            log.error("Month warm crashed", tenant=tenant_id, error=str(e))
        finally:
            conn.close()
            _release(keys)

    threading.Thread(target=_run, name=f"report-warm-{tenant_id}", daemon=True).start()
    return True
