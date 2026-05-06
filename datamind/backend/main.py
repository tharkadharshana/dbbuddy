from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import os
from dotenv import load_dotenv

from db import get_connection, get_table_schemas, get_foreign_keys, get_sample_data, schema_to_text
from llm import query_to_sql, generate_report_summary, discover_analytics
from analytics import (
    run_forecast, run_anomaly_detection,
    run_cohort_analysis, run_rfm_analysis,
    run_basket_analysis, run_growth_metrics,
    run_employee_performance, run_product_velocity,
    run_payment_breakdown, run_location_comparison
)
from auth import (
    create_user, authenticate_user, create_token,
    get_user_settings, update_user_settings, current_user
)

load_dotenv()

app = FastAPI(title="DataMind AI", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helper: resolve DB config & API keys from user settings ──────────────────

def _resolve_db(user: dict) -> dict:
    """Pick the active DB config from user settings, or fall back to env."""
    s = user.get("settings", {})
    configs = s.get("db_configs", [])
    idx = s.get("active_db_index", 0)
    if configs and 0 <= idx < len(configs):
        return configs[idx]
    return {}   # fallback → env vars


def _resolve_llm_keys(user: dict, llm: str) -> str:
    """Inject user's API key into env before LLM call."""
    s = user.get("settings", {})
    if llm == "gemini" and s.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = s["gemini_api_key"]
    elif llm == "deepseek" and s.get("deepseek_api_key"):
        os.environ["DEEPSEEK_API_KEY"] = s["deepseek_api_key"]
    return llm


# ══════════════════════════════════════════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/auth/register")
def register(req: RegisterRequest):
    user = create_user(req.name, req.email, req.password)
    token = create_token(req.email)
    return {"token": token, "user": {"name": user["name"], "email": user["email"]}}


@app.post("/auth/login")
def login(req: LoginRequest):
    user = authenticate_user(req.email, req.password)
    token = create_token(req.email)
    return {"token": token, "user": {"name": user["name"], "email": user["email"]}}


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {"name": user["name"], "email": user["email"]}


# ══════════════════════════════════════════════════════════════════════════════
# USER SETTINGS ROUTES
# ══════════════════════════════════════════════════════════════════════════════

class SettingsPatch(BaseModel):
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    default_llm: Optional[str] = None
    theme: Optional[str] = None
    active_db_index: Optional[int] = None


class DBConfig(BaseModel):
    name: str           # friendly label e.g. "Production DB"
    host: str
    port: int = 3306
    database: str
    user: str
    password: str


@app.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    # Mask passwords in returned DB configs
    safe_configs = []
    for cfg in s.get("db_configs", []):
        c = dict(cfg)
        if c.get("password"):
            c["password"] = "••••••••"
        safe_configs.append(c)
    return {**s, "db_configs": safe_configs}


@app.patch("/settings")
def patch_settings(req: SettingsPatch, user: dict = Depends(current_user)):
    patch = {k: v for k, v in req.dict().items() if v is not None}
    updated = update_user_settings(user["email"], patch)
    return {"ok": True, "settings": updated}


@app.post("/settings/db")
def add_db_config(cfg: DBConfig, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    configs.append(cfg.dict())
    update_user_settings(user["email"], {"db_configs": configs})
    return {"ok": True, "db_configs_count": len(configs)}


@app.put("/settings/db/{index}")
def update_db_config(index: int, cfg: DBConfig, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="DB config index out of range")
    configs[index] = cfg.dict()
    update_user_settings(user["email"], {"db_configs": configs})
    return {"ok": True}


@app.delete("/settings/db/{index}")
def delete_db_config(index: int, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="DB config index out of range")
    configs.pop(index)
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": 0})
    return {"ok": True}


@app.post("/settings/db/{index}/activate")
def activate_db(index: int, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="DB config index out of range")
    update_user_settings(user["email"], {"active_db_index": index})
    return {"ok": True, "active": index}


@app.post("/settings/db/test")
def test_db_connection(cfg: DBConfig, user: dict = Depends(current_user)):
    """Test a DB connection without saving it."""
    try:
        conn = get_connection(cfg.dict())
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        return {"ok": True, "tables": tables, "table_count": len(tables)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# TABLES & SCHEMA
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tables")
def list_tables(user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        schemas = get_table_schemas(conn, tables)
        fkeys = get_foreign_keys(conn)
        conn.close()
        return {"tables": tables, "schemas": schemas, "foreign_keys": fkeys}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# SMART DISCOVERY
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/discover")
def discover(user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [row[0] for row in cursor.fetchall()]
        schemas = get_table_schemas(conn, tables)
        fkeys = get_foreign_keys(conn)
        samples = get_sample_data(conn, tables, rows=3)
        conn.close()
        llm = user.get("settings", {}).get("default_llm", "gemini")
        _resolve_llm_keys(user, llm)
        catalogue = discover_analytics(schemas, fkeys, samples)
        return {"catalogue": catalogue}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# NL QUERY
# ══════════════════════════════════════════════════════════════════════════════

class NLQueryRequest(BaseModel):
    question: str
    llm: str = "gemini"


@app.post("/query")
def natural_language_query(req: NLQueryRequest, user: dict = Depends(current_user)):
    try:
        _resolve_llm_keys(user, req.llm)
        conn = get_connection(_resolve_db(user))
        schemas = get_table_schemas(conn, None)
        fkeys = get_foreign_keys(conn)
        sql = query_to_sql(req.question, schemas, req.llm, fkeys)
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()

        import decimal, datetime
        def safe(v):
            if isinstance(v, decimal.Decimal): return float(v)
            if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
            return v

        data = [{k: safe(v) for k, v in dict(zip(columns, row)).items()} for row in rows]
        return {"sql": sql, "columns": columns, "data": data, "row_count": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsRunRequest(BaseModel):
    template_id: str
    llm: str = "gemini"
    params: Optional[Dict[str, Any]] = {}


@app.post("/analytics/run")
def run_analytics(req: AnalyticsRunRequest, user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        result = _dispatch(req.template_id, conn, req.params or {})
        conn.close()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _safe_val(v):
    import decimal, datetime
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
    return v


def _sql(conn, sql: str, title: str) -> dict:
    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    data = [{c: _safe_val(v) for c, v in zip(cols, row)} for row in rows]
    return {"title": title, "columns": cols, "data": data, "row_count": len(data)}


def _dispatch(tid: str, conn, params: dict):
    if tid == "revenue_trend":
        return _sql(conn, """
            SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month,
                   ROUND(SUM(invoiceTotal),2) as revenue,
                   COUNT(*) as transactions,
                   ROUND(AVG(invoiceTotal),2) as avg_ticket,
                   ROUND(SUM(totalDiscount),2) as total_discounts
            FROM invoices WHERE invoiceDate IS NOT NULL
            GROUP BY month ORDER BY month DESC LIMIT 24""", "Monthly Revenue Trend")
    elif tid == "revenue_by_category":
        return _sql(conn, """
            SELECT p.category,
                   ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue,
                   ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as gross_profit,
                   ROUND(AVG((ii.itemPrice-ii.itemCost)/NULLIF(ii.itemPrice,0)*100),1) as margin_pct,
                   SUM(ii.qty) as units_sold, COUNT(DISTINCT ii.invoiceNumber) as orders
            FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode
            GROUP BY p.category ORDER BY revenue DESC""", "Revenue by Product Category")
    elif tid == "revenue_by_location":
        return _sql(conn, """
            SELECT l.location_name,
                   ROUND(SUM(i.invoiceTotal),2) as revenue,
                   COUNT(*) as transactions,
                   ROUND(AVG(i.invoiceTotal),2) as avg_ticket,
                   COUNT(DISTINCT i.customerId) as unique_customers,
                   ROUND(SUM(i.totalDiscount),2) as discounts_given
            FROM invoices i JOIN locations l ON i.location_id=l.location_id
            WHERE i.invoiceDate IS NOT NULL
            GROUP BY l.location_id,l.location_name ORDER BY revenue DESC""", "Revenue by Location")
    elif tid == "hourly_pattern":
        return _sql(conn, """
            SELECT HOUR(invoiceTime) as hour,
                   ROUND(SUM(invoiceTotal),2) as total_revenue,
                   COUNT(*) as transactions,
                   ROUND(AVG(invoiceTotal),2) as avg_ticket
            FROM invoices WHERE invoiceTime IS NOT NULL
            GROUP BY hour ORDER BY hour""", "Hourly Sales Pattern")
    elif tid == "daily_trend_7":
        return _sql(conn, """
            SELECT invoiceDate as date,
                   ROUND(SUM(invoiceTotal),2) as revenue,
                   COUNT(*) as orders,
                   ROUND(AVG(invoiceTotal),2) as avg_order,
                   COUNT(DISTINCT customerId) as customers
            FROM invoices WHERE invoiceDate >= CURDATE()-INTERVAL 7 DAY
            GROUP BY invoiceDate ORDER BY invoiceDate""", "Last 7 Days")
    elif tid == "discount_analysis":
        return _sql(conn, """
            SELECT CASE WHEN totalDiscount=0 THEN 'No Discount'
                        WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.05 THEN '<5%'
                        WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.10 THEN '5–10%'
                        WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.20 THEN '10–20%'
                        ELSE '>20%' END as discount_band,
                   COUNT(*) as invoices,
                   ROUND(AVG(invoiceTotal),2) as avg_order,
                   ROUND(SUM(invoiceTotal),2) as total_revenue,
                   ROUND(AVG(totalDiscount),2) as avg_discount
            FROM invoices WHERE invoiceTotal>0
            GROUP BY discount_band ORDER BY avg_order DESC""", "Discount Band Analysis")
    elif tid == "top_products":
        return _sql(conn, """
            SELECT p.name, p.category,
                   ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue,
                   SUM(ii.qty) as units,
                   ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as gross_profit,
                   ROUND(AVG((ii.itemPrice-ii.itemCost)/NULLIF(ii.itemPrice,0)*100),1) as margin_pct,
                   COUNT(DISTINCT ii.invoiceNumber) as in_orders
            FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode
            GROUP BY p.itemCode,p.name,p.category ORDER BY revenue DESC LIMIT 20""", "Top 20 Products")
    elif tid == "slow_products":
        return _sql(conn, """
            SELECT p.name, p.category,
                   COALESCE(SUM(ii.qty),0) as total_sold,
                   COALESCE(ROUND(SUM(ii.qty*ii.itemPrice),2),0) as revenue,
                   DATEDIFF(CURDATE(),MAX(i.invoiceDate)) as days_since_sold
            FROM products p
            LEFT JOIN invoice_items ii ON p.itemCode=ii.itemCode
            LEFT JOIN invoices i ON ii.invoiceNumber=i.invoiceNumber
            GROUP BY p.itemCode,p.name,p.category
            HAVING total_sold<10 OR days_since_sold>30 OR days_since_sold IS NULL
            ORDER BY days_since_sold DESC LIMIT 20""", "Slow Moving Products")
    elif tid == "margin_by_category":
        return _sql(conn, """
            SELECT p.category, COUNT(DISTINCT p.itemCode) as skus,
                   ROUND(AVG(p.basePrice),2) as avg_price,
                   ROUND(AVG(p.baseCost),2) as avg_cost,
                   ROUND(AVG((p.basePrice-p.baseCost)/NULLIF(p.basePrice,0)*100),1) as avg_margin_pct,
                   ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as total_profit
            FROM products p LEFT JOIN invoice_items ii ON p.itemCode=ii.itemCode
            GROUP BY p.category ORDER BY total_profit DESC""", "Margin by Category")
    elif tid == "product_velocity":
        return run_product_velocity(conn)
    elif tid == "top_customers":
        return _sql(conn, """
            SELECT c.name, c.email,
                   COUNT(DISTINCT i.invoiceNumber) as orders,
                   ROUND(SUM(i.invoiceTotal),2) as lifetime_value,
                   ROUND(AVG(i.invoiceTotal),2) as avg_order,
                   c.loyaltyPoints, MAX(i.invoiceDate) as last_seen,
                   DATEDIFF(CURDATE(),MAX(i.invoiceDate)) as days_since
            FROM customers c JOIN invoices i ON c.customerId=i.customerId
            GROUP BY c.customerId,c.name,c.email,c.loyaltyPoints
            ORDER BY lifetime_value DESC LIMIT 25""", "Top Customers by LTV")
    elif tid == "customer_rfm":
        return run_rfm_analysis(conn)
    elif tid == "customer_cohort":
        return run_cohort_analysis(conn)
    elif tid == "customer_retention":
        return _sql(conn, """
            SELECT DATE_FORMAT(first_purchase,'%Y-%m') as cohort,
                   COUNT(*) as new_customers,
                   SUM(CASE WHEN total_orders>1 THEN 1 ELSE 0 END) as returned,
                   ROUND(SUM(CASE WHEN total_orders>1 THEN 1 ELSE 0 END)/COUNT(*)*100,1) as retention_pct
            FROM (SELECT customerId,MIN(invoiceDate) as first_purchase,COUNT(*) as total_orders
                  FROM invoices WHERE customerId IS NOT NULL GROUP BY customerId) t
            GROUP BY cohort ORDER BY cohort DESC LIMIT 18""", "Customer Retention by Cohort")
    elif tid == "loyalty_tiers":
        return _sql(conn, """
            SELECT CASE WHEN loyaltyPoints=0 THEN 'No Points'
                        WHEN loyaltyPoints<100 THEN 'Bronze'
                        WHEN loyaltyPoints<500 THEN 'Silver'
                        WHEN loyaltyPoints<1000 THEN 'Gold'
                        ELSE 'Platinum' END as tier,
                   COUNT(*) as customers,
                   ROUND(AVG(sub.total_spent),2) as avg_ltv,
                   ROUND(AVG(sub.avg_order),2) as avg_order
            FROM customers c
            JOIN (SELECT customerId,SUM(invoiceTotal) as total_spent,AVG(invoiceTotal) as avg_order
                  FROM invoices GROUP BY customerId) sub ON c.customerId=sub.customerId
            GROUP BY tier ORDER BY avg_ltv DESC""", "Loyalty Tier Performance")
    elif tid == "cashier_performance":
        return run_employee_performance(conn)
    elif tid == "payment_methods":
        return run_payment_breakdown(conn)
    elif tid == "credit_outstanding":
        return _sql(conn, """
            SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month,
                   COUNT(*) as credit_invoices,
                   ROUND(SUM(creditAmount),2) as credit_given,
                   ROUND(SUM(CASE WHEN creditComplete THEN creditAmount ELSE 0 END),2) as collected,
                   ROUND(SUM(CASE WHEN NOT creditComplete THEN creditAmount ELSE 0 END),2) as outstanding
            FROM invoices WHERE creditAmount>0
            GROUP BY month ORDER BY month DESC LIMIT 12""", "Credit & Collections")
    elif tid == "basket_analysis":
        return run_basket_analysis(conn)
    elif tid == "growth_metrics":
        return run_growth_metrics(conn)
    elif tid == "location_comparison":
        return run_location_comparison(conn)
    elif tid == "order_types":
        return _sql(conn, """
            SELECT orderType, channel,COUNT(*) as orders,
                   ROUND(SUM(invoiceTotal),2) as revenue,
                   ROUND(AVG(invoiceTotal),2) as avg_order
            FROM invoices WHERE orderType IS NOT NULL
            GROUP BY orderType,channel ORDER BY revenue DESC""", "Revenue by Order Type")
    elif tid == "tax_analysis":
        return _sql(conn, """
            SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month,
                   ROUND(SUM(chargeTotalTax),2) as total_tax,
                   ROUND(SUM(invoiceTotal),2) as gross_revenue,
                   ROUND(SUM(chargeTotalTax)/NULLIF(SUM(invoiceTotal),0)*100,2) as effective_tax_rate
            FROM invoices WHERE invoiceDate IS NOT NULL
            GROUP BY month ORDER BY month DESC LIMIT 12""", "Tax Analysis")
    elif tid == "inventory_movement":
        return _sql(conn, """
            SELECT p.name, p.category, il.reason,
                   ROUND(SUM(il.change_qty),2) as net_qty,
                   COUNT(*) as entries, MAX(il.log_date) as last_movement
            FROM inventory_logs il JOIN products p ON il.itemCode=p.itemCode
            GROUP BY p.itemCode,p.name,p.category,il.reason
            ORDER BY ABS(SUM(il.change_qty)) DESC LIMIT 30""", "Inventory Movement")
    else:
        raise ValueError(f"Unknown template: {tid}")


# ══════════════════════════════════════════════════════════════════════════════
# FORECAST
# ══════════════════════════════════════════════════════════════════════════════

class ForecastRequest(BaseModel):
    table: str
    date_column: str
    value_column: str
    periods: int = 90


@app.post("/forecast")
def forecast(req: ForecastRequest, user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute(f"SELECT DATE(`{req.date_column}`) as ds, SUM(`{req.value_column}`) as y FROM `{req.table}` GROUP BY ds ORDER BY ds")
        rows = cursor.fetchall()
        conn.close()
        return run_forecast(rows, req.periods)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/forecast/auto")
def auto_forecast(periods: int = 90, user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute("SELECT DATE(invoiceDate) as ds, SUM(invoiceTotal) as y FROM invoices WHERE invoiceDate IS NOT NULL GROUP BY ds ORDER BY ds")
        rows = cursor.fetchall()
        conn.close()
        return run_forecast(rows, periods)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALIES
# ══════════════════════════════════════════════════════════════════════════════

class AnomalyRequest(BaseModel):
    table: str
    value_column: str
    date_column: Optional[str] = None


@app.post("/anomalies")
def anomalies(req: AnomalyRequest, user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        if req.date_column:
            cursor.execute(f"SELECT `{req.date_column}`, `{req.value_column}` FROM `{req.table}` ORDER BY `{req.date_column}`")
        else:
            cursor.execute(f"SELECT `{req.value_column}` FROM `{req.table}`")
        rows = cursor.fetchall()
        conn.close()
        return run_anomaly_detection(rows, has_date=bool(req.date_column))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/anomalies/auto")
def auto_anomalies(user: dict = Depends(current_user)):
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute("SELECT invoiceDate, SUM(invoiceTotal) FROM invoices WHERE invoiceDate IS NOT NULL GROUP BY invoiceDate ORDER BY invoiceDate")
        rows = cursor.fetchall()
        conn.close()
        return run_anomaly_detection(rows, has_date=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════════════════

class ReportRequest(BaseModel):
    title: str
    sections: List[str]
    llm: str = "gemini"
    format: str = "full"


@app.post("/report")
def generate_report(req: ReportRequest, user: dict = Depends(current_user)):
    try:
        _resolve_llm_keys(user, req.llm)
        conn = get_connection(_resolve_db(user))
        section_data = {}
        for sid in req.sections:
            try:
                section_data[sid] = _dispatch(sid, conn, {})
            except Exception:
                pass

        cursor = conn.cursor()
        cursor.execute("""
            SELECT ROUND(SUM(invoiceTotal),2), COUNT(*), ROUND(AVG(invoiceTotal),2),
                   COUNT(DISTINCT customerId), ROUND(SUM(totalDiscount),2),
                   MIN(invoiceDate), MAX(invoiceDate)
            FROM invoices WHERE invoiceDate IS NOT NULL""")
        row = cursor.fetchone()
        kpis = {}
        if row:
            keys = ["total_revenue","total_invoices","avg_ticket","unique_customers","total_discounts","from_date","to_date"]
            kpis = {k: _safe_val(v) for k, v in zip(keys, row)}
        conn.close()

        narrative = generate_report_summary(
            title=req.title, kpis=kpis,
            section_data=section_data, llm=req.llm, format=req.format
        )
        return {"title": req.title, "kpis": kpis, "sections": section_data, "narrative": narrative}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
