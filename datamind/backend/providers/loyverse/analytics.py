"""
providers/loyverse/analytics.py
================================
Pre-built analytics templates for Loyverse POS integration.
These work on the synced tables in DataMind's internal database.

Column names and table names must match loyverse/schema.sql exactly.
Receipts PK is receipt_number (no id column).
Line items join via receipt_number, not receipt_id.
Employees column is cashier_id in receipts (not employee_id).
"""

TEMPLATES = {
    "revenue_trend": {
        "title": "Daily Revenue Trend",
        "description": "Revenue trend over the last 90 days",
        "category": "Revenue", "complexity": "simple", "icon": "📈",
        "type": "timeseries",
        "sql": """
            SELECT
                DATE(receipt_date) as date,
                ROUND(SUM(total_money), 2) as revenue,
                COUNT(*) as transactions,
                ROUND(AVG(total_money), 2) as avg_ticket
            FROM `{prefix}_receipts`
            WHERE receipt_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
            GROUP BY DATE(receipt_date)
            ORDER BY date
        """
    },

    "top_items": {
        "title": "Top 20 Items by Revenue",
        "description": "Best selling items in the last 30 days",
        "category": "Products", "complexity": "simple", "icon": "🏷️",
        "type": "table",
        "sql": """
            SELECT
                p.item_name as item,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_money), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM `{prefix}_receipt_line_items` li
            JOIN `{prefix}_products` p ON li.item_id = p.id
            JOIN `{prefix}_receipts` r ON li.receipt_number = r.receipt_number
            WHERE r.receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY p.id, p.item_name
            ORDER BY revenue DESC
            LIMIT 20
        """
    },

    "customer_insights": {
        "title": "Customer Purchase Insights",
        "description": "Customer spending and frequency analysis",
        "category": "Customers", "complexity": "medium", "icon": "👥",
        "type": "table",
        "sql": """
            SELECT
                c.name as customer,
                c.email,
                DATEDIFF(CURDATE(), MAX(r.receipt_date)) as days_since_last,
                COUNT(DISTINCT r.receipt_number) as total_visits,
                ROUND(SUM(r.total_money), 2) as lifetime_value,
                ROUND(AVG(r.total_money), 2) as avg_spend
            FROM `{prefix}_customers` c
            JOIN `{prefix}_receipts` r ON c.id = r.customer_id
            WHERE r.receipt_date IS NOT NULL
            GROUP BY c.id, c.name, c.email
            ORDER BY lifetime_value DESC
            LIMIT 50
        """
    },

    "payment_methods": {
        "title": "Payment Method Breakdown",
        "description": "Transaction distribution by payment type",
        "category": "Payments", "complexity": "simple", "icon": "💳",
        "type": "chart",
        "sql": """
            SELECT
                COALESCE(pl.payment_type_name, 'Unknown') as payment_method,
                COUNT(DISTINCT pl.receipt_number) as transactions,
                ROUND(SUM(pl.money_amount), 2) as revenue,
                ROUND(AVG(pl.money_amount), 2) as avg_transaction
            FROM `{prefix}_payment_line_items` pl
            JOIN `{prefix}_receipts` r ON pl.receipt_number = r.receipt_number
            WHERE r.receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY pl.payment_type_name
            ORDER BY revenue DESC
        """
    },

    "store_performance": {
        "title": "Store Performance Comparison",
        "description": "Revenue and metrics by store location",
        "category": "Locations", "complexity": "simple", "icon": "📍",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(s.name, 'Unknown') as store,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_money), 2) as revenue,
                ROUND(AVG(r.total_money), 2) as avg_ticket,
                COUNT(DISTINCT r.customer_id) as customers
            FROM `{prefix}_receipts` r
            LEFT JOIN `{prefix}_stores` s ON r.store_id = s.id
            WHERE r.receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY r.store_id, s.name
            ORDER BY revenue DESC
        """
    },

    "employee_sales": {
        "title": "Employee Sales Performance",
        "description": "Sales metrics by employee",
        "category": "Employees", "complexity": "simple", "icon": "👤",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(e.name, 'Unknown') as employee,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_money), 2) as revenue,
                ROUND(AVG(r.total_money), 2) as avg_sale
            FROM `{prefix}_receipts` r
            LEFT JOIN `{prefix}_employees` e ON r.cashier_id = e.id
            WHERE r.receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY r.cashier_id, e.name
            ORDER BY revenue DESC
        """
    },

    "category_breakdown": {
        "title": "Category Sales Breakdown",
        "description": "Revenue analysis by item category",
        "category": "Products", "complexity": "simple", "icon": "🏷️",
        "type": "table",
        "sql": """
            SELECT
                COALESCE(cat.name, 'Uncategorized') as category,
                COUNT(DISTINCT p.id) as item_count,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_money), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM `{prefix}_receipt_line_items` li
            JOIN `{prefix}_products` p ON li.item_id = p.id
            LEFT JOIN `{prefix}_categories` cat ON p.category_id = cat.id
            JOIN `{prefix}_receipts` r ON li.receipt_number = r.receipt_number
            WHERE r.receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY p.category_id, cat.name
            ORDER BY revenue DESC
        """
    },

    "hourly_sales": {
        "title": "Sales by Hour",
        "description": "Peak hours analysis for the last 30 days",
        "category": "Operations", "complexity": "simple", "icon": "🕐",
        "type": "chart",
        "sql": """
            SELECT
                HOUR(receipt_date) as hour,
                COUNT(*) as transactions,
                ROUND(SUM(total_money), 2) as revenue,
                ROUND(AVG(total_money), 2) as avg_ticket
            FROM `{prefix}_receipts`
            WHERE receipt_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY HOUR(receipt_date)
            ORDER BY hour
        """
    }
}


def run_loyverse_analytics(conn, table_prefix: str, template_id: str):
    """Run a pre-built Loyverse analytics template."""
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
