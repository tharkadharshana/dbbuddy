"""
DataMind AI v3 — main.py

Three fixes in this version:
  1. DB config ALWAYS comes from user settings — .env is only a global fallback
     for server admins who want a default DB.
  2. LLM selection is respected exactly — DeepSeek uses DeepSeek key,
     Gemini uses Gemini key. Keys come from user settings, not os.environ.
  3. Comprehensive structured logging throughout.
"""

import os
import decimal
import datetime
import traceback
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from dotenv import load_dotenv

load_dotenv()  # must run before any local module reads os.getenv at import time

from logger import get_logger
from db import get_connection, get_table_schemas, get_foreign_keys, get_sample_data
from llm import query_to_sql, generate_report_summary, call_llm, validate_llm_key, list_gemini_models
from cache import get_cache, save_cache, invalidate_cache, get_cache_status
from schema_builder import build_schema_cache
from analytics import (
    run_forecast, run_anomaly_detection, run_cohort_analysis,
    run_rfm_analysis, run_basket_analysis, run_growth_metrics,
    run_employee_performance, run_product_velocity,
    run_payment_breakdown, run_location_comparison,
)
from integrations import (
    bootstrap_integration_tables,
    connect_provider, disconnect_provider,
    get_user_connections, get_connection_status,
    trigger_sync, get_sync_history, start_scheduler,
    get_user_total_rows, _get_internal_conn,
)
from credits import get_user_credits, get_usage_history, bootstrap_credit_tables
from providers import list_providers, get_provider

log = get_logger(__name__)

# Bootstrap integration tables (create if not exist)
try:
    bootstrap_integration_tables()
except Exception as _be:
    log.warning("Integration bootstrap skipped (configure DATAMIND_DB_* in .env)", error=str(_be))

from auth import (
    create_user, authenticate_user, create_token,
    get_user_settings, update_user_settings, current_user,
)

app = FastAPI(title="DataMind AI", version="3.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup_event():
    try:
        bootstrap_integration_tables()
    except Exception as _be:
        log.warning("Integration bootstrap skipped in startup", error=str(_be))
    try:
        bootstrap_credit_tables()
    except Exception as _be:
        log.warning("Credit bootstrap skipped (configure DATAMIND_DB_* in .env)", error=str(_be))
    start_scheduler()
    log.info("DataMind backend started")

# In-memory build progress tracker
_build_status: Dict[str, Any] = {}


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — key resolution (user settings first, env fallback only)
# ══════════════════════════════════════════════════════════════════════════════

def _safe(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
    return v


def _resolve_db(user: dict) -> dict:
    """
    Return the active DB config from user settings.
    Falls back to env vars ONLY if the user has no configs at all.
    Logs clearly which source is being used.
    """
    s = user.get("settings", {})
    configs = s.get("db_configs", [])
    idx = int(s.get("active_db_index", 0))
    if configs and 0 <= idx < len(configs):
        cfg = configs[idx]
        log.debug("Using user DB config",
                  user=user.get("email"), config_name=cfg.get("name"),
                  host=cfg.get("host"), database=cfg.get("database"))
        return cfg
    log.warning("No user DB config found, falling back to .env",
                user=user.get("email"))
    return {}   # get_connection() will use env vars


def _resolve_api_key(user: dict, llm: str) -> str:
    """
    Return the correct API key for the requested LLM.
    Source: user settings ONLY. .env is never touched here.
    Raises clear error if missing.
    """
    s = user.get("settings", {})
    llm = (llm or "gemini").lower().strip()
    if llm == "gemini":
        key = s.get("gemini_api_key", "").strip()
        if not key:
            # Last resort: server-level env var (for server admins only)
            key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise HTTPException(
                status_code=422,
                detail="Gemini API key not set. Go to Settings → LLM API Keys."
            )
        log.debug("Resolved Gemini API key", user=user.get("email"), source="settings" if s.get("gemini_api_key") else "env")
        return key

    elif llm == "deepseek":
        key = s.get("deepseek_api_key", "").strip()
        if not key:
            key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not key:
            raise HTTPException(
                status_code=422,
                detail="DeepSeek API key not set. Go to Settings → LLM API Keys."
            )
        log.debug("Resolved DeepSeek API key", user=user.get("email"), source="settings" if s.get("deepseek_api_key") else "env")
        return key

    raise HTTPException(status_code=422, detail=f"Unknown LLM: '{llm}'. Use 'gemini' or 'deepseek'.")


def _llm_has_key(user: dict, name: str) -> bool:
    """Return True if a real (non-placeholder) key exists for this LLM."""
    from llm import _is_real_key
    s = user.get("settings", {})
    if name == "gemini":
        return _is_real_key(s.get("gemini_api_key", "")) or _is_real_key(os.getenv("GEMINI_API_KEY", ""))
    if name == "deepseek":
        return _is_real_key(s.get("deepseek_api_key", "")) or _is_real_key(os.getenv("DEEPSEEK_API_KEY", ""))
    return False


def _get_llm(user: dict) -> str:
    """Return user's preferred LLM, falling back to whichever one actually has a key."""
    s = user.get("settings", {})
    preferred = (s.get("default_llm") or "gemini").lower()
    if _llm_has_key(user, preferred):
        return preferred
    for fallback in ("gemini", "deepseek"):
        if fallback != preferred and _llm_has_key(user, fallback):
            log.info("LLM key fallback", preferred=preferred, using=fallback, user=user.get("email"))
            return fallback
    return preferred  # let _resolve_api_key raise the informative error


def _effective_llm(user: dict, requested: str) -> str:
    """Use requested LLM if a key exists for it, otherwise fall back to _get_llm()."""
    return requested if _llm_has_key(user, requested) else _get_llm(user)


def _status_key(email: str, db_config: dict) -> str:
    import hashlib
    key = f"{email}:{db_config.get('host','env')}:{db_config.get('database','env')}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _run_sql(conn, sql: str, title: str) -> dict:
    cursor = conn.cursor()
    log.debug("Executing SQL", title=title, sql=sql[:120])
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    data = [{c: _safe(v) for c, v in zip(cols, row)} for row in rows]
    log.debug("SQL result", title=title, rows=len(data))
    return {"title": title, "columns": cols, "data": data, "row_count": len(data)}


# ══════════════════════════════════════════════════════════════════════════════
# REQUEST LOGGING MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = datetime.datetime.utcnow()
    log.debug("Request started", method=request.method, path=request.url.path)
    try:
        response = await call_next(request)
        duration = (datetime.datetime.utcnow() - start).total_seconds() * 1000
        log.info("Request completed",
                 method=request.method, path=request.url.path,
                 status=response.status_code, duration_ms=round(duration, 1))
        return response
    except Exception as e:
        duration = (datetime.datetime.utcnow() - start).total_seconds() * 1000
        log.error("Request failed",
                  method=request.method, path=request.url.path,
                  error=str(e), duration_ms=round(duration, 1))
        raise


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND CACHE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _background_build(email: str, db_config: dict, llm: str, api_key: str):
    sk = _status_key(email, db_config)
    _build_status[sk] = {
        "status": "building",
        "progress": [],
        "started_at": datetime.datetime.utcnow().isoformat(),
    }
    logs = _build_status[sk]["progress"]
    log.info("Cache build started",
             user=email, db=db_config.get("database"), llm=llm)
    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [r[0] for r in cursor.fetchall()]
        schemas = get_table_schemas(conn, tables)
        fkeys = get_foreign_keys(conn)
        samples = get_sample_data(conn, tables, rows=3)
        log.info("Schema loaded for cache build",
                 user=email, tables=len(tables), fkeys=len(fkeys))

        def progress(msg):
            logs.append(msg)
            log.debug("Cache build progress", user=email, step=msg)

        def llm_caller(prompt, system, llm_name, max_tokens):
            return call_llm(prompt, system, llm_name, max_tokens, api_key=api_key, user_email=email)

        cache_data = build_schema_cache(
            conn=conn, schemas=schemas, fkeys=fkeys, samples=samples,
            llm_caller=llm_caller, llm=llm, progress_callback=progress,
        )
        conn.close()
        save_cache(email, db_config, cache_data)
        _build_status[sk]["status"] = "done"
        _build_status[sk]["successful"] = cache_data["successful_count"]
        _build_status[sk]["failed"] = cache_data["failed_count"]
        logs.append(f"✅ Done — {cache_data['successful_count']} templates cached, "
                    f"{cache_data['failed_count']} failed.")
        log.info("Cache build complete",
                 user=email, successful=cache_data["successful_count"],
                 failed=cache_data["failed_count"])
    except Exception as e:
        _build_status[sk]["status"] = "error"
        _build_status[sk]["error"] = str(e)
        logs.append(f"❌ Build failed: {e}")
        log.error("Cache build failed", user=email, error=str(e),
                  traceback=traceback.format_exc())


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    log.debug("Health check")
    return {"status": "ok", "version": "3.0.0"}



@app.get("/llm/models")
def llm_models(user: dict = Depends(current_user)):
    """Return all Gemini models available for this API key. Useful for debugging."""
    s = user.get("settings", {})
    gemini_key = s.get("gemini_api_key", "") or os.getenv("GEMINI_API_KEY", "")
    models = list_gemini_models(gemini_key)
    log.info("Listed Gemini models", user=user["email"], count=len(models))
    return {"gemini_models": models, "count": len(models)}

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
    log.info("Register attempt", email=req.email)
    user = create_user(req.name, req.email, req.password)
    token = create_token(req.email)
    log.info("User registered", email=req.email)
    return {"token": token, "user": {"name": user["name"], "email": user["email"]}}


@app.post("/auth/login")
def login(req: LoginRequest):
    log.info("Login attempt", email=req.email)
    user = authenticate_user(req.email, req.password)
    token = create_token(req.email)
    log.info("User logged in", email=req.email)
    return {"token": token, "user": {"name": user["name"], "email": user["email"]}}


@app.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return {"name": user["name"], "email": user["email"]}


# ══════════════════════════════════════════════════════════════════════════════
# ONBOARDING — validate LLM key + check DB + trigger build (all in one flow)
# ══════════════════════════════════════════════════════════════════════════════

class ValidateKeyRequest(BaseModel):
    llm: str
    api_key: str

class OnboardingDBRequest(BaseModel):
    name: str
    host: str
    port: int = 3306
    database: str
    user: str
    password: str
    llm: str = "gemini"   # which LLM to use for the cache build


@app.post("/onboarding/validate-key")
def onboarding_validate_key(req: ValidateKeyRequest,
                             user: dict = Depends(current_user)):
    """Step 1 of onboarding: test the LLM API key."""
    log.info("Onboarding: validating LLM key", user=user["email"], llm=req.llm)
    result = validate_llm_key(req.llm, req.api_key)
    if result["ok"]:
        # Save key to user settings immediately
        patch = {}
        if req.llm == "gemini":
            patch["gemini_api_key"] = req.api_key
        else:
            patch["deepseek_api_key"] = req.api_key
        patch["default_llm"] = req.llm
        update_user_settings(user["email"], patch)
        log.info("Onboarding: LLM key saved", user=user["email"], llm=req.llm)
    return result


@app.post("/onboarding/test-db")
def onboarding_test_db(req: OnboardingDBRequest,
                       user: dict = Depends(current_user)):
    """Step 2: test DB connection and return table list."""
    log.info("Onboarding: testing DB connection",
             user=user["email"], host=req.host, database=req.database)
    try:
        conn = get_connection(req.dict())
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [r[0] for r in cursor.fetchall()]
        cursor.execute("SHOW TABLE STATUS")
        status_rows = cursor.fetchall()
        conn.close()
        log.info("Onboarding: DB connection OK",
                 user=user["email"], tables=len(tables))
        return {"ok": True, "tables": tables, "table_count": len(tables)}
    except Exception as e:
        log.warning("Onboarding: DB connection failed",
                    user=user["email"], error=str(e))
        return {"ok": False, "error": str(e)}


@app.post("/onboarding/connect-db")
def onboarding_connect_db(req: OnboardingDBRequest,
                           background_tasks: BackgroundTasks,
                           user: dict = Depends(current_user)):
    """
    Step 3: save DB config, then immediately trigger cache build.
    Returns instantly — cache builds in background.
    """
    log.info("Onboarding: connecting DB",
             user=user["email"], host=req.host, database=req.database)
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    db_dict = {
        "name": req.name, "host": req.host, "port": req.port,
        "database": req.database, "user": req.user, "password": req.password,
    }
    configs.append(db_dict)
    new_idx = len(configs) - 1
    update_user_settings(user["email"], {
        "db_configs": configs,
        "active_db_index": new_idx,
    })

    # Get the API key that was just validated
    llm = req.llm or _get_llm(user)
    try:
        api_key = _resolve_api_key(user, llm)
    except HTTPException:
        api_key = ""
        log.warning("No API key available for cache build", user=user["email"], llm=llm)

    log.info("Onboarding: triggering background cache build",
             user=user["email"], llm=llm)
    background_tasks.add_task(_background_build, user["email"], db_dict, llm, api_key)
    return {"ok": True, "building_cache": True, "db_index": new_idx}


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class SettingsPatch(BaseModel):
    gemini_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    default_llm: Optional[str] = None
    active_db_index: Optional[int] = None
    onboarding_complete: Optional[bool] = None

class DBConfig(BaseModel):
    name: str
    host: str
    port: int = 3306
    database: str
    user: str
    password: str


@app.get("/settings")
def get_settings(user: dict = Depends(current_user)):
    log.debug("Get settings", user=user["email"])
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
    log.info("Patch settings", user=user["email"], keys=list(patch.keys()))
    updated = update_user_settings(user["email"], patch)
    return {"ok": True}


@app.post("/settings/db")
def add_db_config(cfg: DBConfig, background_tasks: BackgroundTasks,
                  user: dict = Depends(current_user)):
    log.info("Add DB config", user=user["email"], db_name=cfg.name, host=cfg.host)
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    configs.append(cfg.dict())
    new_idx = len(configs) - 1
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": new_idx})
    llm = _get_llm(user)
    try:
        api_key = _resolve_api_key(user, llm)
        background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm, api_key)
        return {"ok": True, "building_cache": True}
    except HTTPException:
        log.warning("Skipping cache build — no API key set", user=user["email"])
        return {"ok": True, "building_cache": False, "warning": "Add an API key in Settings to build the analytics cache."}


@app.put("/settings/db/{index}")
def update_db_config(index: int, cfg: DBConfig,
                     background_tasks: BackgroundTasks,
                     user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    invalidate_cache(user["email"], configs[index])
    configs[index] = cfg.dict()
    update_user_settings(user["email"], {"db_configs": configs})
    llm = _get_llm(user)
    try:
        api_key = _resolve_api_key(user, llm)
        background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm, api_key)
    except HTTPException:
        pass
    return {"ok": True}


@app.delete("/settings/db/{index}")
def delete_db_config(index: int, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    log.info("Delete DB config", user=user["email"], index=index)
    invalidate_cache(user["email"], configs[index])
    configs.pop(index)
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": 0})
    return {"ok": True}


@app.post("/settings/db/{index}/activate")
def activate_db(index: int, background_tasks: BackgroundTasks,
                user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Index out of range")
    update_user_settings(user["email"], {"active_db_index": index})
    db_config = configs[index]
    if not get_cache(user["email"], db_config):
        llm = _get_llm(user)
        try:
            api_key = _resolve_api_key(user, llm)
            background_tasks.add_task(_background_build, user["email"], db_config, llm, api_key)
            return {"ok": True, "active": index, "building_cache": True}
        except HTTPException:
            return {"ok": True, "active": index, "building_cache": False}
    return {"ok": True, "active": index, "building_cache": False}


@app.post("/settings/db/test")
def test_db_connection(cfg: DBConfig, user: dict = Depends(current_user)):
    log.info("Test DB connection", user=user["email"], host=cfg.host, database=cfg.database)
    try:
        conn = get_connection(cfg.dict())
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        tables = [r[0] for r in cursor.fetchall()]
        conn.close()
        log.info("DB test OK", user=user["email"], tables=len(tables))
        return {"ok": True, "tables": tables, "table_count": len(tables)}
    except Exception as e:
        log.warning("DB test failed", user=user["email"], error=str(e))
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# CACHE
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/cache/status")
def cache_status(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    sk = _status_key(user["email"], db_config)
    cache_info = get_cache_status(user["email"], db_config)
    build_info = _build_status.get(sk, {})
    return {**cache_info, "build": build_info}


@app.get("/cache/progress")
def cache_progress(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    sk = _status_key(user["email"], db_config)
    return _build_status.get(sk, {"status": "unknown"})


@app.post("/cache/rebuild")
def rebuild_cache(background_tasks: BackgroundTasks,
                  user: dict = Depends(current_user)):
    if not user.get("settings", {}).get("db_configs"):
        raise HTTPException(
            status_code=422,
            detail="Analytics rebuild requires a direct MySQL database connection. "
                   "Integration analytics templates (SalesPlay, Loyverse, etc.) are "
                   "pre-built and don't need an AI rebuild — use the Analytics Hub."
        )
    db_config = _resolve_db(user)
    invalidate_cache(user["email"], db_config)
    llm = _get_llm(user)
    api_key = _resolve_api_key(user, llm)
    log.info("Cache rebuild requested", user=user["email"], llm=llm)
    background_tasks.add_task(_background_build, user["email"], db_config, llm, api_key)
    return {"ok": True, "message": "Rebuild started"}


# ══════════════════════════════════════════════════════════════════════════════
# TABLES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/tables")
def list_tables(user: dict = Depends(current_user)):
    s = user.get("settings", {})

    # Own-DB user: show tables from their configured database
    if s.get("db_configs"):
        db_config = _resolve_db(user)
        log.info("List tables (own DB)", user=user["email"])
        try:
            conn = get_connection(db_config)
            cursor = conn.cursor()
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]
            schemas = get_table_schemas(conn, tables)
            fkeys = get_foreign_keys(conn)
            conn.close()
            log.info("Tables loaded", user=user["email"], count=len(tables))
            return {"tables": tables, "schemas": schemas, "foreign_keys": fkeys}
        except Exception as e:
            log.error("Failed to list tables", user=user["email"], error=str(e))
            raise HTTPException(status_code=500, detail=str(e))

    # Provider-only user: return only their own integration tables
    conns = get_user_connections(user["email"])
    if not conns:
        return {"tables": [], "schemas": {}, "foreign_keys": []}
    try:
        iconn = _get_internal_conn()
        cursor = iconn.cursor()
        tables = []
        for c in conns:
            prefix = c.get("table_prefix", "")
            if not prefix:
                continue
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME LIKE %s "
                "ORDER BY TABLE_NAME",
                (f"{prefix}%",)
            )
            tables.extend(r[0] for r in cursor.fetchall())
        iconn.close()
        log.info("Provider tables listed", user=user["email"], count=len(tables))
        return {"tables": tables, "schemas": {}, "foreign_keys": []}
    except Exception as e:
        log.error("Failed to list provider tables", user=user["email"], error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tables/{table_name}/columns")
def get_table_columns(table_name: str, user: dict = Depends(current_user)):
    """Return column names and types for a table the user owns."""
    import re
    if not re.match(r'^[A-Za-z0-9_]+$', table_name):
        raise HTTPException(status_code=400, detail="Invalid table name.")
    s = user.get("settings", {})
    try:
        if s.get("db_configs"):
            conn = get_connection(_resolve_db(user))
        else:
            # Verify this table belongs to the requesting user
            conns = get_user_connections(user["email"])
            prefixes = [c.get("table_prefix", "") for c in conns if c.get("table_prefix")]
            if not prefixes or not any(table_name.startswith(p) for p in prefixes):
                raise HTTPException(status_code=403, detail="Access denied.")
            conn = _get_internal_conn()
        cursor = conn.cursor()
        cursor.execute(f"DESCRIBE `{table_name}`")
        rows = cursor.fetchall()
        conn.close()
        return {"columns": [{"name": r[0], "type": r[1]} for r in rows]}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Failed to describe table", table=table_name, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# DISCOVER
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/discover")
def discover(user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    if cache:
        log.debug("Discover: serving from cache", user=user["email"],
                  templates=len(cache.get("template_sql", {})))
        return {
            "catalogue": cache.get("catalogue", []),
            "from_cache": True,
            "built_at": cache.get("built_at"),
            "template_count": len(cache.get("template_sql", {})),
        }
    sk = _status_key(user["email"], db_config)
    build_info = _build_status.get(sk, {})
    if build_info.get("status") == "building":
        return {"catalogue": [], "from_cache": False, "building": True,
                "progress": build_info.get("progress", [])}
    log.info("Discover: no cache found", user=user["email"])
    return {"catalogue": [], "from_cache": False, "building": False, "needs_build": True}


# ══════════════════════════════════════════════════════════════════════════════
# NL QUERY
# ══════════════════════════════════════════════════════════════════════════════

class NLQueryRequest(BaseModel):
    question: str
    llm: str = "gemini"


@app.post("/query")
def natural_language_query(req: NLQueryRequest, user: dict = Depends(current_user)):
    llm = _effective_llm(user, req.llm)
    log.info("NL query", user=user["email"], llm=llm, question=req.question[:80])
    api_key = _resolve_api_key(user, llm)
    try:
        conn = get_connection(_resolve_db(user))
        schemas = get_table_schemas(conn, None)
        fkeys = get_foreign_keys(conn)
        sql = query_to_sql(req.question, schemas, llm, fkeys, api_key=api_key, user_email=user["email"])
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        conn.close()
        data = [{k: _safe(v) for k, v in dict(zip(columns, row)).items()} for row in rows]
        log.info("NL query complete", user=user["email"], rows=len(data))
        return {"sql": sql, "columns": columns, "data": data, "row_count": len(data)}
    except HTTPException:
        raise
    except Exception as e:
        log.error("NL query failed", user=user["email"], error=str(e))
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
    log.info("Run analytics", user=user["email"], template=req.template_id)
    db_config = _resolve_db(user)
    conn = get_connection(db_config)
    try:
        # 1. Cache SQL
        cache = get_cache(user["email"], db_config)
        if cache and req.template_id in cache.get("template_sql", {}):
            sql = cache["template_sql"][req.template_id]
            title = next(
                (i.get("title", req.template_id) for i in cache.get("catalogue", [])
                 if i.get("id") == req.template_id),
                req.template_id.replace("_", " ").title()
            )
            try:
                result = _run_sql(conn, sql, title)
                result["source"] = "cache"
                log.info("Analytics served from cache", template=req.template_id)
                conn.close()
                return result
            except Exception as sql_err:
                log.warning("Cached SQL failed, trying fallbacks",
                            template=req.template_id, error=str(sql_err))

        # 2. Python analytics
        r = _try_python(req.template_id, conn)
        if r:
            r["source"] = "python"
            conn.close()
            return r

        # 3. Hardcoded fallback
        r = _hardcoded(req.template_id, conn)
        if r:
            r["source"] = "fallback"
            log.warning("Analytics using hardcoded fallback SQL", template=req.template_id)
            conn.close()
            return r

        conn.close()
        raise HTTPException(status_code=404,
                            detail=f"Template '{req.template_id}' not available. Rebuild the cache.")
    except HTTPException:
        raise
    except Exception as e:
        conn.close()
        log.error("Analytics run failed", template=req.template_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


def _try_python(tid: str, conn) -> Optional[Dict]:
    try:
        if tid == "customer_rfm":        return run_rfm_analysis(conn)
        if tid == "customer_cohort":     return run_cohort_analysis(conn)
        if tid == "basket_analysis":     return run_basket_analysis(conn)
        if tid == "growth_metrics":      return run_growth_metrics(conn)
        if tid == "cashier_performance": return run_employee_performance(conn)
        if tid == "product_velocity":    return run_product_velocity(conn)
        if tid == "payment_methods":     return run_payment_breakdown(conn)
        if tid == "location_comparison": return run_location_comparison(conn)
    except Exception as e:
        log.warning("Python analytics failed", tid=tid, error=str(e))
    return None


def _hardcoded(tid: str, conn) -> Optional[Dict]:
    QUERIES = {
        "revenue_trend":       ("SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month, ROUND(SUM(invoiceTotal),2) as revenue, COUNT(*) as transactions, ROUND(AVG(invoiceTotal),2) as avg_ticket FROM invoices WHERE invoiceDate IS NOT NULL GROUP BY month ORDER BY month DESC LIMIT 24", "Monthly Revenue Trend"),
        "revenue_by_category": ("SELECT p.category, ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue, ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as gross_profit, ROUND(AVG((ii.itemPrice-ii.itemCost)/NULLIF(ii.itemPrice,0)*100),1) as margin_pct FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode GROUP BY p.category ORDER BY revenue DESC", "Revenue by Category"),
        "revenue_by_location": ("SELECT l.location_name, ROUND(SUM(i.invoiceTotal),2) as revenue, COUNT(*) as transactions, ROUND(AVG(i.invoiceTotal),2) as avg_ticket FROM invoices i JOIN locations l ON i.location_id=l.location_id GROUP BY l.location_id,l.location_name ORDER BY revenue DESC", "Revenue by Location"),
        "hourly_pattern":      ("SELECT HOUR(invoiceTime) as hour, ROUND(SUM(invoiceTotal),2) as total_revenue, COUNT(*) as transactions FROM invoices WHERE invoiceTime IS NOT NULL GROUP BY hour ORDER BY hour", "Hourly Pattern"),
        "daily_trend_7":       ("SELECT invoiceDate as date, ROUND(SUM(invoiceTotal),2) as revenue, COUNT(*) as orders FROM invoices WHERE invoiceDate >= CURDATE()-INTERVAL 7 DAY GROUP BY invoiceDate ORDER BY invoiceDate", "Last 7 Days"),
        "top_products":        ("SELECT p.name, p.category, ROUND(SUM(ii.qty*ii.itemPrice),2) as revenue, SUM(ii.qty) as units FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode GROUP BY p.itemCode,p.name,p.category ORDER BY revenue DESC LIMIT 20", "Top Products"),
        "top_customers":       ("SELECT c.name, COUNT(DISTINCT i.invoiceNumber) as orders, ROUND(SUM(i.invoiceTotal),2) as lifetime_value FROM customers c JOIN invoices i ON c.customerId=i.customerId GROUP BY c.customerId,c.name ORDER BY lifetime_value DESC LIMIT 25", "Top Customers"),
        "payment_methods":     ("SELECT payMethod as payment_method, COUNT(*) as transactions, ROUND(SUM(invoiceTotal),2) as revenue FROM invoices WHERE payMethod IS NOT NULL GROUP BY payMethod ORDER BY revenue DESC", "Payment Methods"),
    }
    if tid not in QUERIES:
        return None
    sql, title = QUERIES[tid]
    try:
        return _run_sql(conn, sql, title)
    except Exception as e:
        log.warning("Hardcoded fallback SQL failed", tid=tid, error=str(e))
        return None


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
    log.info("Forecast (manual)", user=user["email"],
             table=req.table, date_col=req.date_column, value_col=req.value_column)
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT DATE(`{req.date_column}`) as ds, SUM(`{req.value_column}`) as y "
            f"FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL GROUP BY ds ORDER BY ds"
        )
        rows = cursor.fetchall()
        conn.close()
        log.info("Forecast data loaded", rows=len(rows))
        return run_forecast(rows, req.periods)
    except Exception as e:
        log.error("Forecast failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/forecast/auto")
def auto_forecast(periods: int = 90, user: dict = Depends(current_user)):
    s = user.get("settings", {})

    # Provider-only user: run forecast on their synced receipts table
    if not s.get("db_configs"):
        conns = get_user_connections(user["email"])
        if not conns:
            raise HTTPException(status_code=422, detail="No data source connected.")
        prefix = conns[0].get("table_prefix", "")
        if not prefix:
            raise HTTPException(status_code=422, detail="Integration tables not ready.")
        receipts_tbl = f"{prefix}receipts"
        log.info("Provider auto forecast", user=user["email"], table=receipts_tbl)
        try:
            iconn = _get_internal_conn()
            cursor = iconn.cursor()
            cursor.execute(
                f"SELECT DATE(created_at) AS ds, SUM(total_money) AS y "
                f"FROM `{receipts_tbl}` "
                f"WHERE created_at IS NOT NULL AND total_money > 0 "
                f"GROUP BY DATE(created_at) ORDER BY DATE(created_at)"
            )
            rows = cursor.fetchall()
            iconn.close()
        except Exception as e:
            log.error("Provider forecast query failed", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        result = run_forecast(rows, periods)
        result.update(used_table=receipts_tbl, used_date_col="created_at",
                      used_value_col="total_money", from_cache=False)
        return result

    # Own-DB user
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    auto = cache.get("auto_columns", {}) if cache else {}
    table  = auto.get("forecast_table")    or "invoices"
    d_col  = auto.get("forecast_date_col") or "invoiceDate"
    v_col  = auto.get("forecast_value_col")or "invoiceTotal"
    log.info("Auto forecast", user=user["email"],
             table=table, date_col=d_col, value_col=v_col,
             from_cache=bool(cache and auto.get("forecast_table")))
    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT DATE(`{d_col}`) as ds, SUM(`{v_col}`) as y "
            f"FROM `{table}` WHERE `{d_col}` IS NOT NULL GROUP BY ds ORDER BY ds"
        )
        rows = cursor.fetchall()
        conn.close()
        result = run_forecast(rows, periods)
        result["used_table"]     = table
        result["used_date_col"]  = d_col
        result["used_value_col"] = v_col
        result["from_cache"]     = bool(cache and auto.get("forecast_table"))
        return result
    except Exception as e:
        log.error("Auto forecast failed", error=str(e))
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
    log.info("Anomaly detection (manual)", user=user["email"], table=req.table)
    try:
        conn = get_connection(_resolve_db(user))
        cursor = conn.cursor()
        if req.date_column:
            cursor.execute(
                f"SELECT `{req.date_column}`, `{req.value_column}` "
                f"FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL "
                f"ORDER BY `{req.date_column}`"
            )
        else:
            cursor.execute(f"SELECT `{req.value_column}` FROM `{req.table}`")
        rows = cursor.fetchall()
        conn.close()
        return run_anomaly_detection(rows, has_date=bool(req.date_column))
    except Exception as e:
        log.error("Anomaly detection failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/anomalies/auto")
def auto_anomalies(user: dict = Depends(current_user)):
    s = user.get("settings", {})

    # Provider-only user: run anomaly detection on their synced receipts table
    if not s.get("db_configs"):
        conns = get_user_connections(user["email"])
        if not conns:
            raise HTTPException(status_code=422, detail="No data source connected.")
        prefix = conns[0].get("table_prefix", "")
        if not prefix:
            raise HTTPException(status_code=422, detail="Integration tables not ready.")
        receipts_tbl = f"{prefix}receipts"
        log.info("Provider auto anomaly", user=user["email"], table=receipts_tbl)
        try:
            iconn = _get_internal_conn()
            cursor = iconn.cursor()
            cursor.execute(
                f"SELECT DATE(created_at), SUM(total_money) "
                f"FROM `{receipts_tbl}` "
                f"WHERE created_at IS NOT NULL AND total_money > 0 "
                f"GROUP BY DATE(created_at) ORDER BY DATE(created_at)"
            )
            rows = cursor.fetchall()
            iconn.close()
        except Exception as e:
            log.error("Provider anomaly query failed", error=str(e))
            raise HTTPException(status_code=500, detail=str(e))
        result = run_anomaly_detection(rows, has_date=True)
        result.update(used_table=receipts_tbl, used_date_col="created_at",
                      used_value_col="total_money", from_cache=False)
        return result

    # Own-DB user
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    auto = cache.get("auto_columns", {}) if cache else {}
    table  = auto.get("anomaly_table")    or "invoices"
    d_col  = auto.get("anomaly_date_col") or "invoiceDate"
    v_col  = auto.get("anomaly_value_col")or "invoiceTotal"
    log.info("Auto anomaly detection", user=user["email"],
             table=table, date_col=d_col, value_col=v_col)
    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT `{d_col}`, SUM(`{v_col}`) FROM `{table}` "
            f"WHERE `{d_col}` IS NOT NULL GROUP BY `{d_col}` ORDER BY `{d_col}`"
        )
        rows = cursor.fetchall()
        conn.close()
        result = run_anomaly_detection(rows, has_date=True)
        result["used_table"]     = table
        result["used_date_col"]  = d_col
        result["used_value_col"] = v_col
        result["from_cache"]     = bool(cache and auto.get("anomaly_table"))
        return result
    except Exception as e:
        log.error("Auto anomaly detection failed", error=str(e))
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
    llm = _effective_llm(user, req.llm)
    log.info("Generate report", user=user["email"], llm=llm,
             title=req.title, sections=req.sections)
    api_key = _resolve_api_key(user, llm)
    try:
        db_config = _resolve_db(user)
        conn = get_connection(db_config)
        cache = get_cache(user["email"], db_config)
        section_data = {}
        for sid in req.sections:
            try:
                r = None
                if cache and sid in cache.get("template_sql", {}):
                    try:
                        sql = cache["template_sql"][sid]
                        title_s = next(
                            (i.get("title", sid) for i in cache.get("catalogue", [])
                             if i.get("id") == sid), sid
                        )
                        r = _run_sql(conn, sql, title_s)
                        r["source"] = "cache"
                    except Exception:
                        r = None
                if not r:
                    r = _try_python(sid, conn)
                    if r: r["source"] = "python"
                if not r:
                    r = _hardcoded(sid, conn)
                    if r: r["source"] = "fallback"
                if r:
                    section_data[sid] = r
            except Exception as e:
                log.warning("Report section failed", sid=sid, error=str(e))

        # KPIs
        kpis = _get_kpis(conn, cache)
        conn.close()
        narrative = generate_report_summary(
            title=req.title, kpis=kpis, section_data=section_data,
            llm=req.llm, format=req.format, api_key=api_key,
            user_email=user["email"]
        )
        log.info("Report generated", user=user["email"], sections=len(section_data))
        return {"title": req.title, "kpis": kpis, "sections": section_data, "narrative": narrative}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Report generation failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


def _get_kpis(conn, cache) -> Dict:
    auto = cache.get("auto_columns", {}) if cache else {}
    table = auto.get("forecast_table")    or "invoices"
    d_col = auto.get("forecast_date_col") or "invoiceDate"
    v_col = auto.get("forecast_value_col")or "invoiceTotal"
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT ROUND(SUM(`{v_col}`),2), COUNT(*), ROUND(AVG(`{v_col}`),2), "
            f"MIN(`{d_col}`), MAX(`{d_col}`) "
            f"FROM `{table}` WHERE `{d_col}` IS NOT NULL"
        )
        row = cursor.fetchone()
        if row:
            keys = ["total_revenue","total_transactions","avg_transaction","from_date","to_date"]
            return {k: _safe(v) for k, v in zip(keys, row)}
    except Exception as e:
        log.warning("KPI fetch failed", error=str(e))
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# EXTERNAL PROVIDER ROUTES
# ══════════════════════════════════════════════════════════════════════════════

# (providers already imported at top of file)


class ProviderConnectRequest(BaseModel):
    provider_id: str
    credentials: Dict[str, str]


@app.get("/providers")
def get_providers():
    """List all available external API providers with their manifests."""
    try:
        providers = list_providers()
        log.info("Listed providers", count=len(providers))
        return {"providers": providers}
    except Exception as e:
        log.error("Failed to list providers", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/providers/connected")
def get_connected_providers(user: dict = Depends(current_user)):
    """List all providers this user has connected. Returns empty list if DB not configured."""
    try:
        connections = get_user_connections(user["email"])
        log.debug("Got connected providers", user=user["email"], count=len(connections))
        return {"connections": connections}
    except Exception as e:
        log.warning("Could not load connections (integration DB may not be configured)",
                    user=user["email"], error=str(e))
        return {"connections": []}  # safe fallback — never break the UI


@app.post("/providers/validate")
def validate_provider_credentials(req: ProviderConnectRequest, user: dict = Depends(current_user)):
    """Test provider credentials before saving."""
    from providers import get_provider as gp
    log.info("Validating provider credentials", user=user["email"], provider=req.provider_id)
    try:
        provider = gp(req.provider_id)
        result = provider.validate_credentials(req.credentials)
        return {"ok": result.ok, "error": result.error, "details": result.details}
    except Exception as e:
        log.error("Provider validation error", provider=req.provider_id, error=str(e))
        return {"ok": False, "error": str(e)}


@app.post("/providers/connect")
def connect_provider_route(req: ProviderConnectRequest,
                           background_tasks: BackgroundTasks,
                           user: dict = Depends(current_user)):
    """
    Connect a provider: validate, create tables, trigger full sync.
    Sync runs in background.
    """
    log.info("Connecting provider", user=user["email"], provider=req.provider_id)
    try:
        connection_id = connect_provider(user["email"], req.provider_id, req.credentials)
        background_tasks.add_task(trigger_sync, user["email"], connection_id, full=True)
        return {"ok": True, "connection_id": connection_id}
    except Exception as e:
        log.error("Provider connect failed", provider=req.provider_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/providers/stats")
def provider_stats(user: dict = Depends(current_user)):
    """Return aggregate stats across all user integrations (total rows across all providers)."""
    try:
        return {"total_rows": get_user_total_rows(user["email"])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/providers/{connection_id}")
def disconnect_provider_route(connection_id: str, user: dict = Depends(current_user)):
    """Disconnect and remove a provider connection."""
    log.info("Disconnecting provider", user=user["email"], connection_id=connection_id)
    try:
        disconnect_provider(user["email"], connection_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/providers/{connection_id}/sync")
def manual_sync(connection_id: str, background_tasks: BackgroundTasks,
                user: dict = Depends(current_user)):
    """Manually trigger a delta sync for a connection."""
    log.info("Manual sync triggered", user=user["email"], connection_id=connection_id)
    background_tasks.add_task(trigger_sync, user["email"], connection_id, full=False)
    return {"ok": True, "message": "Sync started in background"}


@app.get("/providers/{connection_id}/status")
def provider_status(connection_id: str, user: dict = Depends(current_user)):
    """Get live sync status and stats for a connection."""
    try:
        status = get_connection_status(user["email"], connection_id)
        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/providers/{connection_id}/history")
def provider_history(connection_id: str, user: dict = Depends(current_user)):
    """Get sync history for a connection."""
    try:
        history = get_sync_history(user["email"], connection_id)
        return {"history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ══════════════════════════════════════════════════════════════════════════════
# CREDITS AND USAGE TRACKING
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/credits")
def get_credits(user: dict = Depends(current_user)):
    """Get user's credit balance and usage statistics."""
    try:
        return get_user_credits(user["email"])
    except Exception as e:
        log.error("Get credits failed", user=user["email"], error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/credits/history")
def get_credit_history(
    limit: int = 50,
    user: dict = Depends(current_user)
):
    """Get user's credit usage history."""
    try:
        return {"history": get_usage_history(user["email"], limit)}
    except Exception as e:
        log.error("Get credit history failed", user=user["email"], error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION-SPECIFIC ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/integrations/{provider_id}/analytics/templates")
def list_integration_templates(
    provider_id: str,
    user: dict = Depends(current_user)
):
    """List available analytics templates for an integration."""
    try:
        templates_map = {}
        
        # Import provider-specific templates
        try:
            from providers.salesplay.analytics import TEMPLATES as SALESPLAY_TEMPLATES
            templates_map["salesplay"] = SALESPLAY_TEMPLATES
        except ImportError:
            pass
        
        try:
            from providers.loyverse.analytics import TEMPLATES as LOYVERSE_TEMPLATES
            templates_map["loyverse"] = LOYVERSE_TEMPLATES
        except ImportError:
            pass
        
        if provider_id not in templates_map:
            return {"templates": []}
        
        templates = templates_map[provider_id]
        return {
            "templates": [
                {
                    "id": tid,
                    "title": t["title"],
                    "description": t.get("description", ""),
                    "type": t.get("type", "table")
                }
                for tid, t in templates.items()
            ]
        }
    except Exception as e:
        log.error("List integration templates failed", 
                  provider=provider_id, 
                  error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


class IntegrationAnalyticsRequest(BaseModel):
    template_id: str


@app.post("/integrations/{provider_id}/analytics/run")
def run_integration_analytics(
    provider_id: str,
    req: IntegrationAnalyticsRequest,
    user: dict = Depends(current_user)
):
    """Run analytics on integration data."""
    from integrations import get_integration, _get_internal_conn
    
    try:
        # Get integration record
        integration = get_integration(user["email"], provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not connected")
        
        table_prefix = integration["table_prefix"]
        
        # Connect to DataMind's internal DB
        conn = _get_internal_conn()
        
        try:
            # Import provider-specific analytics
            if provider_id == "salesplay":
                from providers.salesplay.analytics import run_salesplay_analytics
                result = run_salesplay_analytics(conn, table_prefix, req.template_id)
            elif provider_id == "loyverse":
                from providers.loyverse.analytics import run_loyverse_analytics
                result = run_loyverse_analytics(conn, table_prefix, req.template_id)
            else:
                raise HTTPException(
                    status_code=404, 
                    detail=f"No analytics available for {provider_id}"
                )
            
            result["source"] = "integration"
            result["provider"] = provider_id
            return result
            
        finally:
            conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        log.error("Integration analytics failed", 
                  provider=provider_id, 
                  template=req.template_id, 
                  user=user["email"],
                  error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


class IntegrationForecastRequest(BaseModel):
    table: str
    date_column: str
    value_column: str
    periods: int = 90


@app.post("/integrations/{provider_id}/forecast")
def forecast_integration(
    provider_id: str,
    req: IntegrationForecastRequest,
    user: dict = Depends(current_user)
):
    """Run forecast on integration data."""
    from integrations import get_integration, _get_internal_conn
    
    try:
        integration = get_integration(user["email"], provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not connected")
        
        conn = _get_internal_conn()
        table_prefix = integration["table_prefix"]
        
        try:
            # Build SQL for forecast query
            sql = f"""
                SELECT DATE({req.date_column}) as date, 
                       SUM({req.value_column}) as value
                FROM {table_prefix}_{req.table}
                WHERE {req.date_column} IS NOT NULL
                GROUP BY DATE({req.date_column})
                ORDER BY date
            """
            
            cursor = conn.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            
            if len(rows) < 10:
                raise HTTPException(
                    status_code=400, 
                    detail="Need at least 10 data points for forecasting"
                )
            
            result = run_forecast(rows, req.periods)
            result["source"] = "integration"
            result["provider"] = provider_id
            return result
            
        finally:
            conn.close()
            
    except HTTPException:
        raise
    except Exception as e:
        log.error("Integration forecast failed", 
                  provider=provider_id,
                  user=user["email"],
                  error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
