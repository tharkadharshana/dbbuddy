"""
report_cache/read.py
======================
Thin read side of the cache (PLAN_03 Step 4). Used directly by tests now;
PLAN 05's answer layer builds cache-first aggregation on top of this.

coverage() is what PLAN 05 uses to decide cache-hit vs API-fetch (doc 09
Part 4): does report_daily_fact fully cover a requested day range, and does
that range include the still-mutating open period?
"""

import json
from datetime import date
from typing import List

from logger import get_logger
from report_cache.periods import daterange_days
from report_cache.registry import REPORTS

log = get_logger(__name__)


def _parse_metrics(row: dict) -> dict:
    value = row.get("metrics")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            log.warning("read: unparseable metrics JSON", row_keys=list(row.keys()))
            return {}
    return value or {}


def get_daily_facts(conn, tenant_id: str, report_id: str, start: date, end: date,
                     shop_id: str = "all") -> List[dict]:
    """[{business_date, metrics, status, fetched_at}, ...] ordered by date."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT business_date, metrics, status, fetched_at
        FROM report_daily_fact
        WHERE tenant_id=%s AND report_id=%s AND shop_id=%s
          AND business_date BETWEEN %s AND %s
        ORDER BY business_date
        """,
        (tenant_id, report_id, shop_id, start, end),
    )
    rows = cursor.fetchall()
    cursor.close()

    for row in rows:
        row["metrics"] = _parse_metrics(row)
    return rows


def get_dim_facts(conn, tenant_id: str, report_id: str, period_month: date,
                   shop_id: str = "all") -> List[dict]:
    """[{dim_type, dim_key, dim_name, metrics, status, fetched_at}, ...] for one month."""
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT dim_type, dim_key, dim_name, metrics, status, fetched_at
        FROM report_dim_fact
        WHERE tenant_id=%s AND report_id=%s AND shop_id=%s AND period_month=%s
        ORDER BY dim_name
        """,
        (tenant_id, report_id, shop_id, period_month.replace(day=1)),
    )
    rows = cursor.fetchall()
    cursor.close()

    for row in rows:
        row["metrics"] = _parse_metrics(row)
    return rows


def coverage(conn, tenant_id: str, report_id: str, start: date, end: date,
             shop_id: str = "all") -> dict:
    """{"covered": bool, "missing_days": [...], "has_open": bool} for a
    scalar (daily-grain) report over [start, end]. `covered` is True only if
    every calendar day in the range has a report_daily_fact row. `has_open`
    is True if any present day is still 'open', OR if today falls within the
    requested range (even before that day's row has been ingested yet) —
    either way, the caller can't treat the range as fully finalized."""
    report = REPORTS.get(report_id)
    if report is None:
        raise ValueError(f"Unknown report_id: {report_id!r}")
    if report.kind != "scalar":
        raise ValueError(f"coverage() is for scalar reports only; {report_id} is {report.kind!r}")
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end})")

    rows = get_daily_facts(conn, tenant_id, report_id, start, end, shop_id=shop_id)
    present_days = {row["business_date"] for row in rows}

    missing_days = [d for d in daterange_days(start, end) if d not in present_days]

    today = date.today()
    has_open = any(row["status"] == "open" for row in rows) or (start <= today <= end)

    return {
        "covered": len(missing_days) == 0,
        "missing_days": missing_days,
        "has_open": has_open,
    }
