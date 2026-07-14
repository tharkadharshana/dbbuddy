"""
report_cache/insights/tools.py
================================
Registers the forecast/anomaly/growth tools onto the report MCP loop (PLAN 06
Step 4). Gated by INSIGHTS_ENABLED — a no-op when off, so the loop is unchanged.
The model calls these for forecast/trend questions; identity (tenant/token/tier)
stays server-side on the ReportToolContext closure (doc 08 §3.6).
"""

import os
from typing import Optional

from logger import get_logger
from report_cache import lookups
from report_cache.insights.forecast import forecast_metric
from report_cache.insights.trends import detect_anomalies, growth_summary

log = get_logger(__name__)

_INSIGHTS_ENABLED = os.getenv("INSIGHTS_ENABLED", "").lower() == "true"
log.info("Insights tools flag", enabled=_INSIGHTS_ENABLED)


def _shop_id(rctx, shop: Optional[str]) -> str:
    """Resolve+authorize a shop name to an id, or 'all'. Mirrors report_tools'
    guard without importing it (avoids a circular import)."""
    if not shop or shop.strip().lower() == "all":
        return "all"
    sid = lookups.resolve_shop(rctx.tenant_id, shop)
    if not sid or not lookups.is_shop_allowed(rctx.tenant_id, sid):
        raise ValueError(f"I couldn't find a shop matching '{shop}'.")
    return sid


def register_insight_tools(mcp, rctx) -> None:
    if not _INSIGHTS_ENABLED:
        return

    @mcp.tool()
    def forecast_sales(metric: str = "net_sales", horizon_days: int = 30,
                       shop: Optional[str] = None) -> dict:
        """Forecast a future sales metric (default net_sales) for the next
        horizon_days using the merchant's own sales history. Additive metrics
        only. Returns the forecast with a confidence range, or a clear message if
        there isn't enough history."""
        result = forecast_metric(rctx.business.conn, rctx.tenant_id, metric=metric,
                                 horizon_days=horizon_days, shop_id=_shop_id(rctx, shop),
                                 token=rctx.token, tier=rctx.tier)
        if result.get("status") == "ok":
            rctx.last_result = {
                "report_id": "sales_summary", "provenance": "from_cache", "source": "forecast",
                "columns": ["date", "yhat", "yhat_lower", "yhat_upper"],
                "data": result["forecast"], "summary": result["summary"],
            }
        return result

    @mcp.tool()
    def sales_anomalies(metric: str = "net_sales", shop: Optional[str] = None) -> dict:
        """Find days where the given sales metric was unusually high or low in the
        merchant's recent history (default net_sales)."""
        return detect_anomalies(rctx.business.conn, rctx.tenant_id, metric=metric,
                                shop_id=_shop_id(rctx, shop), tier=rctx.tier)

    @mcp.tool()
    def sales_growth(metric: str = "net_sales", months: int = 6,
                     shop: Optional[str] = None) -> dict:
        """Month-over-month totals and % change for a sales metric (default
        net_sales), for "how am I trending?" questions."""
        result = growth_summary(rctx.business.conn, rctx.tenant_id, metric=metric,
                                months=months, shop_id=_shop_id(rctx, shop), tier=rctx.tier)
        if result.get("status") == "ok":
            rctx.last_result = {
                "report_id": "sales_summary", "provenance": "from_cache", "source": "growth",
                "columns": ["month", "value", "mom_pct"], "data": result["months"], "summary": None,
            }
        return result
