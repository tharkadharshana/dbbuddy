"""
providers/salesplay/analytics.py
=================================
Pre-built analytics templates for SalesPlay POS integration.
These work on the synced tables in DataMind's internal database.

Table naming: schema creates tables as {prefix}shops, {prefix}receipts, etc.
(no extra underscore — the prefix itself ends in the provider name).
Column names match salesplay/schema.sql exactly.
"""

TEMPLATES = {
    "revenue_trend": {
        "title": "Daily Revenue Trend",
        "description": "Revenue trend over the last 90 days",
        "type": "timeseries",
        "sql": """
            SELECT
                DATE(created_at) as date,
                ROUND(SUM(total_money), 2) as revenue,
                COUNT(*) as transactions,
                ROUND(AVG(total_money), 2) as avg_ticket
            FROM `{prefix}receipts`
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
              AND status != 'VOID'
            GROUP BY DATE(created_at)
            ORDER BY date
        """
    },

    "top_products": {
        "title": "Top 20 Products by Revenue",
        "description": "Best selling products in the last 30 days",
        "type": "table",
        "sql": """
            SELECT
                p.product_name as product,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_money), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM `{prefix}receipt_line_items` li
            JOIN `{prefix}products` p ON li.product_id = p.id
            JOIN `{prefix}receipts` r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.status != 'VOID'
            GROUP BY p.id, p.product_name
            ORDER BY revenue DESC
            LIMIT 20
        """
    },

    "customer_analysis": {
        "title": "Customer Purchase Analysis",
        "description": "RFM-style analysis on customer behavior",
        "type": "table",
        "sql": """
            SELECT
                c.customer_name as customer,
                DATEDIFF(CURDATE(), MAX(r.created_at)) as days_since_last_purchase,
                COUNT(DISTINCT r.id) as total_orders,
                ROUND(SUM(r.total_money), 2) as lifetime_value,
                ROUND(AVG(r.total_money), 2) as avg_order_value
            FROM `{prefix}customers` c
            JOIN `{prefix}receipts` r ON c.id = r.customer_id
            WHERE r.created_at IS NOT NULL
              AND r.status != 'VOID'
            GROUP BY c.id, c.customer_name
            ORDER BY lifetime_value DESC
            LIMIT 50
        """
    },

    "payment_breakdown": {
        "title": "Payment Method Distribution",
        "description": "Revenue by payment type in the last 30 days",
        "type": "chart",
        "sql": """
            SELECT
                COALESCE(r.payment_type_name, 'Unknown') as payment_method,
                COUNT(*) as transaction_count,
                ROUND(SUM(r.total_money), 2) as total_revenue,
                ROUND(AVG(r.total_money), 2) as avg_transaction
            FROM `{prefix}receipts` r
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.status != 'VOID'
            GROUP BY r.payment_type_name
            ORDER BY total_revenue DESC
        """
    },

    "hourly_performance": {
        "title": "Sales by Hour of Day",
        "description": "Peak sales hours analysis for the last 30 days",
        "type": "chart",
        "sql": """
            SELECT
                HOUR(created_at) as hour_of_day,
                COUNT(*) as transactions,
                ROUND(SUM(total_money), 2) as revenue,
                ROUND(AVG(total_money), 2) as avg_ticket
            FROM `{prefix}receipts`
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND status != 'VOID'
            GROUP BY HOUR(created_at)
            ORDER BY hour_of_day
        """
    },

    "category_performance": {
        "title": "Category Sales Performance",
        "description": "Revenue breakdown by product category",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(cat.category_name, 'Uncategorized') as category,
                COUNT(DISTINCT p.id) as products_count,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_money), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM `{prefix}receipt_line_items` li
            JOIN `{prefix}products` p ON li.product_id = p.id
            LEFT JOIN `{prefix}categories` cat ON p.category_id = cat.id
            JOIN `{prefix}receipts` r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.status != 'VOID'
            GROUP BY cat.id, cat.category_name
            ORDER BY revenue DESC
        """
    },

    "daily_summary": {
        "title": "Daily Sales Summary",
        "description": "Complete daily sales metrics for the last 30 days",
        "type": "table",
        "sql": """
            SELECT
                DATE(created_at) as sale_date,
                COUNT(*) as transactions,
                ROUND(SUM(total_money), 2) as revenue,
                ROUND(AVG(total_money), 2) as avg_ticket,
                COUNT(DISTINCT customer_id) as unique_customers
            FROM `{prefix}receipts`
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND status != 'VOID'
            GROUP BY DATE(created_at)
            ORDER BY sale_date DESC
        """
    },

    "shop_performance": {
        "title": "Shop Performance Comparison",
        "description": "Revenue and transactions by shop location",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(s.shop_name, r.shop_name, 'Unknown') as shop,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_money), 2) as revenue,
                ROUND(AVG(r.total_money), 2) as avg_ticket,
                COUNT(DISTINCT r.customer_id) as customers
            FROM `{prefix}receipts` r
            LEFT JOIN `{prefix}shops` s ON r.shop_id = s.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
              AND r.status != 'VOID'
            GROUP BY r.shop_id, s.shop_name, r.shop_name
            ORDER BY revenue DESC
        """
    }
}


def run_salesplay_analytics(conn, table_prefix: str, template_id: str):
    """Run a pre-built SalesPlay analytics template."""
    if template_id not in TEMPLATES:
        raise ValueError(f"Template {template_id} not found")

    template = TEMPLATES[template_id]
    sql = template["sql"].format(prefix=table_prefix)

    cursor = conn.cursor()
    cursor.execute(sql)

    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()

    import decimal, datetime
    def safe(v):
        if isinstance(v, decimal.Decimal): return float(v)
        if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
        return v

    data = [{col: safe(val) for col, val in zip(cols, row)} for row in rows]

    return {
        "title": template["title"],
        "description": template["description"],
        "type": template["type"],
        "columns": cols,
        "data": data,
        "row_count": len(data)
    }
