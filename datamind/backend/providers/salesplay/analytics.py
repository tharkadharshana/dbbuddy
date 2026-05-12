"""
providers/salesplay/analytics.py
=================================
Pre-built analytics templates for SalesPlay POS integration.
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
                ROUND(SUM(total_amount), 2) as revenue,
                COUNT(*) as transactions,
                ROUND(AVG(total_amount), 2) as avg_ticket
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
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
                p.name as product,
                p.category,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_amount), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_products p ON li.product_id = p.id
            JOIN {prefix}_receipts r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY p.id, p.name, p.category
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
                c.name as customer,
                DATEDIFF(CURDATE(), MAX(r.created_at)) as days_since_last_purchase,
                COUNT(DISTINCT r.id) as total_orders,
                ROUND(SUM(r.total_amount), 2) as lifetime_value,
                ROUND(AVG(r.total_amount), 2) as avg_order_value
            FROM {prefix}_customers c
            JOIN {prefix}_receipts r ON c.id = r.customer_id
            WHERE r.created_at IS NOT NULL
            GROUP BY c.id, c.name
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
                pt.name as payment_method,
                COUNT(*) as transaction_count,
                ROUND(SUM(r.total_amount), 2) as total_revenue,
                ROUND(AVG(r.total_amount), 2) as avg_transaction
            FROM {prefix}_receipts r
            JOIN {prefix}_payment_types pt ON r.payment_type_id = pt.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY pt.id, pt.name
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
                ROUND(SUM(total_amount), 2) as revenue,
                ROUND(AVG(total_amount), 2) as avg_ticket
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
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
                p.category,
                COUNT(DISTINCT p.id) as products_count,
                SUM(li.quantity) as units_sold,
                ROUND(SUM(li.total_amount), 2) as revenue,
                ROUND(AVG(li.price), 2) as avg_price
            FROM {prefix}_receipt_line_items li
            JOIN {prefix}_products p ON li.product_id = p.id
            JOIN {prefix}_receipts r ON li.receipt_id = r.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY p.category
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
                ROUND(SUM(total_amount), 2) as revenue,
                ROUND(AVG(total_amount), 2) as avg_ticket,
                COUNT(DISTINCT customer_id) as unique_customers
            FROM {prefix}_receipts
            WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
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
                s.name as shop_name,
                COUNT(*) as transactions,
                ROUND(SUM(r.total_amount), 2) as revenue,
                ROUND(AVG(r.total_amount), 2) as avg_ticket,
                COUNT(DISTINCT r.customer_id) as customers
            FROM {prefix}_receipts r
            JOIN {prefix}_shops s ON r.shop_id = s.id
            WHERE r.created_at >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY s.id, s.name
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