"""
SalesPlay analytics templates.

Queries target the shared normalized sp_* tables (tenant_id partitioned)
instead of JSON views over integration_records. This eliminates the Block
Nested Loop full-table-scan JOINs that caused top_products to take 507s
and category_performance 680s.

Before: JOIN between views → 37,848 × 37,848 comparisons, no usable index
After:  Single-table GROUP BY with WHERE tenant_id='...' → uses (tenant_id,
        created_at) composite index → milliseconds

category_name is stored denormalized in sp_receipt_line_items at sync time,
so the 4-table JOIN (receipts × line_items × products × categories) is gone.
"""

import os
import decimal
import datetime
import time as _time

# ── TTL result cache ──────────────────────────────────────────────────────────
# Simple in-memory cache per (tenant_id, template_id). Results are valid for
# CACHE_TTL seconds. Busted automatically after each sync via cache_bust().

_result_cache: dict = {}
_CACHE_TTL = int(os.getenv("ANALYTICS_CACHE_TTL", "300"))  # configurable via .env


def _cache_get(tenant_id: str, template_id: str):
    key = (tenant_id, template_id)
    entry = _result_cache.get(key)
    if entry:
        result, expires_at = entry
        if _time.monotonic() < expires_at:
            return result
        del _result_cache[key]
    return None


def _cache_set(tenant_id: str, template_id: str, result: dict):
    _result_cache[(tenant_id, template_id)] = (result, _time.monotonic() + _CACHE_TTL)


def cache_bust(tenant_id: str) -> None:
    """Invalidate all cached results for a tenant. Call after sync completes."""
    for key in list(_result_cache):
        if key[0] == tenant_id:
            del _result_cache[key]


# ── Analytics templates ───────────────────────────────────────────────────────
# All queries use {tenant_id} placeholder — filled at runtime.
# Single-table queries use sp_receipts (receipts are already denormalized with
# shop_name, customer_name, payment_type_name).
# Multi-entity queries use sp_receipt_line_items which carries product_name and
# category_name denormalized at sync time — no JOINs needed.

TEMPLATES = {
    "revenue_trend": {
        "title": "Daily Revenue Trend",
        "description": "Revenue over the last 90 days",
        "category": "Revenue", "complexity": "simple", "icon": "📈",
        "type": "timeseries",
        "sql": """
            SELECT
                DATE(created_at)            AS date,
                ROUND(SUM(total_money), 2)  AS revenue,
                COUNT(*)                    AS transactions,
                ROUND(AVG(total_money), 2)  AS avg_ticket
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
              AND receipt_type = 'SALE'
            GROUP BY DATE(created_at)
            ORDER BY date
        """
    },

    "top_products": {
        "title": "Top 20 Products by Revenue",
        "description": "Best-selling products in the last 30 days",
        "category": "Products", "complexity": "simple", "icon": "🏆",
        "type": "table",
        "sql": """
            SELECT
                product_name                            AS product,
                COALESCE(category_name, '—')            AS category,
                SUM(quantity)                           AS units_sold,
                ROUND(SUM(total_money), 2)              AS revenue,
                ROUND(AVG(price), 2)                    AS avg_price
            FROM sp_receipt_line_items
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY product_name, category_name
            ORDER BY revenue DESC
            LIMIT 20
        """
    },

    "customer_analysis": {
        "title": "Customer Purchase Analysis",
        "description": "Spending behaviour and loyalty metrics per customer",
        "category": "Customers", "complexity": "medium", "icon": "👥",
        "type": "table",
        "sql": """
            SELECT
                customer_name                           AS customer,
                DATEDIFF(CURDATE(), MAX(created_at))    AS days_since_last_purchase,
                COUNT(DISTINCT id)                      AS total_orders,
                ROUND(SUM(total_money), 2)              AS lifetime_value,
                ROUND(AVG(total_money), 2)              AS avg_order_value
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND receipt_type = 'SALE'
              AND customer_name IS NOT NULL
              AND customer_name != ''
            GROUP BY customer_name
            ORDER BY lifetime_value DESC
            LIMIT 50
        """
    },

    "payment_breakdown": {
        "title": "Payment Method Distribution",
        "description": "Revenue breakdown by payment type in the last 30 days",
        "category": "Payments", "complexity": "simple", "icon": "💳",
        "type": "chart",
        "sql": """
            SELECT
                COALESCE(payment_type_name, 'Unknown')  AS payment_method,
                COUNT(*)                                AS transaction_count,
                ROUND(SUM(total_money), 2)              AS total_revenue,
                ROUND(AVG(total_money), 2)              AS avg_transaction
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY payment_type_name
            ORDER BY total_revenue DESC
        """
    },

    "hourly_performance": {
        "title": "Sales by Hour of Day",
        "description": "Peak sales hours in the last 30 days",
        "category": "Operations", "complexity": "simple", "icon": "🕐",
        "type": "chart",
        "sql": """
            SELECT
                HOUR(created_at)            AS hour_of_day,
                COUNT(*)                    AS transactions,
                ROUND(SUM(total_money), 2)  AS revenue,
                ROUND(AVG(total_money), 2)  AS avg_ticket
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY HOUR(created_at)
            ORDER BY hour_of_day
        """
    },

    "category_performance": {
        "title": "Category Sales Performance",
        "description": "Revenue breakdown by product category in the last 30 days",
        "category": "Products", "complexity": "simple", "icon": "🏷️",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(NULLIF(category_name, ''), 'Uncategorized') AS category,
                COUNT(DISTINCT product_name)             AS products_count,
                SUM(quantity)                            AS units_sold,
                ROUND(SUM(total_money), 2)               AS revenue,
                ROUND(AVG(price), 2)                     AS avg_price
            FROM sp_receipt_line_items
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY COALESCE(NULLIF(category_name, ''), 'Uncategorized')
            ORDER BY revenue DESC
        """
    },

    "daily_summary": {
        "title": "Daily Sales Summary",
        "description": "Complete daily metrics for the last 30 days",
        "category": "Revenue", "complexity": "simple", "icon": "📅",
        "type": "table",
        "sql": """
            SELECT
                DATE(created_at)                        AS sale_date,
                COUNT(*)                                AS transactions,
                ROUND(SUM(total_money), 2)              AS revenue,
                ROUND(AVG(total_money), 2)              AS avg_ticket,
                COUNT(DISTINCT customer_id)             AS unique_customers
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY DATE(created_at)
            ORDER BY sale_date DESC
        """
    },

    "shop_performance": {
        "title": "Shop Performance Comparison",
        "description": "Revenue and transactions by shop location in the last 30 days",
        "category": "Locations", "complexity": "simple", "icon": "🏪",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(NULLIF(shop_name, ''), NULLIF(shop_id, ''), 'Unknown Shop') AS shop,
                COUNT(*)                                AS transactions,
                ROUND(SUM(total_money), 2)              AS revenue,
                ROUND(AVG(total_money), 2)              AS avg_ticket,
                COUNT(DISTINCT customer_id)             AS unique_customers
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY COALESCE(NULLIF(shop_name, ''), NULLIF(shop_id, ''), 'Unknown Shop')
            ORDER BY revenue DESC
        """
    },
}


def _safe(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    return v


def run_salesplay_analytics(conn, table_prefix: str, template_id: str) -> dict:
    """Run a pre-built SalesPlay analytics template against sp_* shared tables."""
    import re as _re
    if not _re.match(r'^[A-Za-z0-9_]+$', table_prefix):
        raise ValueError(f"Invalid table_prefix for analytics: {table_prefix!r}")

    if template_id not in TEMPLATES:
        raise ValueError(f"Template '{template_id}' not found for SalesPlay")

    # Return cached result if still fresh
    cached = _cache_get(table_prefix, template_id)
    if cached is not None:
        return {**cached, "cached": True}

    template = TEMPLATES[template_id]
    sql = template["sql"].format(tenant_id=table_prefix)

    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    cursor.close()

    data = [{col: _safe(val) for col, val in zip(cols, row)} for row in rows]

    result = {
        "title":       template["title"],
        "description": template["description"],
        "type":        template["type"],
        "columns":     cols,
        "data":        data,
        "row_count":   len(data),
        "cached":      False,
    }
    _cache_set(table_prefix, template_id, result)
    return result
