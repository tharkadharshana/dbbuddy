"""
DataMind AI v2 — main.py

Architecture:
  ┌─────────────────────────────────────────────────────────┐
  │  First DB connect                                        │
  │  → schema_builder.build_schema_cache()                  │
  │  → LLM generates SQL for ALL templates once             │
  │  → saved to data/cache/{user}_{db}.json                 │
  └─────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────┐
  │  Every subsequent request                                │
  │  → cache.get_cache() reads from disk (0 LLM tokens)     │
  │  → runs the pre-generated SQL against live DB            │
  └─────────────────────────────────────────────────────────┘
  ┌─────────────────────────────────────────────────────────┐
  │  Fallback (if cache missing or SQL fails)                │
  │  → analytics.py Python functions (hardcoded POS SQL)     │
  │  → only used when dynamic SQL is unavailable             │
  └─────────────────────────────────────────────────────────┘
"""

from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import os, json, decimal, datetime
from dotenv import load_dotenv

from db import get_connection, get_table_schemas, get_foreign_keys, get_sample_data, schema_to_text
from llm import query_to_sql, generate_report_summary, call_llm
from cache import get_cache, save_cache, invalidate_cache, get_cache_status
from schema_builder import build_schema_cache, TEMPLATE_GOALS
from analytics import (
    run_forecast, run_anomaly_detection,
    run_cohort_analysis, run_rfm_analysis,
    run_basket_analysis, run_growth_metrics,
    run_employee_performance, run_product_velocity,
    run_payment_breakdown, run_location_comparison,
)
from auth import (
    create_user, authenticate_user, create_token,
    get_user_settings, update_user_settings, current_user
)

load_dotenv()

app = FastAPI(title="DataMind AI", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory build status tracker (template_id → "building" | "done" | "error")
_build_status: Dict[str, Any] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
    return v


def _resolve_db(user: dict) -> dict:
    s = user.get("settings", {})
    configs = s.get("db_configs", [])
    idx = s.get("active_db_index", 0)
    if configs and 0 <= idx < len(configs):
        return configs[idx]
    return {}


def _resolve_llm_keys(user: dict, llm: str) -> str:
    s = user.get("settings", {})
    if llm == "gemini" and s.get("gemini_api_key"):
        os.environ["GEMINI_API_KEY"] = s["gemini_api_key"]
    elif llm == "deepseek" and s.get("deepseek_api_key"):
        os.environ["DEEPSEEK_API_KEY"] = s["deepseek_api_key"]
    return llm


def _run_sql(conn, sql: str, title: str) -> dict:
    cursor = conn.cursor()
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    data = [{c: _safe(v) for c, v in zip(cols, row)} for row in rows]
    return {"title": title, "columns": cols, "data": data, "row_count": len(data)}


def _build_status_key(email: str, db_config: dict) -> str:
    import hashlib
    key = f"{email}:{db_config.get('host','env')}:{db_config.get('database','env')}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


# ── Background cache builder ───────────────────────────────────────────────────

def _background_build(email: str, db_config: dict, llm: str):
    """Runs in background after DB is connected. Builds and saves the full cache."""
    status_key = _build_status_key(email, db_config)
    _build_status[status_key] = {"status": "building", "progress": [], "started_at": str(datetime.datetime.utcnow())}
    logs = _build_status[status_key]["progress"]

    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [r[0] for r in cursor.fetchall()]
        schemas = get_table_schemas(conn, tables)
        fkeys = get_foreign_keys(conn)
        samples = get_sample_data(conn, tables, rows=3)

        def progress(msg):
            logs.append(msg)

        def llm_caller(prompt, system, llm_name, max_tokens):
            return call_llm(prompt, system, llm_name, max_tokens)

        cache_data = build_schema_cache(
            conn=conn,
            schemas=schemas,
            fkeys=fkeys,
            samples=samples,
            llm_caller=llm_caller,
            llm=llm,
            progress_callback=progress,
        )
        conn.close()
        save_cache(email, db_config, cache_data)
        _build_status[status_key]["status"] = "done"
        _build_status[status_key]["successful"] = cache_data["successful_count"]
        _build_status[status_key]["failed"] = cache_data["failed_count"]
        logs.append(f"✅ Cache built: {cache_data['successful_count']} templates ready.")

    except Exception as e:
        _build_status[status_key]["status"] = "error"
        _build_status[status_key]["error"] = str(e)
        logs.append(f"❌ Build failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# AUTH
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
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class SettingsPatch(BaseModel):
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    default_llm: Optional[str] = None
    theme: Optional[str] = None
    active_db_index: Optional[int] = None


class DBConfig(BaseModel):
    name: str
    host: str
    port: int = 3306
    database: str
    user: str
    password: str


@app.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
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
def add_db_config(cfg: DBConfig, background_tasks: BackgroundTasks, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    configs.append(cfg.dict())
    new_idx = len(configs) - 1
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": new_idx})
    # Trigger background cache build
    llm = s.get("default_llm", "gemini")
    _resolve_llm_keys(user, llm)
    background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm)
    return {"ok": True, "db_configs_count": len(configs), "building_cache": True}


@app.put("/settings/db/{index}")
def update_db_config(index: int, cfg: DBConfig, background_tasks: BackgroundTasks, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    old_cfg = configs[index]
    # Invalidate old cache
    invalidate_cache(user["email"], old_cfg)
    configs[index] = cfg.dict()
    update_user_settings(user["email"], {"db_configs": configs})
    # Rebuild cache for new config
    llm = s.get("default_llm", "gemini")
    _resolve_llm_keys(user, llm)
    background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm)
    return {"ok": True, "building_cache": True}


@app.delete("/settings/db/{index}")
def delete_db_config(index: int, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    invalidate_cache(user["email"], configs[index])
    configs.pop(index)
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": 0})
    return {"ok": True}


@app.post("/settings/db/{index}/activate")
def activate_db(index: int, background_tasks: BackgroundTasks, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    update_user_settings(user["email"], {"active_db_index": index})
    # Build cache if not already cached
    db_config = configs[index]
    if not get_cache(user["email"], db_config):
        llm = s.get("default_llm", "gemini")
        _resolve_llm_keys(user, llm)
        background_tasks.add_task(_background_build, user["email"], db_config, llm)
        return {"ok": True, "active": index, "building_cache": True}
    return {"ok": True, "active": index, "building_cache": False}


@app.post("/settings/db/test")
def test_db_connection(cfg: DBConfig, user: dict = Depends(current_user)):
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
# CACHE STATUS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/cache/status")
def cache_status(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    status_key = _build_status_key(user["email"], db_config)
    build_info = _build_status.get(status_key, {})
    cache_info = get_cache_status(user["email"], db_config)
    return {**cache_info, "build": build_info}


@app.post("/cache/rebuild")
def rebuild_cache(background_tasks: BackgroundTasks, user: dict = Depends(current_user)):
    """Force a full cache rebuild for the active DB."""
    db_config = _resolve_db(user)
    invalidate_cache(user["email"], db_config)
    s = user.get("settings", {})
    llm = s.get("default_llm", "gemini")
    _resolve_llm_keys(user, llm)
    background_tasks.add_task(_background_build, user["email"], db_config, llm)
    return {"ok": True, "message": "Cache rebuild started in background"}


@app.get("/cache/progress")
def cache_progress(user: dict = Depends(current_user)):
    """Poll build progress — used by frontend to show a progress log."""
    db_config = _resolve_db(user)
    status_key = _build_status_key(user["email"], db_config)
    return _build_status.get(status_key, {"status": "unknown"})


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH & TABLES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {"status": "ok", "version": "3.0.0"}


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
# DISCOVER — reads from cache (0 LLM tokens)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/discover")
def discover(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)

    if cache:
        # ✅ Serve from cache — zero LLM tokens
        return {
            "catalogue": cache.get("catalogue", []),
            "from_cache": True,
            "built_at": cache.get("built_at"),
            "template_count": len(cache.get("template_sql", {})),
        }

    # Cache not built yet — return status
    status_key = _build_status_key(user["email"], db_config)
    build_info = _build_status.get(status_key, {})
    if build_info.get("status") == "building":
        return {"catalogue": [], "from_cache": False, "building": True, "progress": build_info.get("progress", [])}

    # No cache, not building — return empty (frontend should trigger a rebuild)
    return {"catalogue": [], "from_cache": False, "building": False, "needs_build": True}


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
        data = [{k: _safe(v) for k, v in dict(zip(columns, row)).items()} for row in rows]
        return {"sql": sql, "columns": columns, "data": data, "row_count": len(data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS TEMPLATES — dynamic from cache, fallback to hardcoded
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsRunRequest(BaseModel):
    template_id: str
    llm: str = "gemini"
    params: Optional[Dict[str, Any]] = {}


@app.post("/analytics/run")
def run_analytics(req: AnalyticsRunRequest, user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    conn = get_connection(db_config)

    try:
        # ── Step 1: Try dynamic SQL from cache ────────────────────────────────
        cache = get_cache(user["email"], db_config)
        if cache and req.template_id in cache.get("template_sql", {}):
            sql = cache["template_sql"][req.template_id]
            # Get title from catalogue
            title = req.template_id.replace("_", " ").title()
            for item in cache.get("catalogue", []):
                if item.get("id") == req.template_id:
                    title = item.get("title", title)
                    break
            try:
                result = _run_sql(conn, sql, title)
                result["source"] = "cache"
                conn.close()
                return result
            except Exception as sql_err:
                # Cached SQL failed (schema changed?) — fall through to hardcoded
                pass

        # ── Step 2: Try Python analytics functions (RFM, Cohort, Basket etc.) ─
        python_result = _try_python_analytics(req.template_id, conn)
        if python_result:
            python_result["source"] = "python"
            conn.close()
            return python_result

        # ── Step 3: Try hardcoded fallback SQL (original POS queries) ─────────
        fallback_result = _hardcoded_fallback(req.template_id, conn)
        if fallback_result:
            fallback_result["source"] = "fallback"
            conn.close()
            return fallback_result

        conn.close()
        raise HTTPException(status_code=404, detail=f"Template '{req.template_id}' not available for this database.")

    except HTTPException:
        raise
    except Exception as e:
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))


def _try_python_analytics(tid: str, conn) -> Optional[Dict]:
    """Run templates that need Python logic (RFM, cohort, basket, etc.)."""
    try:
        if tid == "customer_rfm":       return run_rfm_analysis(conn)
        if tid == "customer_cohort":    return run_cohort_analysis(conn)
        if tid == "basket_analysis":    return run_basket_analysis(conn)
        if tid == "growth_metrics":     return run_growth_metrics(conn)
        if tid == "cashier_performance":return run_employee_performance(conn)
        if tid == "product_velocity":   return run_product_velocity(conn)
        if tid == "payment_methods":    return run_payment_breakdown(conn)
        if tid == "location_comparison":return run_location_comparison(conn)
    except Exception:
        pass
    return None


def _run_sql_safe(conn, sql, title):
    try:
        return _run_sql(conn, sql, title)
    except Exception:
        return None


def _hardcoded_fallback(tid: str, conn) -> Optional[Dict]:
    """Original hardcoded POS SQL — last resort fallback."""
    queries = {
        "revenue_trend": ("SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month, ROUND(SUM(invoiceTotal),2) as revenue, COUNT(*) as transactions, ROUND(AVG(invoiceTotal),2) as avg_ticket, ROUND(SUM(totalDiscount),2) as total_discounts FROM invoices WHERE invoiceDate IS NOT NULL GROUP BY month ORDER BY month DESC LIMIT 24", "Monthly Revenue Trend"),
        "revenue_by_category": ("SELECT p.category, ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue, ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as gross_profit, ROUND(AVG((ii.itemPrice-ii.itemCost)/NULLIF(ii.itemPrice,0)*100),1) as margin_pct, SUM(ii.qty) as units_sold FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode GROUP BY p.category ORDER BY revenue DESC", "Revenue by Category"),
        "revenue_by_location": ("SELECT l.location_name, ROUND(SUM(i.invoiceTotal),2) as revenue, COUNT(*) as transactions, ROUND(AVG(i.invoiceTotal),2) as avg_ticket FROM invoices i JOIN locations l ON i.location_id=l.location_id WHERE i.invoiceDate IS NOT NULL GROUP BY l.location_id,l.location_name ORDER BY revenue DESC", "Revenue by Location"),
        "hourly_pattern": ("SELECT HOUR(invoiceTime) as hour, ROUND(SUM(invoiceTotal),2) as total_revenue, COUNT(*) as transactions FROM invoices WHERE invoiceTime IS NOT NULL GROUP BY hour ORDER BY hour", "Hourly Sales Pattern"),
        "daily_trend_7": ("SELECT invoiceDate as date, ROUND(SUM(invoiceTotal),2) as revenue, COUNT(*) as orders FROM invoices WHERE invoiceDate >= CURDATE()-INTERVAL 7 DAY GROUP BY invoiceDate ORDER BY invoiceDate", "Last 7 Days"),
        "discount_analysis": ("SELECT CASE WHEN totalDiscount=0 THEN 'No Discount' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.05 THEN '<5%' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.10 THEN '5-10%' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.20 THEN '10-20%' ELSE '>20%' END as discount_band, COUNT(*) as invoices, ROUND(AVG(invoiceTotal),2) as avg_order, ROUND(SUM(invoiceTotal),2) as total_revenue FROM invoices WHERE invoiceTotal>0 GROUP BY discount_band ORDER BY avg_order DESC", "Discount Analysis"),
        "top_products": ("SELECT p.name, p.category, ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue, SUM(ii.qty) as units, ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as gross_profit FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode GROUP BY p.itemCode,p.name,p.category ORDER BY revenue DESC LIMIT 20", "Top Products"),
        "slow_products": ("SELECT p.name, p.category, COALESCE(SUM(ii.qty),0) as total_sold, DATEDIFF(CURDATE(),MAX(i.invoiceDate)) as days_since_sold FROM products p LEFT JOIN invoice_items ii ON p.itemCode=ii.itemCode LEFT JOIN invoices i ON ii.invoiceNumber=i.invoiceNumber GROUP BY p.itemCode,p.name,p.category HAVING total_sold<10 OR days_since_sold>30 OR days_since_sold IS NULL ORDER BY days_since_sold DESC LIMIT 20", "Slow Products"),
        "margin_by_category": ("SELECT p.category, COUNT(DISTINCT p.itemCode) as skus, ROUND(AVG((p.basePrice-p.baseCost)/NULLIF(p.basePrice,0)*100),1) as avg_margin_pct, ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as total_profit FROM products p LEFT JOIN invoice_items ii ON p.itemCode=ii.itemCode GROUP BY p.category ORDER BY total_profit DESC", "Margin by Category"),
        "top_customers": ("SELECT c.name, COUNT(DISTINCT i.invoiceNumber) as orders, ROUND(SUM(i.invoiceTotal),2) as lifetime_value, ROUND(AVG(i.invoiceTotal),2) as avg_order, c.loyaltyPoints, MAX(i.invoiceDate) as last_seen FROM customers c JOIN invoices i ON c.customerId=i.customerId GROUP BY c.customerId,c.name,c.loyaltyPoints ORDER BY lifetime_value DESC LIMIT 25", "Top Customers"),
        "customer_retention": ("SELECT DATE_FORMAT(first_purchase,'%Y-%m') as cohort, COUNT(*) as new_customers, ROUND(SUM(CASE WHEN total_orders>1 THEN 1 ELSE 0 END)/COUNT(*)*100,1) as retention_pct FROM (SELECT customerId,MIN(invoiceDate) as first_purchase,COUNT(*) as total_orders FROM invoices WHERE customerId IS NOT NULL GROUP BY customerId) t GROUP BY cohort ORDER BY cohort DESC LIMIT 18", "Customer Retention"),
        "loyalty_tiers": ("SELECT CASE WHEN loyaltyPoints=0 THEN 'No Points' WHEN loyaltyPoints<100 THEN 'Bronze' WHEN loyaltyPoints<500 THEN 'Silver' WHEN loyaltyPoints<1000 THEN 'Gold' ELSE 'Platinum' END as tier, COUNT(*) as customers, ROUND(AVG(sub.total_spent),2) as avg_ltv FROM customers c JOIN (SELECT customerId,SUM(invoiceTotal) as total_spent FROM invoices GROUP BY customerId) sub ON c.customerId=sub.customerId GROUP BY tier ORDER BY avg_ltv DESC", "Loyalty Tiers"),
        "payment_methods": ("SELECT payMethod as payment_method, COUNT(*) as transactions, ROUND(SUM(invoiceTotal),2) as revenue, ROUND(AVG(invoiceTotal),2) as avg_ticket FROM invoices WHERE payMethod IS NOT NULL GROUP BY payMethod ORDER BY revenue DESC", "Payment Methods"),
        "credit_outstanding": ("SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month, COUNT(*) as credit_invoices, ROUND(SUM(creditAmount),2) as credit_given, ROUND(SUM(CASE WHEN creditComplete THEN creditAmount ELSE 0 END),2) as collected, ROUND(SUM(CASE WHEN NOT creditComplete THEN creditAmount ELSE 0 END),2) as outstanding FROM invoices WHERE creditAmount>0 GROUP BY month ORDER BY month DESC LIMIT 12", "Credit & Outstanding"),
        "order_types": ("SELECT orderType, COUNT(*) as orders, ROUND(SUM(invoiceTotal),2) as revenue, ROUND(AVG(invoiceTotal),2) as avg_order FROM invoices WHERE orderType IS NOT NULL GROUP BY orderType ORDER BY revenue DESC", "Order Types"),
        "tax_analysis": ("SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month, ROUND(SUM(chargeTotalTax),2) as total_tax, ROUND(SUM(invoiceTotal),2) as gross_revenue FROM invoices WHERE invoiceDate IS NOT NULL GROUP BY month ORDER BY month DESC LIMIT 12", "Tax Analysis"),
        "inventory_movement": ("SELECT p.name, p.category, il.reason, ROUND(SUM(il.change_qty),2) as net_qty, COUNT(*) as entries FROM inventory_logs il JOIN products p ON il.itemCode=p.itemCode GROUP BY p.itemCode,p.name,p.category,il.reason ORDER BY ABS(SUM(il.change_qty)) DESC LIMIT 30", "Inventory Movement"),
    }
    if tid not in queries:
        return None
    sql, title = queries[tid]
    return _run_sql_safe(conn, sql, title)


# ══════════════════════════════════════════════════════════════════════════════
# FORECAST — uses cached auto-columns, or manual, or env fallback
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
        cursor.execute(f"SELECT DATE(`{req.date_column}`) as ds, SUM(`{req.value_column}`) as y FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL GROUP BY ds ORDER BY ds")
        rows = cursor.fetchall()
        conn.close()
        return run_forecast(rows, req.periods)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/forecast/auto")
def auto_forecast(periods: int = 90, user: dict = Depends(current_user)):
    """Auto-detect best columns from cache, fall back to env-based defaults."""
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    auto = cache.get("auto_columns", {}) if cache else {}

    table  = auto.get("forecast_table")  or "invoices"
    d_col  = auto.get("forecast_date_col")  or "invoiceDate"
    v_col  = auto.get("forecast_value_col") or "invoiceTotal"

    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute(f"SELECT DATE(`{d_col}`) as ds, SUM(`{v_col}`) as y FROM `{table}` WHERE `{d_col}` IS NOT NULL GROUP BY ds ORDER BY ds")
        rows = cursor.fetchall()
        conn.close()
        result = run_forecast(rows, periods)
        result["used_table"] = table
        result["used_date_col"] = d_col
        result["used_value_col"] = v_col
        result["from_cache"] = bool(cache and auto.get("forecast_table"))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALIES — same pattern as forecast
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
            cursor.execute(f"SELECT `{req.date_column}`, `{req.value_column}` FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL ORDER BY `{req.date_column}`")
        else:
            cursor.execute(f"SELECT `{req.value_column}` FROM `{req.table}`")
        rows = cursor.fetchall()
        conn.close()
        return run_anomaly_detection(rows, has_date=bool(req.date_column))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/anomalies/auto")
def auto_anomalies(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    auto = cache.get("auto_columns", {}) if cache else {}

    table  = auto.get("anomaly_table")     or "invoices"
    d_col  = auto.get("anomaly_date_col")  or "invoiceDate"
    v_col  = auto.get("anomaly_value_col") or "invoiceTotal"

    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute(f"SELECT `{d_col}`, SUM(`{v_col}`) FROM `{table}` WHERE `{d_col}` IS NOT NULL GROUP BY `{d_col}` ORDER BY `{d_col}`")
        rows = cursor.fetchall()
        conn.close()
        result = run_anomaly_detection(rows, has_date=True)
        result["used_table"] = table
        result["used_date_col"] = d_col
        result["used_value_col"] = v_col
        result["from_cache"] = bool(cache and auto.get("anomaly_table"))
        return result
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
        db_config = _resolve_db(user)
        conn = get_connection(db_config)
        cache = get_cache(user["email"], db_config)

        section_data = {}
        for sid in req.sections:
            try:
                # Try cache first, then python, then fallback
                result = None
                if cache and sid in cache.get("template_sql", {}):
                    try:
                        sql = cache["template_sql"][sid]
                        title = sid.replace("_", " ").title()
                        for item in cache.get("catalogue", []):
                            if item.get("id") == sid:
                                title = item.get("title", title)
                                break
                        result = _run_sql(conn, sql, title)
                        result["source"] = "cache"
                    except Exception:
                        result = None
                if not result:
                    result = _try_python_analytics(sid, conn)
                    if result:
                        result["source"] = "python"
                if not result:
                    result = _hardcoded_fallback(sid, conn)
                    if result:
                        result["source"] = "fallback"
                if result:
                    section_data[sid] = result
            except Exception:
                pass

        # Get overall KPIs — try to find date+amount columns dynamically
        kpis = _get_kpis(conn, user, cache)
        conn.close()

        narrative = generate_report_summary(
            title=req.title, kpis=kpis,
            section_data=section_data, llm=req.llm, format=req.format
        )
        return {"title": req.title, "kpis": kpis, "sections": section_data, "narrative": narrative}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _get_kpis(conn, user, cache) -> Dict:
    """Try to get global KPIs using cached auto-columns, fall back to invoices."""
    auto = cache.get("auto_columns", {}) if cache else {}
    table = auto.get("forecast_table") or "invoices"
    d_col = auto.get("forecast_date_col") or "invoiceDate"
    v_col = auto.get("forecast_value_col") or "invoiceTotal"

    try:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT ROUND(SUM(`{v_col}`),2), COUNT(*), ROUND(AVG(`{v_col}`),2),
                   MIN(`{d_col}`), MAX(`{d_col}`)
            FROM `{table}` WHERE `{d_col}` IS NOT NULL
        """)
        row = cursor.fetchone()
        if row:
            keys = ["total_revenue", "total_transactions", "avg_transaction", "from_date", "to_date"]
            return {k: _safe(v) for k, v in zip(keys, row)}
    except Exception:
        pass
    return {}
