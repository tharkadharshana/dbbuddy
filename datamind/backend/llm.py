import os
import json
import requests
from typing import Dict, Any, List
from db import schema_to_text


def call_gemini(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY not set in .env")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": f"{system}\n\n{prompt}" if system else prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": max_tokens}
    }
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, timeout=90)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        except requests.exceptions.Timeout:
            if attempt == 0: continue
            raise Exception("Gemini API timed out.")
        except Exception as e:
            raise e


def call_deepseek(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key == "your_deepseek_api_key_here":
        raise ValueError("DEEPSEEK_API_KEY not set in .env")
    url = "https://api.deepseek.com/v1/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": "deepseek-chat", "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    for attempt in range(2):
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.Timeout:
            if attempt == 0: continue
            raise Exception("DeepSeek API timed out.")
        except Exception as e:
            raise e


def call_llm(prompt: str, system: str = "", llm: str = "gemini", max_tokens: int = 2000) -> str:
    if str(llm).lower() == "deepseek":
        return call_deepseek(prompt, system, max_tokens)
    return call_gemini(prompt, system, max_tokens)


def query_to_sql(question: str, schemas: Dict[str, Any], llm: str = "gemini", fkeys: list = None) -> str:
    schema_text = schema_to_text(schemas, fkeys)
    system = (
        "You are an expert MySQL query writer. "
        "Given a database schema (with foreign key relationships) and a plain English question, "
        "write a valid MySQL SELECT query that may JOIN multiple tables as needed. "
        "Return ONLY the raw SQL — no markdown, no backticks, no explanation. "
        "Never use DROP, DELETE, INSERT, UPDATE, or any mutating statement."
    )
    prompt = f"Schema:\n{schema_text}\n\nQuestion: {question}\n\nSQL:"
    raw = call_llm(prompt, system, llm)
    return raw.replace("```sql", "").replace("```", "").strip()


def discover_analytics(schemas: Dict, fkeys: list, samples: Dict) -> List[Dict]:
    """
    Ask the LLM to analyse the schema + sample data and return a structured catalogue
    of all analytics & predictions that are possible.
    Falls back to a hardcoded catalogue if LLM call fails.
    """
    schema_text = schema_to_text(schemas, fkeys)
    sample_text = ""
    for table, info in samples.items():
        cols = ", ".join(info["columns"])
        sample_text += f"\nTable `{table}` columns: {cols}\n"
        if info["rows"]:
            sample_text += f"  Sample: {info['rows'][0]}\n"

    system = (
        "You are a data analytics expert. Analyse this MySQL database schema and sample data. "
        "Return a JSON array of analytics templates that can be run on this data. "
        "Each item must have: id (snake_case), title, description, category, "
        "icon (emoji), complexity (simple|medium|advanced), "
        "tables_used (array), insight (one-line value proposition). "
        "Include 20-25 diverse items covering revenue, products, customers, employees, "
        "payments, inventory, forecasting, anomaly detection, cohort analysis, basket analysis, growth. "
        "Return ONLY the JSON array, no other text."
    )
    prompt = f"Database Schema:\n{schema_text}\n\nSample Data:{sample_text}\n\nReturn JSON array of analytics templates:"

    try:
        raw = call_llm(prompt, system, "gemini", max_tokens=3000)
        raw = raw.replace("```json", "").replace("```", "").strip()
        # Find the JSON array
        start = raw.find("[")
        end = raw.rfind("]") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
    except Exception:
        pass

    # Fallback hardcoded catalogue
    return _fallback_catalogue()


def _fallback_catalogue() -> List[Dict]:
    return [
        {"id":"revenue_trend","title":"Monthly Revenue Trend","description":"Track revenue, transaction count, and average ticket size month by month","category":"Revenue","icon":"📈","complexity":"simple","tables_used":["invoices"],"insight":"Identify growth months and seasonal patterns"},
        {"id":"revenue_by_category","title":"Revenue by Product Category","description":"Break down sales, gross profit, and margin by product category","category":"Revenue","icon":"🏷️","complexity":"simple","tables_used":["invoice_items","products"],"insight":"See which categories drive profitability"},
        {"id":"revenue_by_location","title":"Revenue by Location","description":"Compare sales performance across all store locations","category":"Revenue","icon":"📍","complexity":"simple","tables_used":["invoices","locations"],"insight":"Find your highest and lowest performing locations"},
        {"id":"hourly_pattern","title":"Hourly Sales Pattern","description":"Discover peak trading hours to optimise staffing and promotions","category":"Revenue","icon":"🕐","complexity":"simple","tables_used":["invoices"],"insight":"Staff smartly — know your rush hours"},
        {"id":"daily_trend_7","title":"Last 7 Days Performance","description":"Daily revenue, orders, and customer counts for the past week","category":"Revenue","icon":"📅","complexity":"simple","tables_used":["invoices"],"insight":"Spot recent dips or spikes immediately"},
        {"id":"discount_analysis","title":"Discount Impact Analysis","description":"See how discount levels affect average order value and total revenue","category":"Revenue","icon":"🏷️","complexity":"medium","tables_used":["invoices"],"insight":"Are discounts driving revenue or eroding it?"},
        {"id":"tax_analysis","title":"Tax Analysis","description":"Monthly tax collected vs effective tax rate","category":"Revenue","icon":"🧾","complexity":"simple","tables_used":["invoices"],"insight":"Stay on top of tax obligations"},
        {"id":"top_products","title":"Top Products by Revenue","description":"Rank all products by revenue, units sold, and profit margin","category":"Products","icon":"🥇","complexity":"simple","tables_used":["invoice_items","products"],"insight":"Double down on your bestsellers"},
        {"id":"slow_products","title":"Slow-Moving Products","description":"Flag products with low sales velocity or not sold recently","category":"Products","icon":"🐌","complexity":"medium","tables_used":["invoice_items","products","invoices"],"insight":"Clear deadstock before it becomes a loss"},
        {"id":"margin_by_category","title":"Margin Analysis by Category","description":"Compare gross margin percentage across product categories","category":"Products","icon":"💰","complexity":"medium","tables_used":["products","invoice_items"],"insight":"Know where you make — and lose — money"},
        {"id":"product_velocity","title":"Product Sales Velocity","description":"Rank products by speed of sale — units per day","category":"Products","icon":"⚡","complexity":"medium","tables_used":["invoice_items","invoices","products"],"insight":"Identify fast movers to prioritise restocking"},
        {"id":"basket_analysis","title":"Basket / Co-purchase Analysis","description":"Discover which products are frequently bought together","category":"Products","icon":"🛒","complexity":"advanced","tables_used":["invoice_items","products"],"insight":"Power up upsell and bundle strategies"},
        {"id":"top_customers","title":"Top Customers by LTV","description":"Rank customers by lifetime value, order frequency, and recency","category":"Customers","icon":"👑","complexity":"simple","tables_used":["customers","invoices"],"insight":"Know your most valuable relationships"},
        {"id":"customer_rfm","title":"RFM Customer Segmentation","description":"Segment customers into Champions, Loyal, At-Risk, Lost etc.","category":"Customers","icon":"🎯","complexity":"advanced","tables_used":["invoices","customers"],"insight":"Target the right message to the right segment"},
        {"id":"customer_cohort","title":"Cohort Retention Analysis","description":"Track how well each acquisition cohort retains over time","category":"Customers","icon":"🔄","complexity":"advanced","tables_used":["invoices","customers"],"insight":"Measure the true health of customer loyalty"},
        {"id":"customer_retention","title":"Monthly Retention Rate","description":"What % of new customers come back in subsequent months","category":"Customers","icon":"🔁","complexity":"medium","tables_used":["invoices"],"insight":"A rising retention rate means a healthy business"},
        {"id":"loyalty_tiers","title":"Loyalty Tier Performance","description":"Compare spend and order frequency across loyalty point tiers","category":"Customers","icon":"⭐","complexity":"medium","tables_used":["customers","invoices"],"insight":"Is your loyalty programme driving revenue?"},
        {"id":"cashier_performance","title":"Cashier / Staff Performance","description":"Rank employees by sales volume, transaction count, and avg ticket","category":"Employees","icon":"👤","complexity":"medium","tables_used":["invoices","employees","locations"],"insight":"Reward top performers and coach the rest"},
        {"id":"payment_methods","title":"Payment Method Breakdown","description":"Revenue split by cash, card, credit, and digital payments","category":"Payments","icon":"💳","complexity":"simple","tables_used":["invoices"],"insight":"Understand how customers prefer to pay"},
        {"id":"credit_outstanding","title":"Credit & Outstanding Balance","description":"Track credit invoices, collections, and outstanding amounts by month","category":"Payments","icon":"⚠️","complexity":"medium","tables_used":["invoices"],"insight":"Stay on top of your receivables"},
        {"id":"growth_metrics","title":"Month-over-Month Growth","description":"Calculate MoM revenue growth, transaction growth, and AOV trends","category":"Growth","icon":"🚀","complexity":"medium","tables_used":["invoices"],"insight":"Quantify whether the business is accelerating"},
        {"id":"location_comparison","title":"Location KPI Comparison","description":"Side-by-side KPI comparison of all locations","category":"Growth","icon":"🗺️","complexity":"medium","tables_used":["invoices","locations"],"insight":"Benchmark locations against each other"},
        {"id":"order_types","title":"Revenue by Order Type & Channel","description":"Dine-in vs takeaway vs delivery vs online revenue split","category":"Growth","icon":"📦","complexity":"simple","tables_used":["invoices"],"insight":"Know which channel to invest in"},
        {"id":"inventory_movement","title":"Inventory Movement Log","description":"Track stock changes by product, reason (sale/restock/damage)","category":"Inventory","icon":"📦","complexity":"medium","tables_used":["inventory_logs","products"],"insight":"Prevent stockouts and identify waste"},
    ]


def generate_report_summary(title: str, kpis: Dict, section_data: Dict, llm: str, format: str = "full") -> str:
    kpi_text = "\n".join(f"  {k}: {v}" for k, v in kpis.items())
    sections_text = ""
    for sid, data in section_data.items():
        sections_text += f"\n\n## {data.get('title', sid)}\n"
        cols = data.get("columns", [])
        rows = data.get("data", [])[:5]
        if rows:
            sections_text += f"Columns: {', '.join(cols)}\n"
            sections_text += "Top rows:\n"
            for row in rows:
                sections_text += f"  {row}\n"

    if format == "executive":
        length_instruction = "Write 3-4 concise paragraphs (executive summary style). Focus on the 3 most important findings and one strategic recommendation."
    elif format == "quick":
        length_instruction = "Write 5-7 bullet points covering key findings. Be very concise."
    else:
        length_instruction = "Write a detailed professional report with 5-7 paragraphs covering: overview, key findings per section, risks, opportunities, and strategic recommendations."

    system = (
        "You are a senior business analyst writing a professional analytics report. "
        "Use concrete numbers from the data. Be direct and actionable. "
        "Structure your writing with clear topic sentences. "
        "Do not invent data — only reference what is provided."
    )
    prompt = (
        f"Report Title: {title}\n\n"
        f"Business KPIs:\n{kpi_text}\n\n"
        f"Data Sections:{sections_text}\n\n"
        f"Instructions: {length_instruction}"
    )
    return call_llm(prompt, system, llm, max_tokens=2500)
