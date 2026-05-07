"""
Schema Builder — runs ONCE per DB connection.

Given a schema + sample data, asks the LLM to:
1. Generate the analytics catalogue (what analyses are possible)
2. Generate valid SQL for every template in that catalogue
3. Detect the best columns for auto-forecast and auto-anomaly

Everything is stored in the cache. Zero LLM calls after that.
"""

import json
import re
from typing import Dict, Any, List, Tuple, Optional
from db import schema_to_text


# ── Template definitions (schema-agnostic goals) ──────────────────────────────
# These describe WHAT we want. The LLM figures out HOW using the actual schema.

TEMPLATE_GOALS = [
    {
        "id": "revenue_trend",
        "goal": "Monthly revenue trend: total sales amount, number of transactions, and average transaction value grouped by month, ordered by month descending, limit 24 months.",
        "category": "Revenue", "icon": "📈", "complexity": "simple",
        "title": "Monthly Revenue Trend",
        "description": "Track revenue, transaction count, and average ticket month by month.",
        "insight": "Identify growth months and seasonal patterns"
    },
    {
        "id": "revenue_by_category",
        "goal": "Revenue breakdown by product/item category: total revenue, total cost, gross profit, margin percentage, units sold, and number of orders per category. JOIN the sales/orders table with the products/items table.",
        "category": "Revenue", "icon": "🏷️", "complexity": "simple",
        "title": "Revenue by Category",
        "description": "Break down sales, gross profit, and margin by product category.",
        "insight": "See which categories drive profitability"
    },
    {
        "id": "revenue_by_location",
        "goal": "Revenue by store/branch/location: total revenue, transaction count, average transaction value, unique customer count. JOIN sales table with locations/branches table.",
        "category": "Revenue", "icon": "📍", "complexity": "simple",
        "title": "Revenue by Location",
        "description": "Compare sales performance across all store locations.",
        "insight": "Find your highest and lowest performing locations"
    },
    {
        "id": "hourly_pattern",
        "goal": "Hourly sales pattern: total revenue, transaction count, and average transaction value grouped by hour of day (0-23). Use HOUR() on the time/datetime column.",
        "category": "Revenue", "icon": "🕐", "complexity": "simple",
        "title": "Hourly Sales Pattern",
        "description": "Discover peak trading hours for staffing and promotions.",
        "insight": "Know your rush hours"
    },
    {
        "id": "daily_trend_7",
        "goal": "Daily sales for the last 7 days: date, total revenue, order count, average order value, unique customer count. Filter to last 7 days using CURDATE() - INTERVAL 7 DAY.",
        "category": "Revenue", "icon": "📅", "complexity": "simple",
        "title": "Last 7 Days",
        "description": "Daily revenue, orders, and customers for the past week.",
        "insight": "Spot recent dips or spikes immediately"
    },
    {
        "id": "discount_analysis",
        "goal": "Discount impact: group transactions into discount bands (no discount, <5%, 5-10%, 10-20%, >20%) using CASE WHEN on discount/total ratio. Show count, average order value, and total revenue per band.",
        "category": "Revenue", "icon": "🏷️", "complexity": "medium",
        "title": "Discount Impact Analysis",
        "description": "See how discount levels affect order value and total revenue.",
        "insight": "Are discounts driving revenue or eroding it?"
    },
    {
        "id": "tax_analysis",
        "goal": "Monthly tax analysis: total tax collected, gross revenue, and effective tax rate percentage per month. Use DATE_FORMAT for month grouping.",
        "category": "Revenue", "icon": "🧾", "complexity": "simple",
        "title": "Tax Analysis",
        "description": "Monthly tax collected vs effective tax rate.",
        "insight": "Stay on top of tax obligations"
    },
    {
        "id": "top_products",
        "goal": "Top 20 products by revenue: product name, category, total revenue, units sold, gross profit, margin percentage, number of orders containing this product. JOIN sales lines with products table.",
        "category": "Products", "icon": "🥇", "complexity": "simple",
        "title": "Top Products",
        "description": "Rank all products by revenue, units, and profit margin.",
        "insight": "Double down on your bestsellers"
    },
    {
        "id": "slow_products",
        "goal": "Slow-moving products: products with fewer than 10 units sold OR not sold in 30+ days. Show product name, category, total units sold, revenue, and days since last sale using DATEDIFF(CURDATE(), MAX(sale_date)). LEFT JOIN to include products with zero sales.",
        "category": "Products", "icon": "🐌", "complexity": "medium",
        "title": "Slow-Moving Products",
        "description": "Flag products with low sales velocity or sold long ago.",
        "insight": "Clear deadstock before it becomes a loss"
    },
    {
        "id": "margin_by_category",
        "goal": "Margin analysis by category: number of SKUs, average selling price, average cost, average margin percentage, and total gross profit. JOIN products with sales lines.",
        "category": "Products", "icon": "💰", "complexity": "medium",
        "title": "Margin by Category",
        "description": "Compare gross margin percentage across product categories.",
        "insight": "Know where you make and lose money"
    },
    {
        "id": "product_velocity",
        "goal": "Product sales velocity: for each product, total units sold, total revenue, first sale date, last sale date, days active (DATEDIFF), units per day, and revenue per day. Only include products with at least 1 sale and more than 0 days active.",
        "category": "Products", "icon": "⚡", "complexity": "medium",
        "title": "Product Sales Velocity",
        "description": "Rank products by speed of sale — units per day.",
        "insight": "Identify fast movers to prioritise restocking"
    },
    {
        "id": "top_customers",
        "goal": "Top 25 customers by lifetime value: customer name, email or contact, total orders, total lifetime spend, average order value, loyalty points (if exists), last purchase date, and days since last purchase. JOIN customers with sales/orders table.",
        "category": "Customers", "icon": "👑", "complexity": "simple",
        "title": "Top Customers by LTV",
        "description": "Rank customers by lifetime value, frequency, and recency.",
        "insight": "Know your most valuable relationships"
    },
    {
        "id": "customer_retention",
        "goal": "Monthly customer retention: for each cohort month (first purchase month), count new customers and how many made a second purchase (total_orders > 1), calculate retention percentage. Use a subquery grouping by customer to get first purchase date and total order count.",
        "category": "Customers", "icon": "🔁", "complexity": "medium",
        "title": "Customer Retention",
        "description": "What % of new customers come back in subsequent months.",
        "insight": "A rising retention rate means a healthy business"
    },
    {
        "id": "loyalty_tiers",
        "goal": "Loyalty tier performance: group customers into tiers by loyalty points (No Points, Bronze 1-99, Silver 100-499, Gold 500-999, Platinum 1000+) using CASE WHEN. Show customer count, average lifetime value, and average order value per tier. JOIN customers with orders subquery.",
        "category": "Customers", "icon": "⭐", "complexity": "medium",
        "title": "Loyalty Tier Performance",
        "description": "Compare spend and frequency across loyalty point tiers.",
        "insight": "Is your loyalty programme driving revenue?"
    },
    {
        "id": "cashier_performance",
        "goal": "Staff/cashier performance ranking: employee name, location/branch, total transactions handled, total sales amount, average ticket size, average discount given, discount rate percentage, and number of distinct working days. JOIN sales with employees/staff and locations tables. Order by total sales descending.",
        "category": "Employees", "icon": "👤", "complexity": "medium",
        "title": "Staff Performance",
        "description": "Rank employees by sales, transactions, and avg ticket.",
        "insight": "Reward top performers and coach the rest"
    },
    {
        "id": "payment_methods",
        "goal": "Payment method breakdown: payment method name, transaction count, total revenue, average ticket, and revenue percentage of total using window function SUM() OVER(). Group by payment method, order by revenue descending.",
        "category": "Payments", "icon": "💳", "complexity": "simple",
        "title": "Payment Methods",
        "description": "Revenue split by payment method.",
        "insight": "Understand how customers prefer to pay"
    },
    {
        "id": "credit_outstanding",
        "goal": "Credit and collections by month: month, count of credit transactions, total credit amount given, total collected (where credit is marked complete/paid), and outstanding balance. Filter rows where a credit amount column is greater than 0.",
        "category": "Payments", "icon": "⚠️", "complexity": "medium",
        "title": "Credit & Outstanding",
        "description": "Track credit invoices, collections, and outstanding amounts.",
        "insight": "Stay on top of your receivables"
    },
    {
        "id": "growth_metrics",
        "goal": "Month-over-month growth metrics: for each month show revenue, order count, average order value, unique customers. Then calculate MoM growth % for each metric using LAG() window function or subquery. Order by month ascending.",
        "category": "Growth", "icon": "🚀", "complexity": "medium",
        "title": "MoM Growth Metrics",
        "description": "Calculate MoM revenue, orders, AOV, and customer count growth.",
        "insight": "Quantify whether the business is accelerating"
    },
    {
        "id": "location_comparison",
        "goal": "Location KPI comparison: for each location/branch show revenue, transaction count, average ticket, unique customers, total discounts given, discount rate %, staff count, and revenue per customer. JOIN sales with locations table.",
        "category": "Growth", "icon": "🗺️", "complexity": "medium",
        "title": "Location Comparison",
        "description": "Side-by-side KPI benchmarking of all locations.",
        "insight": "Benchmark locations against each other"
    },
    {
        "id": "order_types",
        "goal": "Revenue by order type and channel (e.g. dine-in, takeaway, delivery, online): order type, channel if available, order count, total revenue, average order value. Group by order type and channel.",
        "category": "Growth", "icon": "📦", "complexity": "simple",
        "title": "Order Types & Channels",
        "description": "Revenue split by order type and sales channel.",
        "insight": "Know which channel to invest in"
    },
    {
        "id": "inventory_movement",
        "goal": "Inventory movement log: product name, category, movement reason/type, net quantity change, entry count, and last movement date. JOIN inventory_logs or stock_movements table with products. Order by absolute quantity change descending, limit 30.",
        "category": "Inventory", "icon": "📦", "complexity": "medium",
        "title": "Inventory Movement",
        "description": "Track stock changes by product and reason.",
        "insight": "Prevent stockouts and identify waste"
    },
]


# ── LLM prompt builder ─────────────────────────────────────────────────────────

def build_sql_generation_prompt(schema_text: str, sample_text: str, template: Dict) -> str:
    return f"""You are an expert MySQL query writer. Analyse the database schema below and write a valid MySQL SELECT query for the requested analysis.

DATABASE SCHEMA:
{schema_text}

SAMPLE DATA (first rows from each table):
{sample_text}

ANALYSIS GOAL:
{template['goal']}

RULES:
- Write ONLY the raw SQL query. No markdown, no backticks, no explanation.
- The query MUST work with the exact table and column names shown in the schema above.
- If a concept in the goal (e.g. "loyalty points", "location", "discount") does not exist in this schema, write a simplified version that uses what IS available.
- If the goal requires joining tables that don't exist in this schema, use only the tables that do exist.
- Never use DROP, DELETE, INSERT, UPDATE or any mutating statement.
- Use ROUND() for decimal values.
- Always include a meaningful ORDER BY clause.

SQL:"""


def build_autodetect_prompt(schema_text: str, sample_text: str) -> str:
    return f"""You are an expert MySQL analyst. Look at this database schema and identify the best columns for time-series analysis.

DATABASE SCHEMA:
{schema_text}

SAMPLE DATA:
{sample_text}

Find:
1. The best DATE or DATETIME column to use as a time axis for forecasting (e.g. created_at, order_date, invoice_date)
2. The best NUMERIC column to forecast (e.g. total_amount, revenue, price — should be a monetary or quantity value)
3. The table those columns belong to

Return ONLY a JSON object with these exact keys, no other text:
{{
  "forecast_table": "table_name",
  "forecast_date_col": "column_name",
  "forecast_value_col": "column_name",
  "anomaly_table": "table_name",
  "anomaly_date_col": "column_name",
  "anomaly_value_col": "column_name"
}}"""


def build_catalogue_prompt(schema_text: str, sample_text: str, successful_ids: List[str]) -> str:
    return f"""You are a data analytics expert. Analyse this MySQL database schema and sample data.

DATABASE SCHEMA:
{schema_text}

SAMPLE DATA:
{sample_text}

The following analytics have already been prepared and are available:
{json.dumps(successful_ids)}

Return a JSON array describing ALL of these analytics plus any additional ones that are possible given this specific schema. Each item must have:
- id (use the same ids from the list above where applicable, snake_case for new ones)
- title (short, clear)
- description (one sentence)
- category (Revenue | Products | Customers | Employees | Payments | Growth | Inventory | Other)
- icon (single emoji)
- complexity (simple | medium | advanced)
- tables_used (array of actual table names from the schema)
- insight (one-line business value)

Return ONLY the JSON array, no other text."""


# ── SQL cleaner ────────────────────────────────────────────────────────────────

def clean_sql(raw: str) -> str:
    """Strip markdown fences and whitespace from LLM SQL output."""
    sql = raw.strip()
    sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"```\s*", "", sql)
    sql = sql.strip()
    # Remove any leading explanation lines (lines not starting with SQL keywords)
    lines = sql.split("\n")
    sql_keywords = {"select", "with", "explain", "show", "--", "/*"}
    start = 0
    for i, line in enumerate(lines):
        if any(line.strip().lower().startswith(k) for k in sql_keywords):
            start = i
            break
    return "\n".join(lines[start:]).strip()


# ── SQL validator ──────────────────────────────────────────────────────────────

def validate_sql(conn, sql: str) -> Tuple[bool, str]:
    """Try EXPLAIN on the SQL. Returns (ok, error_message)."""
    try:
        cursor = conn.cursor()
        cursor.execute(f"EXPLAIN {sql}")
        cursor.fetchall()
        return True, ""
    except Exception as e:
        return False, str(e)


# ── Main builder ───────────────────────────────────────────────────────────────

def build_schema_cache(
    conn,
    schemas: Dict,
    fkeys: List,
    samples: Dict,
    llm_caller,           # callable(prompt, system, llm, max_tokens) -> str
    llm: str = "gemini",
    progress_callback=None  # optional callable(message: str)
) -> Dict:
    """
    The big one-time build. Generates ALL SQL for this schema.
    Returns the full cache dict ready to be saved.
    """

    def log(msg):
        if progress_callback:
            progress_callback(msg)

    schema_text = schema_to_text(schemas, fkeys)

    # Build sample text
    sample_text = ""
    for table, info in samples.items():
        cols = ", ".join(info["columns"])
        sample_text += f"\nTable `{table}` (columns: {cols})\n"
        for row in info["rows"][:2]:
            sample_text += f"  {dict(zip(info['columns'], row))}\n"

    log("Detecting best time-series columns…")
    auto_columns = _detect_auto_columns(schema_text, sample_text, llm_caller, llm)

    log(f"Generating SQL for {len(TEMPLATE_GOALS)} analytics templates…")
    template_sql = {}
    template_errors = {}
    successful_ids = []

    for i, template in enumerate(TEMPLATE_GOALS):
        tid = template["id"]
        log(f"  [{i+1}/{len(TEMPLATE_GOALS)}] {template['title']}…")
        sql, err = _generate_and_validate_sql(
            conn, schema_text, sample_text, template, llm_caller, llm
        )
        if sql:
            template_sql[tid] = sql
            successful_ids.append(tid)
        else:
            template_errors[tid] = err
            log(f"    ⚠ Failed: {err[:80]}")

    log(f"Generated SQL for {len(successful_ids)}/{len(TEMPLATE_GOALS)} templates.")
    log("Building analytics catalogue…")
    catalogue = _build_catalogue(
        schema_text, sample_text, successful_ids, llm_caller, llm
    )

    return {
        "schema_text": schema_text,
        "catalogue": catalogue,
        "template_sql": template_sql,
        "template_errors": template_errors,
        "auto_columns": auto_columns,
        "successful_count": len(successful_ids),
        "failed_count": len(template_errors),
    }


def _generate_and_validate_sql(
    conn, schema_text, sample_text, template, llm_caller, llm
) -> Tuple[Optional[str], str]:
    """Generate SQL for one template, validate it, retry once on failure."""
    for attempt in range(2):
        try:
            prompt = build_sql_generation_prompt(schema_text, sample_text, template)
            raw = llm_caller(prompt, "", llm, 800)
            sql = clean_sql(raw)
            if not sql.lower().startswith("select") and not sql.lower().startswith("with"):
                raise ValueError(f"LLM returned non-SELECT: {sql[:80]}")
            ok, err = validate_sql(conn, sql)
            if ok:
                return sql, ""
            # On first attempt failure, enrich the prompt with the error
            if attempt == 0:
                template = dict(template)
                template["goal"] = (
                    f"{template['goal']}\n\nPrevious attempt failed with: {err}\n"
                    "Fix the query to match the exact column names in the schema."
                )
        except Exception as e:
            if attempt == 1:
                return None, str(e)
    return None, "Max retries exceeded"


def _detect_auto_columns(schema_text, sample_text, llm_caller, llm) -> Dict:
    """Ask LLM to identify best date and value columns for auto-forecast/anomaly."""
    defaults = {
        "forecast_table": None, "forecast_date_col": None, "forecast_value_col": None,
        "anomaly_table": None,  "anomaly_date_col": None,  "anomaly_value_col": None,
    }
    try:
        prompt = build_autodetect_prompt(schema_text, sample_text)
        raw = llm_caller(prompt, "", llm, 400)
        raw = raw.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw[start:end])
            defaults.update(result)
    except Exception:
        pass
    return defaults


def _build_catalogue(schema_text, sample_text, successful_ids, llm_caller, llm) -> List[Dict]:
    """Build the full analytics catalogue using LLM, fall back to built-in list."""
    try:
        prompt = build_catalogue_prompt(schema_text, sample_text, successful_ids)
        raw = llm_caller(prompt, "", llm, 3000)
        raw = raw.strip()
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
            if parsed:
                return parsed
    except Exception:
        pass

    # Fallback: build catalogue from our template goals (only successful ones)
    successful_set = set(successful_ids)
    return [
        {
            "id": t["id"],
            "title": t["title"],
            "description": t["description"],
            "category": t["category"],
            "icon": t["icon"],
            "complexity": t["complexity"],
            "tables_used": [],
            "insight": t["insight"],
        }
        for t in TEMPLATE_GOALS
        if t["id"] in successful_set
    ]
