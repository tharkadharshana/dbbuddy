"""
providers/loyverse/analytics.py
================================
Pre-built analytics templates for Loyverse POS integration.
These work on the synced tables in DataMind's internal database.
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
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
            GROUP BY DATE(created_at)
            ORDER BY date
        """
    },
    
    "top_items": {
        "title": "Top 20 Items by Revenue",
        "description": "Best selling items in the last 30 days",
        "type": "table",
        "sql": """
            SELECT 
                i.name as item,
                i.category,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.line_total), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_items i ON li.item_id = i.id
            JOIN {prefix}_receipts r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY i.id, i.name, i.category
            ORDER BY revenue DESC
            LIMIT 20
        """
    },
    
    "customer_insights": {
        "title": "Customer Purchase Insights",
        "description": "Customer spending and frequency analysis",
        "type": "table",
        "sql": """
            SELECT 
                c.name as customer,
                c.email,
                DATEDIFF(CURDATE(), MAX(r.created_at)) as days_since_last,
                COUNT(DISTINCT r.id) as total_visits,
                ROUND(SUM(r.total_money), 2) as lifetime_value,
                ROUND(AVG(r.total_money), 2) as avg_spend
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
        "description": "Transaction distribution by payment type",
        "type": "chart",
        "sql": """
            SELECT 
                payment_type,
                COUNT(*) as transactions,
                ROUND(SUM(total_money), 2) as revenue,
                ROUND(AVG(total_money), 2) as avg_transaction
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                AND payment_type IS NOT NULL
            GROUP BY payment_type
            ORDER BY revenue DESC
        """
    },
    
    "store_performance": {
        "title": "Store Performance Comparison",
        "description": "Revenue and metrics by store location",
        "type": "table",
        "sql": """
            SELECT 
                s.name as store,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_money), 2) as revenue,
                ROUND(AVG(r.total_money), 2) as avg_ticket,
                COUNT(DISTINCT r.customer_id) as customers
            FROM {prefix}_receipts r
            JOIN {prefix}_stores s ON r.store_id = s.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY s.id, s.name
            ORDER BY revenue DESC
        """
    },
    
    "employee_sales": {
        "title": "Employee Sales Performance",
        "description": "Sales metrics by employee",
        "type": "table",
        "sql": """
            SELECT 
                e.name as employee,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_money), 2) as revenue,
                ROUND(AVG(r.total_money), 2) as avg_sale
            FROM {prefix}_receipts r
            JOIN {prefix}_employees e ON r.employee_id = e.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY e.id, e.name
            ORDER BY revenue DESC
        """
    },
    
    "category_breakdown": {
        "title": "Category Sales Breakdown",
        "description": "Revenue analysis by item category",
        "type": "table",
        "sql": """
            SELECT 
                i.category,
                COUNT(DISTINCT i.id) as item_count,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.line_total), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_items i ON li.item_id = i.id
            JOIN {prefix}_receipts r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY i.category
            ORDER BY revenue DESC
        """
    },
    
    "hourly_sales": {
        "title": "Sales by Hour",
        "description": "Peak hours analysis for the last 30 days",
        "type": "chart",
        "sql": """
            SELECT 
                HOUR(created_at) as hour,
                COUNT(*) as transactions,
                ROUND(SUM(total_money), 2) as revenue,
                ROUND(AVG(total_money), 2) as avg_ticket
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY HOUR(created_at)
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
    
    # Convert to dict format
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