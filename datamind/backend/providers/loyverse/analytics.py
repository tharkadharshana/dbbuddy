"""
Loyverse analytics templates.
Table naming: schema.sql creates tables WITH underscore separator,
e.g. prefix="dm_abc123_loyverse" → tables are "dm_abc123_loyverse_receipts"
All SQL here uses {prefix}_tablename.

Key schema notes:
- receipt_line_items FK to receipts is receipt_number (not receipt_id)
- products table column is item_name (not name)
- payment data is in {prefix}_payment_line_items (separate table)
- categories.name exists; products join to categories via category_id
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
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
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
                li.item_name                            AS product,
                COALESCE(cat.name, '—')                 AS category,
                SUM(li.quantity)                        AS units_sold,
                ROUND(SUM(li.total_money), 2)           AS revenue,
                ROUND(AVG(li.price), 2)                 AS avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_receipts r ON li.receipt_number = r.receipt_number
            LEFT JOIN {prefix}_categories cat ON li.category_id = cat.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY li.item_name, cat.name
            ORDER BY revenue DESC
            LIMIT 20
        """
    },

    "customer_insights": {
        "title": "Customer Purchase Insights",
        "description": "Spending behaviour and frequency per customer",
        "category": "Customers", "complexity": "medium", "icon": "👥",
        "type": "table",
        "sql": """
            SELECT
                c.name                                  AS customer,
                c.email,
                DATEDIFF(CURDATE(), MAX(r.created_at))  AS days_since_last,
                COUNT(DISTINCT r.receipt_number)        AS total_visits,
                ROUND(SUM(r.total_money), 2)            AS lifetime_value,
                ROUND(AVG(r.total_money), 2)            AS avg_spend
            FROM {prefix}_customers c
            JOIN {prefix}_receipts r ON c.id = r.customer_id
            WHERE r.created_at IS NOT NULL
            GROUP BY c.id, c.name, c.email
            ORDER BY lifetime_value DESC
            LIMIT 50
        """
    },

    "payment_methods": {
        "title": "Payment Method Breakdown",
        "description": "Transaction distribution by payment type in the last 30 days",
        "category": "Payments", "complexity": "simple", "icon": "💳",
        "type": "chart",
        "sql": """
            SELECT
                COALESCE(p.payment_type_name, 'Unknown') AS payment_method,
                COUNT(*)                                  AS transactions,
                ROUND(SUM(p.money_amount), 2)             AS revenue,
                ROUND(AVG(p.money_amount), 2)             AS avg_transaction
            FROM {prefix}_payment_line_items p
            JOIN {prefix}_receipts r ON p.receipt_number = r.receipt_number
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY p.payment_type_name
            ORDER BY revenue DESC
        """
    },

    "store_performance": {
        "title": "Store Performance Comparison",
        "description": "Revenue and transactions by store in the last 30 days",
        "category": "Locations", "complexity": "simple", "icon": "🏪",
        "type": "table",
        "sql": """
            SELECT
                s.name                                  AS store,
                COUNT(*)                                AS transactions,
                ROUND(SUM(r.total_money), 2)            AS revenue,
                ROUND(AVG(r.total_money), 2)            AS avg_ticket,
                COUNT(DISTINCT r.customer_id)           AS unique_customers
            FROM {prefix}_receipts r
            JOIN {prefix}_stores s ON r.store_id = s.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY s.id, s.name
            ORDER BY revenue DESC
        """
    },

    "employee_sales": {
        "title": "Employee Sales Performance",
        "description": "Sales metrics by employee in the last 30 days",
        "category": "Employees", "complexity": "simple", "icon": "👤",
        "type": "table",
        "sql": """
            SELECT
                e.name                                  AS employee,
                COUNT(*)                                AS transactions,
                ROUND(SUM(r.total_money), 2)            AS revenue,
                ROUND(AVG(r.total_money), 2)            AS avg_sale
            FROM {prefix}_receipts r
            JOIN {prefix}_employees e ON r.cashier_id = e.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY e.id, e.name
            ORDER BY revenue DESC
        """
    },

    "category_breakdown": {
        "title": "Category Sales Breakdown",
        "description": "Revenue by product category in the last 30 days",
        "category": "Products", "complexity": "simple", "icon": "🏷️",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(cat.name, 'Uncategorized')     AS category,
                COUNT(DISTINCT li.item_id)              AS items_count,
                SUM(li.quantity)                        AS units_sold,
                ROUND(SUM(li.total_money), 2)           AS revenue,
                ROUND(AVG(li.price), 2)                 AS avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_receipts r ON li.receipt_number = r.receipt_number
            LEFT JOIN {prefix}_categories cat ON li.category_id = cat.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY li.category_id, cat.name
            ORDER BY revenue DESC
        """
    },

    "hourly_sales": {
        "title": "Sales by Hour of Day",
        "description": "Peak hours analysis for the last 30 days",
        "category": "Operations", "complexity": "simple", "icon": "🕐",
        "type": "chart",
        "sql": """
            SELECT
                HOUR(created_at)            AS hour,
                COUNT(*)                    AS transactions,
                ROUND(SUM(total_money), 2)  AS revenue,
                ROUND(AVG(total_money), 2)  AS avg_ticket
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY HOUR(created_at)
            ORDER BY hour
        """
    },
}


def _safe(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return str(v)
    return v


def run_loyverse_analytics(conn, table_prefix: str, template_id: str) -> dict:
    """Run a pre-built Loyverse analytics template."""
    if template_id not in TEMPLATES:
        raise ValueError(f"Template '{template_id}' not found for Loyverse")

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
