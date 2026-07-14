"""
report_cache/insights/trends.py
=================================
Anomaly detection and month-over-month growth over the cached daily series
(PLAN 06 Step 2). `detect_anomalies` wraps analytics.run_anomaly_detection;
`growth_summary` computes additive MoM deltas from the cached daily facts (doc
09 C3 — sum additive base metrics per month, never sum non-additive ones).
"""

import os
from collections import OrderedDict
from datetime import date, timedelta
from typing import Optional

from logger import get_logger
from report_cache import read, tiers
from report_cache.registry import REPORTS

log = get_logger(__name__)

_ANOMALY_MIN_POINTS = 5      # analytics.run_anomaly_detection's own floor
_ANOMALY_LOOKBACK_DAYS = int(os.getenv("INSIGHTS_ANOMALY_LOOKBACK_DAYS", "180"))


def _additive_series(conn, tenant_id, report_id, metric, start, end, shop_id):
    report = REPORTS.get(report_id)
    if report is None or not report.daily_cacheable:
        return None, "unsupported"
    m = next((mm for mm in report.metrics if mm.key == metric), None)
    if m is None:
        return None, "unknown_metric"
    if m.agg != "sum":
        return None, "not_additive"
    facts = read.get_daily_facts(conn, tenant_id, report_id, start, end, shop_id=shop_id)
    return facts, None


def detect_anomalies(conn, tenant_id: str, metric: str = "net_sales",
                     report_id: str = "sales_summary", shop_id: str = "all",
                     tier: Optional[str] = None) -> dict:
    """Flag unusually high/low days in the cached series. Returns
    {status, anomaly_count, anomalies[...]} or a status/message."""
    end = date.today()
    start = max(tiers.window_start(tenant_id), end - timedelta(days=_ANOMALY_LOOKBACK_DAYS))
    facts, err = _additive_series(conn, tenant_id, report_id, metric, start, end, shop_id)
    if err:
        return {"status": err, "message": f"Anomaly detection isn't available for {metric}."}

    series = [(f["business_date"], f["metrics"].get(metric))
              for f in facts if f["metrics"].get(metric) is not None]
    if len(series) < _ANOMALY_MIN_POINTS:
        return {"status": "insufficient_history",
                "message": "Not enough history yet to spot unusual days."}
    try:
        from analytics import run_anomaly_detection
        res = run_anomaly_detection(series, has_date=True)
    except (ValueError, ImportError) as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "ok", "metric": metric,
            "anomaly_count": res["anomaly_count"], "anomalies": res["anomalies"]}


def growth_summary(conn, tenant_id: str, metric: str = "net_sales",
                   report_id: str = "sales_summary", shop_id: str = "all",
                   months: int = 6, tier: Optional[str] = None) -> dict:
    """Month-over-month totals + % change for an additive metric, from cached
    daily facts. Returns {status, metric, months:[{month, value, mom_pct}]}."""
    end = date.today()
    start = tiers.window_start(tenant_id)
    facts, err = _additive_series(conn, tenant_id, report_id, metric, start, end, shop_id)
    if err:
        return {"status": err, "message": f"Growth isn't available for {metric}."}

    by_month: "OrderedDict[str, float]" = OrderedDict()
    for f in sorted(facts, key=lambda r: r["business_date"]):
        v = f["metrics"].get(metric)
        if v is None:
            continue
        key = f["business_date"].strftime("%Y-%m")
        by_month[key] = by_month.get(key, 0.0) + v

    if not by_month:
        return {"status": "insufficient_history", "message": "No monthly sales history yet."}

    items = list(by_month.items())[-months:]
    out = []
    prev = None
    for month, value in items:
        mom = round((value - prev) / prev * 100, 1) if prev else None
        out.append({"month": month, "value": round(value, 2), "mom_pct": mom})
        prev = value
    return {"status": "ok", "metric": metric, "months": out}
