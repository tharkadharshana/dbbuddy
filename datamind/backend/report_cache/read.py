"""
report_cache/read.py — read cached report facts back out.

Coverage comes from report_sync_state (not from counting fact rows — a month
with zero sales legitimately has no rows). Aggregation across periods happens
in the answer layer, which owns the additivity rules.
"""

import json

from .ingest import month_start


def cached_months(conn, tenant_id: str, report_id: str, shop_id: str = "all") -> set:
    """{'YYYY-MM', ...} of finally-cached months."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT month FROM report_sync_state WHERE tenant_id=%s AND report_id=%s "
        "AND shop_id=%s AND status='final'", (tenant_id, report_id, shop_id))
    return {row[0].strftime("%Y-%m") for row in cursor.fetchall()}


def read_month_facts(conn, tenant_id: str, report_id: str, months: list,
                     shop_id: str = "all") -> dict:
    """{'YYYY-MM': {metric: value}} for cached monthly scalar facts."""
    if not months:
        return {}
    firsts = [month_start(ym) for ym in months]
    cursor = conn.cursor()
    cursor.execute(
        "SELECT period_start, metrics FROM report_fact WHERE tenant_id=%s "
        f"AND report_id=%s AND shop_id=%s AND grain='month' AND period_start IN "
        f"({','.join(['%s'] * len(firsts))})",
        (tenant_id, report_id, shop_id, *firsts))
    return {row[0].strftime("%Y-%m"): json.loads(row[1]) for row in cursor.fetchall()}


def read_daily_facts(conn, tenant_id: str, report_id: str, start, end,
                     shop_id: str = "all") -> list:
    """[(date, {metric: value})] for cached daily facts in [start, end]."""
    cursor = conn.cursor()
    cursor.execute(
        "SELECT period_start, metrics FROM report_fact WHERE tenant_id=%s "
        "AND report_id=%s AND shop_id=%s AND grain='day' "
        "AND period_start BETWEEN %s AND %s ORDER BY period_start",
        (tenant_id, report_id, shop_id, start, end))
    return [(row[0], json.loads(row[1])) for row in cursor.fetchall()]


def read_dim_facts(conn, tenant_id: str, report_id: str, months: list,
                   shop_id: str = "all") -> list:
    """[(month 'YYYY-MM', dim_key, dim_label, {metric: value})] for cached months."""
    if not months:
        return []
    firsts = [month_start(ym) for ym in months]
    cursor = conn.cursor()
    cursor.execute(
        "SELECT month, dim_key, dim_label, metrics FROM report_dim_fact "
        f"WHERE tenant_id=%s AND report_id=%s AND shop_id=%s AND month IN "
        f"({','.join(['%s'] * len(firsts))}) ORDER BY month, dim_key",
        (tenant_id, report_id, shop_id, *firsts))
    return [(row[0].strftime("%Y-%m"), row[1], row[2], json.loads(row[3]))
            for row in cursor.fetchall()]
