"""
SalesPlay analytics templates.
Table naming: schema.sql creates tables WITHOUT underscore separator,
e.g. prefix="dm_abc123_salesplay" → tables are "dm_abc123_salesplayreceipts"
All SQL here uses {prefix}tablename (no extra underscore).
"""

import decimal
import datetime

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
            FROM {prefix}receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
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
                p.product_name                          AS product,
                COALESCE(cat.category_name, '—')        AS category,
                SUM(li.quantity)                        AS units_sold,
                ROUND(SUM(li.total_money), 2)           AS revenue,
                ROUND(AVG(li.price), 2)                 AS avg_price
            FROM {prefix}receipt_line_items li
            JOIN {prefix}products  p   ON li.product_id  = p.id
            JOIN {prefix}receipts  r   ON li.receipt_id  = r.id
            LEFT JOIN {prefix}categories cat ON p.category_id = cat.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.receipt_type = 'SALE'
            GROUP BY p.id, p.product_name, cat.category_name
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
                c.customer_name                         AS customer,
                DATEDIFF(CURDATE(), MAX(r.created_at))  AS days_since_last_purchase,
                COUNT(DISTINCT r.id)                    AS total_orders,
                ROUND(SUM(r.total_money), 2)            AS lifetime_value,
                ROUND(AVG(r.total_money), 2)            AS avg_order_value
            FROM {prefix}customers c
            JOIN {prefix}receipts  r ON c.id = r.customer_id
            WHERE r.receipt_type = 'SALE'
            GROUP BY c.id, c.customer_name
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
            FROM {prefix}receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
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
            FROM {prefix}receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
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
                COALESCE(cat.category_name, 'Uncategorized') AS category,
                COUNT(DISTINCT p.id)                         AS products_count,
                SUM(li.quantity)                             AS units_sold,
                ROUND(SUM(li.total_money), 2)                AS revenue,
                ROUND(AVG(li.price), 2)                      AS avg_price
            FROM {prefix}receipt_line_items li
            JOIN {prefix}products  p   ON li.product_id  = p.id
            JOIN {prefix}receipts  r   ON li.receipt_id  = r.id
            LEFT JOIN {prefix}categories cat ON p.category_id = cat.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.receipt_type = 'SALE'
            GROUP BY cat.id, cat.category_name
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
            FROM {prefix}receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
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
                COALESCE(shop_name, shop_id)            AS shop,
                COUNT(*)                                AS transactions,
                ROUND(SUM(total_money), 2)              AS revenue,
                ROUND(AVG(total_money), 2)              AS avg_ticket,
                COUNT(DISTINCT customer_id)             AS unique_customers
            FROM {prefix}receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND receipt_type = 'SALE'
            GROUP BY shop_id, shop_name
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
    """Run a pre-built SalesPlay analytics template."""
    import re as _re
    if not _re.match(r'^[A-Za-z0-9_]+$', table_prefix):
        raise ValueError(f"Invalid table_prefix for analytics: {table_prefix!r}")

    if template_id not in TEMPLATES:
        raise ValueError(f"Template '{template_id}' not found for SalesPlay")

    template = TEMPLATES[template_id]
    sql = template["sql"].format(prefix=table_prefix)

    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    cursor.close()

    data = [{col: _safe(val) for col, val in zip(cols, row)} for row in rows]

    return {
        "title": template["title"],
        "description": template["description"],
        "type": template["type"],
        "columns": cols,
        "data": data,
        "row_count": len(data),
    }
