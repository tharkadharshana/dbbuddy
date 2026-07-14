"""
report_cache/insights/forecast.py
===================================
Forecast an additive metric over a cached daily series (PLAN 06 Step 1). Reuses
analytics.run_forecast (Prophet) — does NOT re-implement forecasting.

Rules enforced here (doc 09 C1/C3, plan Step 1/5):
  - Additive metrics only. A ratio/non_additive metric (avg_ticket, margin %) is
    rejected — ratios are derived from forecasted components, never forecast
    directly.
  - Daily-cacheable reports only (sales_summary today) — the others have no valid
    per-day series in the cache.
  - Minimum-history guard: ~6 weeks of non-zero days, else a graceful
    "not enough history" instead of a garbage model.
  - Horizon capped (future isn't limited by the tier's history window, but we
    cap it so a request can't ask for a 5-year projection).
"""

import os
from datetime import date
from typing import Optional

from logger import get_logger
from report_cache import read, tiers
from report_cache.registry import REPORTS

log = get_logger(__name__)

_MIN_HISTORY_DAYS = int(os.getenv("INSIGHTS_FORECAST_MIN_DAYS", "42"))   # ~6 weeks of non-zero days
_MAX_HORIZON_DAYS = int(os.getenv("INSIGHTS_FORECAST_MAX_HORIZON", "90"))


def forecast_metric(conn, tenant_id: str, metric: str = "net_sales",
                    report_id: str = "sales_summary", horizon_days: int = 30,
                    shop_id: str = "all", token: Optional[str] = None,
                    tier: Optional[str] = None) -> dict:
    """Return {status, metric, horizon_days, history, forecast, summary,
    weekly_seasonality} for an additive daily metric, or a status of
    'unsupported' / 'not_additive' / 'insufficient_history' / 'error' with a
    human `message`. `token`/`tier` are accepted for signature parity with the
    other tools (the series is cache-only — historical closed days already live
    in the cache from onboarding/backfill)."""
    report = REPORTS.get(report_id)
    if report is None or not report.daily_cacheable:
        return {"status": "unsupported",
                "message": f"Forecasting isn't available for '{report_id}' — try sales figures."}

    m = next((mm for mm in report.metrics if mm.key == metric), None)
    if m is None:
        return {"status": "unknown_metric", "message": f"Unknown metric '{metric}'."}
    if m.agg != "sum":
        return {"status": "not_additive",
                "message": f"'{metric}' is a ratio/derived figure and can't be forecast directly; "
                           "forecast its components (e.g. net sales) instead."}

    win_start = tiers.window_start(tenant_id)
    facts = read.get_daily_facts(conn, tenant_id, report_id, win_start, date.today(), shop_id=shop_id)
    series = [(f["business_date"], f["metrics"].get(metric))
              for f in facts if f["metrics"].get(metric) is not None]
    non_zero_days = sum(1 for _, v in series if v)

    if non_zero_days < _MIN_HISTORY_DAYS:
        return {"status": "insufficient_history",
                "message": "I need at least about 6 weeks of sales history to forecast reliably. "
                           "Keep recording sales and check back soon.",
                "history_days": non_zero_days}

    horizon = max(1, min(int(horizon_days), _MAX_HORIZON_DAYS))
    try:
        from analytics import run_forecast
        fc = run_forecast(series, periods=horizon)
    except ValueError as exc:               # too few points despite the guard
        return {"status": "insufficient_history", "message": str(exc)}
    except ImportError:
        log.warning("forecast_metric: prophet not installed")
        return {"status": "error", "message": "Forecasting is temporarily unavailable."}

    log.info("Forecast produced", tenant=tenant_id, metric=metric, horizon=horizon,
             history_days=non_zero_days)
    return {
        "status": "ok",
        "metric": metric,
        "horizon_days": horizon,
        "history": fc["historical"],
        "forecast": fc["forecast"],
        "summary": fc["summary"],
        "weekly_seasonality": fc["weekly_seasonality"],
        "disclaimer": "Forecasts are estimates based on your past sales, not guarantees.",
    }
