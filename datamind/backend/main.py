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
import re
import decimal
import datetime
import traceback
from fastapi import FastAPI, APIRouter, HTTPException, Depends, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from dotenv import load_dotenv

load_dotenv()  # must run before any local module reads os.getenv at import time

from logger import get_logger
from db import get_connection, get_table_schemas, get_foreign_keys, get_sample_data
from llm import (
    query_to_sql, generate_report_summary, call_llm, validate_llm_key,
    list_gemini_models, LLMTransientError,
    classify_question, synthesize_multi_step_answer,
)
from cache import get_cache, save_cache, invalidate_cache, get_cache_status
from schema_builder import build_schema_cache
from analytics import (
    run_forecast, run_anomaly_detection, run_cohort_analysis,
    run_rfm_analysis, run_basket_analysis, run_growth_metrics,
    run_employee_performance, run_product_velocity,
    run_payment_breakdown, run_location_comparison,
)
import conversations as _conv
from integrations import (
    bootstrap_integration_tables,
    connect_provider, disconnect_provider,
    get_user_connections, get_connection_status,
    trigger_sync, get_sync_history, start_scheduler,
    get_user_total_rows, _get_internal_conn,
    list_integrations, delete_user_data,
)
# credits.py / legacy credit tables removed — billing system supersedes them
from providers import list_providers, get_provider
from billing import (
    bootstrap_billing_tables, start_trial, subscribe_to_plan,
    get_user_subscription, get_subscription_plans, get_plan_by_id,
    check_ai_limit, check_db_limit, purchase_addon, get_llm_usage_history,
    get_addon_pricing, charge_ai_usage, get_ai_credit_rate, set_ai_credit_rate,
    calculate_tokens, charge_tokens, get_token_usage_history,
    check_plan_feature, get_plan_history_limit,
)
from embed import router as embed_router, bootstrap_embed_tables
from v1 import router as partner_router
from pool import get_pool

log = get_logger(__name__)

# Bootstrap integration tables (create if not exist)
try:
    bootstrap_integration_tables()
except Exception as _be:
    log.warning("Integration bootstrap skipped (configure DATAMIND_DB_* in .env)", error=str(_be))

from auth import (
    create_user, authenticate_user, create_token, get_user,
    get_user_settings, update_user_settings, current_user, init_users_table, delete_user,
    create_sso_handoff_token, redeem_sso_handoff_token,
)

_APP_NAME = os.getenv("APP_NAME", "SalesPlay AI")

app = FastAPI(
    title=_APP_NAME,
    version="3.0.0",
    description=(
        f"{_APP_NAME} API.\n\n"
        "## Partner API (v1)\n"
        "Server-to-server endpoints for embed partners. "
        f"Authenticate with `X-API-Key: <partner_key>` obtained from the {_APP_NAME} partner dashboard.\n\n"
        "All `/v1/` endpoints also require a `user_email` parameter to identify which end-user "
        "is being queried.\n\n"
        "## Embed API\n"
        f"Iframe embedding bootstrap flow (`/embed/*`). Used by the {_APP_NAME} embed SDK.\n\n"
        "## User API\n"
        "Standard user-facing endpoints (`/auth/*`, `/query`, `/analytics/*`, etc.). "
        "Authenticate with `Authorization: Bearer <jwt>`."
    ),
    contact={"name": f"{_APP_NAME} Support", "email": "support@datamind.ai"},
    license_info={"name": "Proprietary"},
)

# Rate limiting — shared limiter instance, limits configurable via .env
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from limiter import limiter as _limiter, RL_AUTH, RL_AUTH_LOGIN, RL_COMPUTE, RL_READ, RL_WRITE

app.state.limiter = _limiter

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=200,
        content={
            "ok": False, "success": False, "type": "error",
            "message": "You're sending requests too quickly. Please wait a moment and try again.",
            "columns": [], "data": [], "row_count": 0,
            "analysis": None, "think_mode": False,
            "conversation_id": None, "data_as_of": None, "steps": [],
        },
    )

app.add_middleware(SlowAPIMiddleware)

# SEC-14: HTTPS enforcement — only active when FORCE_HTTPS=true (production).
# In local dev (Windows) this env var is unset so redirects never fire.
# On Red Hat: set FORCE_HTTPS=true in the systemd environment file.
_FORCE_HTTPS    = os.getenv("FORCE_HTTPS", "").lower() == "true"
_SQL_TIMEOUT_MS = int(os.getenv("SQL_TIMEOUT_MS", "30000"))  # hard-kill runaway LLM queries

# Cached name of the session timeout variable for this MySQL/MariaDB server.
# "max_execution_time" (MySQL 5.7.8+, milliseconds)
# "max_statement_time"  (MariaDB, seconds)
# None = server doesn't support either — timeouts are skipped silently.
_sql_timeout_var: str | None = "unknown"  # "unknown" triggers first-use probe
_sql_timeout_lock = __import__("threading").Lock()


def _set_query_timeout(cursor) -> None:
    """Apply SQL_TIMEOUT_MS to the current session, probing which variable the server uses."""
    global _sql_timeout_var
    if _sql_timeout_var is None:
        return  # server supports neither — skip
    if _sql_timeout_var == "unknown":
        with _sql_timeout_lock:
            if _sql_timeout_var == "unknown":  # re-check inside lock
                for var, val in [
                    ("max_execution_time", _SQL_TIMEOUT_MS),           # MySQL (ms)
                    ("max_statement_time", _SQL_TIMEOUT_MS / 1000.0),  # MariaDB (seconds)
                ]:
                    try:
                        cursor.execute(f"SET SESSION {var}={val}")
                        _sql_timeout_var = var
                        log.info("SQL timeout variable detected", var=var, value=val)
                        return
                    except Exception:
                        pass
                _sql_timeout_var = None
                log.warning("SQL timeout not supported by this MySQL/MariaDB server — runaway queries will not be auto-killed")
                return
    # Variable already known
    try:
        val = _SQL_TIMEOUT_MS if _sql_timeout_var == "max_execution_time" else _SQL_TIMEOUT_MS / 1000.0
        cursor.execute(f"SET SESSION {_sql_timeout_var}={val}")
    except Exception as e:
        log.warning("Failed to set SQL timeout", var=_sql_timeout_var, error=str(e))

def _enforce_tenant_isolation(sql: str, tenant_id: str) -> str:
    """
    SEC-15: Server-side tenant isolation enforcement for shared sp_*/ly_* tables.

    The LLM is instructed to add WHERE tenant_id = '...' but sometimes omits it
    or adds it only for the primary table while leaving JOINed tables unscoped.
    This function post-processes the generated SQL to guarantee every shared-table
    reference is filtered to the correct tenant BEFORE execution.

    Strategy:
      1. Find all sp_*/ly_* table references with their aliases (FROM and JOIN).
      2. For each alias not already scoped with alias.tenant_id = '...':
         - If it's the FROM table: inject into the WHERE clause (or create one).
         - If it's a JOINed table: inject AND alias.tenant_id = '...' into the ON clause.
      3. Raise ValueError if ANY other tenant_id literal appears in the SQL
         (prevents prompt-injection attacks requesting another tenant's data).
    """
    if not tenant_id:
        return sql

    safe_tid = tenant_id.replace("'", "\\'")
    expected_literal = f"'{safe_tid}'"

    # Security: reject if a *different* tenant_id literal appears in the SQL.
    # e.g. user tries: "show me data where tenant_id = 'other_user_prefix'"
    tid_val_re = re.compile(r"tenant_id\s*=\s*'([^']*)'", re.IGNORECASE)
    for m in tid_val_re.finditer(sql):
        found_tid = m.group(1)
        if found_tid != tenant_id:
            raise ValueError(
                f"Query references tenant_id '{found_tid}' which does not match "
                f"your account. Cross-tenant queries are not allowed."
            )

    # SQL keywords that can never be a table alias — if the regex captures one of
    # these it means the table has no alias and the keyword belongs to the next clause.
    _ALIAS_KEYWORDS = frozenset({
        'WHERE', 'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'ON', 'SET',
        'INNER', 'LEFT', 'RIGHT', 'CROSS', 'FULL', 'JOIN', 'UNION',
        'AND', 'OR', 'NOT', 'SELECT', 'FROM', 'AS', 'BY', 'WITH',
    })

    # Find all sp_*/ly_* table references: (FROM|JOIN) table_name [AS] alias
    # Captures: group1=clause, group2=table, group3=alias (may be None)
    table_re = re.compile(
        r'\b(FROM|(?:INNER|LEFT|RIGHT|CROSS|FULL)?\s*JOIN)\s+'
        r'(`?(?:sp|ly)_\w+`?)'
        r'(?:\s+(?:AS\s+)?(`?\w+`?))?',
        re.IGNORECASE,
    )

    # Build list of (alias, clause_type, match_end_pos)
    refs = []
    for m in table_re.finditer(sql):
        clause = m.group(1).strip().upper()
        clause_type = "FROM" if clause == "FROM" else "JOIN"
        table_raw = m.group(2).strip('`')
        captured_alias = (m.group(3) or "").strip('`')
        # If the captured alias is a SQL keyword, the table has no alias — use table name.
        alias_raw = table_raw if (not captured_alias or captured_alias.upper() in _ALIAS_KEYWORDS) else captured_alias
        refs.append((alias_raw, clause_type, m.end()))

    if not refs:
        return sql  # No shared tables referenced — nothing to enforce.

    # Determine which aliases are already correctly scoped
    already_scoped = set()
    for alias, _, _ in refs:
        pattern = re.compile(
            rf'\b{re.escape(alias)}\.tenant_id\s*=\s*{re.escape(expected_literal)}',
            re.IGNORECASE,
        )
        if pattern.search(sql):
            already_scoped.add(alias)

    # Also consider bare "tenant_id = '...'" (no alias prefix) as scoping the FROM table
    bare_scoped = bool(re.search(
        rf'\btenant_id\s*=\s*{re.escape(expected_literal)}', sql, re.IGNORECASE
    ))
    from_alias = next((a for a, ct, _ in refs if ct == "FROM"), None)
    if bare_scoped and from_alias:
        already_scoped.add(from_alias)

    unscoped = [(a, ct, pos) for (a, ct, pos) in refs if a not in already_scoped]
    if not unscoped:
        return sql  # All references already scoped — nothing to do.

    log.warning(
        "Tenant isolation: injecting missing tenant_id filters",
        tenant_id=tenant_id,
        unscoped_aliases=[a for a, _, _ in unscoped],
    )

    # ── Step 1: Inject AND alias.tenant_id = '...' into each JOIN ON clause ──
    # Process in reverse order so injections don't shift positions.
    for alias, clause_type, match_end in sorted(unscoped, key=lambda x: x[2], reverse=True):
        if clause_type != "JOIN":
            continue
        # Find the ON keyword after this JOIN match
        on_re = re.compile(r'\bON\b', re.IGNORECASE)
        on_m = on_re.search(sql, match_end)
        if on_m:
            # Find the end of the ON condition (before next JOIN/WHERE/GROUP/ORDER/LIMIT/HAVING)
            end_re = re.compile(
                r'\b(?:INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|CROSS\s+JOIN|JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b',
                re.IGNORECASE,
            )
            end_m = end_re.search(sql, on_m.end())
            insert_at = end_m.start() if end_m else len(sql)
            # Find last non-whitespace before insert_at to place AND cleanly
            inject = f" AND {alias}.tenant_id = {expected_literal}"
            sql = sql[:insert_at].rstrip() + inject + " " + sql[insert_at:].lstrip()

    # ── Step 2: Inject tenant_id into WHERE clause for FROM table (if unscoped) ──
    from_unscoped = [a for a, ct, _ in unscoped if ct == "FROM"]
    if from_unscoped:
        alias = from_unscoped[0]
        cond = f"{alias}.tenant_id = {expected_literal}"
        where_re = re.compile(r'\bWHERE\b', re.IGNORECASE)
        where_m = where_re.search(sql)
        if where_m:
            # Insert as first condition after WHERE
            insert_at = where_m.end()
            sql = sql[:insert_at] + f" {cond} AND " + sql[insert_at:].lstrip()
        else:
            # No WHERE clause — insert before GROUP BY / ORDER BY / LIMIT / HAVING
            end_re = re.compile(
                r'\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b', re.IGNORECASE
            )
            end_m = end_re.search(sql)
            if end_m:
                sql = sql[:end_m.start()] + f"WHERE {cond} " + sql[end_m.start():]
            else:
                sql = sql.rstrip(';').rstrip() + f" WHERE {cond}"

    return sql


def _enforce_date_filter(sql: str, history_months: int) -> str:
    """
    Enforce the plan's data-history window on every NL query for integration users.

    Problem: follow-up questions ("what can I do to increase this?") let the LLM
    generate SQL without a date filter, pulling ALL historical data even though
    the user's plan only allows N months. The LLM prompt hint is advisory —
    this function is mandatory server-side enforcement.

    Strategy:
      - If the SQL already contains a created_at comparison (the LLM handled it),
        return unchanged.
      - Otherwise find the primary sp_*/ly_* FROM table alias and inject
        AND alias.created_at >= DATE_SUB(CURDATE(), INTERVAL N MONTH)
        into the WHERE clause (or create one if absent).

    Only applied to integration users (sp_*/ly_* tables) where the date column
    is always created_at. Not applied to own-DB users — unknown schema.
    """
    if not history_months:
        return sql

    # If LLM already applied a date filter on created_at, trust it.
    if re.search(r'\bcreated_at\s*[><=]', sql, re.IGNORECASE):
        return sql

    # Find the primary FROM sp_*/ly_* table with its alias.
    table_re = re.compile(
        r'\bFROM\s+(`?(?:sp|ly)_\w+`?)(?:\s+(?:AS\s+)?(`?\w+`?))?',
        re.IGNORECASE,
    )
    m = table_re.search(sql)
    if not m:
        return sql  # no shared table found — nothing to inject

    alias = (m.group(2) or m.group(1)).strip('`')
    date_cond = (
        f"{alias}.created_at >= DATE_SUB(CURDATE(), INTERVAL {history_months} MONTH)"
    )

    log.debug("Date filter enforced", alias=alias, history_months=history_months)

    where_re = re.compile(r'\bWHERE\b', re.IGNORECASE)
    where_m = where_re.search(sql)
    if where_m:
        insert_at = where_m.end()
        return sql[:insert_at] + f" {date_cond} AND " + sql[insert_at:].lstrip()

    end_re = re.compile(
        r'\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b', re.IGNORECASE
    )
    end_m = end_re.search(sql)
    if end_m:
        return sql[:end_m.start()] + f"WHERE {date_cond} " + sql[end_m.start():]

    return sql.rstrip(';').rstrip() + f" WHERE {date_cond}"


if _FORCE_HTTPS:
    from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
    app.add_middleware(HTTPSRedirectMiddleware)

@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    if _FORCE_HTTPS:
        # HSTS: tell browsers to only use HTTPS for 1 year, include subdomains
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# CORS — allow all origins in dev; lock down to registered embed origins in prod
# Set EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,... in production .env
_embed_origins_raw = os.getenv("EMBED_ALLOWED_ORIGINS", "")
_embed_origins = [o.strip() for o in _embed_origins_raw.split(",") if o.strip()]
# SEC-07: never default to "*" — always start from explicit localhost origins
_cors_origins = ["http://localhost:5173", "http://localhost:3000"] + _embed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-API-Key"],
)

app.include_router(embed_router)   # /embed/* — kept unversioned (live in partner iframes)
app.include_router(partner_router)  # /v1/partner/* — Partner API
# All user-facing routes are registered on this router and included under /v1
v1 = APIRouter(prefix="/v1", tags=["v1"])

# SEC-08: standard error envelope — all 4xx/5xx responses use {"ok": false, "error": "..."}
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": exc.detail},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    details = [
        {"field": ".".join(str(l) for l in e["loc"][1:]), "msg": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"ok": False, "error": "Invalid request parameters.", "details": details},
    )


@app.on_event("startup")
def startup_event():
    # Initialise connection pool first — all bootstraps below depend on DB access
    try:
        get_pool()
    except Exception as _pe:
        log.warning("DB connection pool init failed", error=str(_pe))
    try:
        init_users_table()
    except Exception as _be:
        log.warning("Users table bootstrap skipped", error=str(_be))
    try:
        bootstrap_integration_tables()
    except Exception as _be:
        log.warning("Integration bootstrap skipped in startup", error=str(_be))
    try:
        bootstrap_billing_tables()
    except Exception as _be:
        log.warning("Billing bootstrap skipped", error=str(_be))
    try:
        bootstrap_embed_tables()
    except Exception as _be:
        log.warning("Embed bootstrap skipped", error=str(_be))
    start_scheduler()
    log.info("DataMind backend started")

# In-memory build progress tracker
# Keyed by status_hash. Evicted after 1 hour so stale entries don't OOM long-running processes.
_build_status: Dict[str, Any] = {}
_BUILD_STATUS_TTL_S = 3600   # 1 hour
_BUILD_PROGRESS_MAX = 500    # max log lines kept per build

def _evict_build_status():
    """Remove entries older than TTL. Called on every new build start."""
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(seconds=_BUILD_STATUS_TTL_S)
    stale = [
        k for k, v in _build_status.items()
        if datetime.datetime.fromisoformat(v.get("started_at", "2000-01-01")) < cutoff
    ]
    for k in stale:
        del _build_status[k]


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS — key resolution (user settings first, env fallback only)
# ══════════════════════════════════════════════════════════════════════════════

def _safe(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return str(v)
    return v


# SEC-02: never expose raw exception strings to API clients
def _server_error(msg: str) -> HTTPException:
    return HTTPException(status_code=500, detail=msg)


def _base_query_response(**kwargs) -> dict:
    """Build a consistent /query response. Defaults suit error/empty cases."""
    base = {
        "success":         kwargs.get("success", True),
        "type":            kwargs.get("type", "data"),
        "message":         kwargs.get("message", None),
        "steps":           kwargs.get("steps", []),
        "columns":         kwargs.get("columns", []),
        "data":            kwargs.get("data", []),
        "row_count":       kwargs.get("row_count", 0),
        "analysis":        kwargs.get("analysis", None),
        "think_mode":      kwargs.get("think_mode", False),
        "conversation_id": kwargs.get("conversation_id", None),
        "data_as_of":      kwargs.get("data_as_of", None),
    }
    if "sql" in kwargs:
        base["sql"] = kwargs["sql"]
    if "multi_results" in kwargs:
        base["multi_results"] = kwargs["multi_results"]
    return base


# SEC-04: block LLM-generated SQL from running mutating statements
import re as _re
_SQL_MUTATION_RE = _re.compile(
    r'\b(DROP|DELETE|INSERT|UPDATE|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|REVOKE|CALL|EXEC)\b',
    _re.IGNORECASE
)

def _guard_sql(sql: str):
    m = _SQL_MUTATION_RE.search(sql)
    if m:
        raise HTTPException(
            status_code=400,
            detail=f"Generated query contains a disallowed statement: {m.group(0).upper()}"
        )


# SEC-06: encrypt DB passwords at rest using same Fernet key as integrations
def _get_pw_fernet():
    from cryptography.fernet import Fernet
    import base64, hashlib
    raw = os.getenv("ENCRYPTION_KEY") or os.getenv("SECRET_KEY", "fallback-key")
    key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(key)

def _encrypt_db_password(pw: str) -> str:
    if not pw:
        return pw
    return _get_pw_fernet().encrypt(pw.encode()).decode()

def _decrypt_db_password(token: str) -> str:
    """Decrypt a password token. Falls back to original value for plaintext migration."""
    if not token:
        return token
    try:
        return _get_pw_fernet().decrypt(token.encode()).decode()
    except Exception:
        # Plaintext password (pre-encryption migration) — return as-is
        return token


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
        cfg = dict(configs[idx])
        if cfg.get("password"):
            cfg["password"] = _decrypt_db_password(cfg["password"])  # SEC-06
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
    llm = (llm or "openai").lower().strip()
    if llm == "openai":
        key = s.get("openai_api_key", "").strip()
        if not key:
            # Last resort: server-level env var (for server admins only)
            key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            raise HTTPException(
                status_code=422,
                detail="AI service is not configured. Please contact support."
            )
        log.debug("Resolved OpenAI API key", user=user.get("email"), source="settings" if s.get("openai_api_key") else "env")
        return key

    elif llm == "gemini":
        key = s.get("gemini_api_key", "").strip()
        if not key:
            # Last resort: server-level env var (for server admins only)
            key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            raise HTTPException(
                status_code=422,
                detail="AI service is not configured. Please contact support."
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
                detail="AI service is not configured. Please contact support."
            )
        log.debug("Resolved DeepSeek API key", user=user.get("email"), source="settings" if s.get("deepseek_api_key") else "env")
        return key

    raise HTTPException(status_code=422, detail="Unsupported AI model selected.")


def _llm_has_key(user: dict, name: str) -> bool:
    """Return True if a real (non-placeholder) key exists for this LLM."""
    from llm import _is_real_key
    s = user.get("settings", {})
    if name == "openai":
        return _is_real_key(s.get("openai_api_key", "")) or _is_real_key(os.getenv("OPENAI_API_KEY", ""))
    if name == "gemini":
        return _is_real_key(s.get("gemini_api_key", "")) or _is_real_key(os.getenv("GEMINI_API_KEY", ""))
    if name == "deepseek":
        return _is_real_key(s.get("deepseek_api_key", "")) or _is_real_key(os.getenv("DEEPSEEK_API_KEY", ""))
    return False


def _get_llm(user: dict) -> str:
    """Return the LLM to use, in LLM_PRIORITY order (default: openai, gemini, deepseek).

    The system-wide priority order always wins so that OpenAI is tried first
    (falling back to Gemini, then DeepSeek) regardless of any per-user
    `default_llm` setting from the (currently hidden) BYOK Settings UI.
    """
    from llm import get_llm_priority
    s = user.get("settings", {})
    preferred = (s.get("default_llm") or "").lower()
    priority = get_llm_priority()
    order = priority + ([preferred] if preferred and preferred not in priority else [])
    for candidate in order:
        if _llm_has_key(user, candidate):
            if candidate != preferred and preferred:
                log.info("LLM key fallback", preferred=preferred, using=candidate, user=user.get("email"))
            return candidate
    return order[0] if order else "openai"  # let _resolve_api_key raise the informative error


def _effective_llm(user: dict, requested: str) -> str:
    """Use requested LLM if a key exists for it, otherwise fall back to _get_llm()."""
    return requested if _llm_has_key(user, requested) else _get_llm(user)


def _status_key(email: str, db_config: dict) -> str:
    import hashlib
    key = f"{email}:{db_config.get('host','env')}:{db_config.get('database','env')}"
    return hashlib.md5(key.encode()).hexdigest()[:16]


def _validate_table_column(schemas: dict, table: str, column: str):
    """Raise 400 if table or column isn't in the user's real schema (prevents SQL injection)."""
    if table not in schemas:
        raise HTTPException(status_code=400, detail=f"Table '{table}' not found.")
    col_names = [c["name"] for c in schemas[table]]
    if column not in col_names:
        raise HTTPException(status_code=400, detail=f"Column '{column}' not found in table '{table}'.")


def _run_sql(conn, sql: str, title: str) -> dict:
    cursor = conn.cursor()
    log.debug("Executing SQL", title=title, sql_preview=f"{sql[:60]}…" if len(sql) > 60 else sql)
    cursor.execute(sql)
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    data = [{c: _safe(v) for c, v in zip(cols, row)} for row in rows]
    log.debug("SQL result", title=title, rows=len(data))
    return {"title": title, "columns": cols, "data": data, "row_count": len(data)}


# Maps analytics template IDs to FEATURE_COST operation types.
_ANALYTICS_OP = {
    "customer_rfm":        "rfm_analysis",
    "customer_cohort":     "cohort_analysis",
    "basket_analysis":     "basket_analysis",
    "growth_metrics":      "growth_metrics",
    "cashier_performance": "employee_performance",
    "product_velocity":    "product_velocity",
    "payment_methods":     "payment_breakdown",
    "location_comparison": "location_comparison",
}


def _charge_op(email: str, op_type: str, rows: int):
    """Charge unified tokens for one analytics operation. Never raises."""
    try:
        tokens = calculate_tokens(op_type, rows_returned=rows)
        charge_tokens(email, tokens, op_type, rows_returned=rows)
    except Exception as _ce:
        log.warning("_charge_op failed silently", op=op_type, error=str(_ce))


def _apply_row_limit(result: dict, row_limit: int) -> dict:
    """Truncate result rows to plan row_limit. Applied to analytics when no date filter ran."""
    if result.get("data") and len(result["data"]) > row_limit:
        result["data"]      = result["data"][:row_limit]
        result["row_count"] = len(result["data"])
    return result


# ══════════════════════════════════════════════════════════════════════════════
# REQUEST LOGGING MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach X-Request-ID to every response for tracing.
    Honours an incoming header so clients can correlate their own IDs."""
    import uuid
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


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
# EMBED SECURITY HEADERS
# ══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def embed_security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/embed"):
        origins = os.getenv("EMBED_ALLOWED_ORIGINS", "*")
        response.headers["Content-Security-Policy"] = f"frame-ancestors {origins}"
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND CACHE BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def _background_build(email: str, db_config: dict, llm: str, api_key: str):
    _evict_build_status()  # prune entries older than 1 hour before adding a new one
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
            if len(logs) < _BUILD_PROGRESS_MAX:
                logs.append(msg)
            elif len(logs) == _BUILD_PROGRESS_MAX:
                logs.append("… (progress log truncated)")
            log.debug("Cache build progress", user=email, step=msg)

        def llm_caller(prompt, system, llm_name, max_tokens):
            return call_llm(prompt, system, llm_name, max_tokens, api_key=api_key, user_email=email, operation="cache_build")

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

@app.get("/health")  # kept at root — load balancers and monitoring expect /health not /v1/health
@_limiter.limit(RL_READ)
def health(request: Request):
    log.debug("Health check")
    return {"status": "ok", "version": "3.0.0"}



@v1.get("/llm/models")
@_limiter.limit(RL_READ)
def llm_models(request: Request, user: dict = Depends(current_user)):
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


@v1.post("/auth/register")
@_limiter.limit(RL_AUTH)
def register(request: Request, req: RegisterRequest):
    log.info("Register attempt", email=req.email)
    user = create_user(req.name, req.email, req.password)
    token = create_token(req.email)
    try:
        start_trial(req.email)
    except Exception as _te:
        log.warning("Trial start skipped", email=req.email, error=str(_te))
    log.info("User registered", email=req.email)
    locale = user.get("settings", {}).get("locale", {})
    return JSONResponse(status_code=201, content={"token": token, "user": {"name": user["name"], "email": user["email"], "locale": locale}})


@v1.post("/auth/login")
@_limiter.limit(RL_AUTH_LOGIN)
def login(request: Request, req: LoginRequest):
    log.info("Login attempt", email=req.email)
    user = authenticate_user(req.email, req.password)
    token = create_token(req.email)
    log.info("User logged in", email=req.email)
    locale = user.get("settings", {}).get("locale", {})
    return {"token": token, "user": {"name": user["name"], "email": user["email"], "locale": locale}}


@v1.post("/auth/sso-handoff")
@_limiter.limit(RL_READ)
def auth_sso_handoff(request: Request, user: dict = Depends(current_user)):
    """Issue a short-lived one-time link so a user already authenticated inside
    a partner iframe (e.g. Salesplay Web Embed) can open the standalone
    DataMind app without re-entering credentials."""
    token = create_sso_handoff_token(user["email"])
    return {"token": token}


class SSOLoginRequest(BaseModel):
    token: str


@v1.post("/auth/sso-login")
@_limiter.limit(RL_AUTH_LOGIN)
def auth_sso_login(request: Request, body: SSOLoginRequest):
    """Exchange a one-time embed handoff token for a normal session token."""
    email = redeem_sso_handoff_token(body.token)
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=401, detail="Account not found")
    token = create_token(email)
    log.info("SSO login from embed", email=email)
    locale = user.get("settings", {}).get("locale", {})
    return {"token": token, "user": {"name": user["name"], "email": user["email"], "locale": locale}}


@v1.get("/auth/me")
@_limiter.limit(RL_READ)
def me(request: Request, user: dict = Depends(current_user)):
    locale = user.get("settings", {}).get("locale", {})
    return {"name": user["name"], "email": user["email"], "locale": locale}


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
    llm: str = "openai"   # which LLM to use for the cache build


@v1.post("/onboarding/validate-key")
@_limiter.limit(RL_WRITE)
def onboarding_validate_key(request: Request, req: ValidateKeyRequest,
                             user: dict = Depends(current_user)):
    """Step 1 of onboarding: test the LLM API key."""
    log.info("Onboarding: validating LLM key", user=user["email"], llm=req.llm)
    result = validate_llm_key(req.llm, req.api_key)
    if result["ok"]:
        # Save key to user settings immediately
        patch = {}
        if req.llm == "openai":
            patch["openai_api_key"] = req.api_key
        elif req.llm == "gemini":
            patch["gemini_api_key"] = req.api_key
        else:
            patch["deepseek_api_key"] = req.api_key
        patch["default_llm"] = req.llm
        update_user_settings(user["email"], patch)
        log.info("Onboarding: LLM key saved", user=user["email"], llm=req.llm)
    return result


@v1.post("/onboarding/test-db")
@_limiter.limit(RL_WRITE)
def onboarding_test_db(request: Request, req: OnboardingDBRequest,
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
        return {"ok": False, "error": "Failed to connect. Check your credentials and try again."}


@v1.post("/onboarding/connect-db")
@_limiter.limit(RL_WRITE)
def onboarding_connect_db(request: Request, req: OnboardingDBRequest,
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
        "database": req.database, "user": req.user,
        "password": _encrypt_db_password(req.password),  # SEC-06
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
    # Pass plaintext config for cache build (encrypted version already persisted above)
    plaintext_dict = {**db_dict, "password": req.password}
    background_tasks.add_task(_background_build, user["email"], plaintext_dict, llm, api_key)
    return {"ok": True, "building_cache": True, "db_index": new_idx}


# ══════════════════════════════════════════════════════════════════════════════
# SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

class SettingsPatch(BaseModel):
    openai_api_key: Optional[str] = None
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


@v1.get("/settings")
@_limiter.limit(RL_READ)
def get_settings(request: Request, user: dict = Depends(current_user)):
    log.debug("Get settings", user=user["email"])
    s = get_user_settings(user["email"])
    safe_configs = []
    for cfg in s.get("db_configs", []):
        c = dict(cfg)
        if c.get("password"):
            c["password"] = "••••••••"
        safe_configs.append(c)
    _HIDDEN = {"openai_api_key", "gemini_api_key", "deepseek_api_key", "default_llm"}
    safe = {k: v for k, v in {**s, "db_configs": safe_configs}.items() if k not in _HIDDEN}
    # Expose AI availability as a boolean — no provider name or key value leaked
    _has_key = (
        bool(s.get("openai_api_key", "").strip()  or os.getenv("OPENAI_API_KEY", "").strip()) or
        bool(s.get("gemini_api_key", "").strip()  or os.getenv("GEMINI_API_KEY", "").strip()) or
        bool(s.get("deepseek_api_key", "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip())
    )
    safe["has_llm_key"] = _has_key
    return safe


@v1.patch("/settings")
@_limiter.limit(RL_WRITE)
def patch_settings(request: Request, req: SettingsPatch, user: dict = Depends(current_user)):
    patch = {k: v for k, v in req.dict().items() if v is not None}
    log.info("Patch settings", user=user["email"], keys=list(patch.keys()))
    updated = update_user_settings(user["email"], patch)
    return {"ok": True}


@v1.post("/settings/db")
@_limiter.limit(RL_WRITE)
def add_db_config(request: Request, cfg: DBConfig, background_tasks: BackgroundTasks,
                  user: dict = Depends(current_user)):
    log.info("Add DB config", user=user["email"], db_name=cfg.name, host=cfg.host)
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    cfg_dict = cfg.dict()
    cfg_dict["password"] = _encrypt_db_password(cfg_dict["password"])  # SEC-06
    configs.append(cfg_dict)
    new_idx = len(configs) - 1
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": new_idx})
    llm = _get_llm(user)
    try:
        api_key = _resolve_api_key(user, llm)
        # Pass plaintext config for cache build (encrypted version already persisted above)
        background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm, api_key)
        return JSONResponse(status_code=201, content={"ok": True, "building_cache": True})
    except HTTPException:
        log.warning("Skipping cache build — no API key set", user=user["email"])
        return JSONResponse(status_code=201, content={"ok": True, "building_cache": False, "warning": "Add an API key in Settings to build the analytics cache."})


@v1.put("/settings/db/{index}")
@_limiter.limit(RL_WRITE)
def update_db_config(request: Request, index: int, cfg: DBConfig,
                     background_tasks: BackgroundTasks,
                     user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Database configuration not found.")
    invalidate_cache(user["email"], configs[index])
    cfg_dict = cfg.dict()
    cfg_dict["password"] = _encrypt_db_password(cfg_dict["password"])  # SEC-06
    configs[index] = cfg_dict
    update_user_settings(user["email"], {"db_configs": configs})
    llm = _get_llm(user)
    try:
        api_key = _resolve_api_key(user, llm)
        # Pass plaintext config for cache build (encrypted version already persisted above)
        background_tasks.add_task(_background_build, user["email"], cfg.dict(), llm, api_key)
    except HTTPException:
        pass
    return {"ok": True}


@v1.delete("/settings/db/{index}")
@_limiter.limit(RL_WRITE)
def delete_db_config(request: Request, index: int, user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Database configuration not found.")
    log.info("Delete DB config", user=user["email"], index=index)
    invalidate_cache(user["email"], configs[index])
    configs.pop(index)
    update_user_settings(user["email"], {"db_configs": configs, "active_db_index": 0})
    return {"ok": True}


@v1.post("/settings/db/{index}/activate")
@_limiter.limit(RL_WRITE)
def activate_db(request: Request, index: int, background_tasks: BackgroundTasks,
                user: dict = Depends(current_user)):
    s = get_user_settings(user["email"])
    configs = s.get("db_configs", [])
    if index < 0 or index >= len(configs):
        raise HTTPException(status_code=404, detail="Database configuration not found.")
    update_user_settings(user["email"], {"active_db_index": index})
    db_config = dict(configs[index])
    if db_config.get("password"):
        db_config["password"] = _decrypt_db_password(db_config["password"])  # SEC-06
    if not get_cache(user["email"], db_config):
        llm = _get_llm(user)
        try:
            api_key = _resolve_api_key(user, llm)
            background_tasks.add_task(_background_build, user["email"], db_config, llm, api_key)
            return {"ok": True, "active": index, "building_cache": True}
        except HTTPException:
            return {"ok": True, "active": index, "building_cache": False}
    return {"ok": True, "active": index, "building_cache": False}


@v1.post("/settings/db/test")
@_limiter.limit(RL_WRITE)
def test_db_connection(request: Request, cfg: DBConfig, user: dict = Depends(current_user)):
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
        return {"ok": False, "error": "Connection failed. Check your host, port, credentials, and firewall settings."}


# ══════════════════════════════════════════════════════════════════════════════
# CACHE
# ══════════════════════════════════════════════════════════════════════════════

@v1.get("/cache/status")
@_limiter.limit(RL_READ)
def cache_status(request: Request, user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    sk = _status_key(user["email"], db_config)
    cache_info = get_cache_status(user["email"], db_config)
    build_info = _build_status.get(sk, {})
    return {**cache_info, "build": build_info}


@v1.get("/cache/progress")
@_limiter.limit(RL_READ)
def cache_progress(request: Request, user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    sk = _status_key(user["email"], db_config)
    return _build_status.get(sk, {"status": "unknown"})


@v1.post("/cache/rebuild")
@_limiter.limit(RL_WRITE)
def rebuild_cache(request: Request, background_tasks: BackgroundTasks,
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

@v1.get("/tables")
@_limiter.limit(RL_READ)
def list_tables(request: Request, user: dict = Depends(current_user)):
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
            raise _server_error("Failed to load tables.")

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
        raise _server_error("Failed to load integration tables.")


@v1.get("/tables/{table_name}/columns")
@_limiter.limit(RL_READ)
def get_table_columns(request: Request, table_name: str, user: dict = Depends(current_user)):
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
        raise _server_error("Failed to load column information.")


# ══════════════════════════════════════════════════════════════════════════════
# DISCOVER
# ══════════════════════════════════════════════════════════════════════════════

_PROVIDER_TEMPLATE_LOADERS = {
    "salesplay": lambda: __import__("providers.salesplay.analytics", fromlist=["TEMPLATES"]).TEMPLATES,
    "loyverse":  lambda: __import__("providers.loyverse.analytics",  fromlist=["TEMPLATES"]).TEMPLATES,
}

_PROVIDER_RUNNER_FACTORIES = {
    "salesplay": lambda: __import__("providers.salesplay.analytics", fromlist=["run_salesplay_analytics"]).run_salesplay_analytics,
    "loyverse":  lambda: __import__("providers.loyverse.analytics",  fromlist=["run_loyverse_analytics"]).run_loyverse_analytics,
}

# Maps report section IDs → provider-specific template IDs
_PROVIDER_SECTION_MAP = {
    "salesplay": {
        "revenue_trend":       "revenue_trend",
        "revenue_by_category": "category_performance",
        "revenue_by_location": "shop_performance",
        "growth_metrics":      "daily_summary",
        "hourly_pattern":      "hourly_performance",
        "top_products":        "top_products",
        "top_customers":       "customer_analysis",
        "payment_methods":     "payment_breakdown",
        "cashier_performance": "hourly_performance",
    },
    "loyverse": {
        "revenue_trend":       "revenue_trend",
        "revenue_by_category": "category_breakdown",
        "revenue_by_location": "store_performance",
        "top_products":        "top_products",
        "top_customers":       "customer_insights",
        "payment_methods":     "payment_methods",
        "cashier_performance": "employee_sales",
    },
}

# Receipts table name pattern per provider (format with prefix=)
_PROVIDER_RECEIPTS_TABLE = {
    "salesplay": "{prefix}receipts",   # no underscore separator
    "loyverse":  "{prefix}_receipts",  # with underscore separator
}


def _get_provider_kpis(conn, table_prefix: str, provider_id: str) -> Dict:
    tbl = _PROVIDER_RECEIPTS_TABLE.get(provider_id, "{prefix}receipts").format(prefix=table_prefix)
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT ROUND(SUM(total_money),2), COUNT(*), ROUND(AVG(total_money),2), "
            f"MIN(created_at), MAX(created_at) "
            f"FROM `{tbl}` WHERE created_at IS NOT NULL AND receipt_type='SALE'"
        )
        row = cursor.fetchone()
        if row:
            keys = ["total_revenue","total_transactions","avg_transaction","from_date","to_date"]
            return {k: _safe(v) for k, v in zip(keys, row)}
    except Exception as e:
        log.warning("Provider KPI fetch failed", provider=provider_id, error=str(e))
    return {}


def _get_integration_catalogue(user_email: str) -> List[Dict]:
    """Build catalogue items from the user's integrations using pre-built provider templates.
    Shows templates for any integration that has a table_prefix (i.e. tables were created) —
    status 'active', 'syncing', and even 'error' are all eligible so users can still browse
    analytics while sync is in flight or after a transient error.
    """
    try:
        integrations = list_integrations(user_email)
    except Exception as e:
        log.warning("Integration catalogue: could not list integrations", user=user_email, error=str(e))
        return []

    log.info("Integration catalogue: integrations found",
             user=user_email,
             count=len(integrations),
             summary=[{"provider": i.get("provider_id"), "status": i.get("status")} for i in integrations])

    items: List[Dict] = []
    for integ in integrations:
        provider_id = integ.get("provider_id")
        status = integ.get("status")
        if not integ.get("table_prefix"):
            log.debug("Integration catalogue: skipping — no table_prefix",
                      user=user_email, provider=provider_id, status=status)
            continue
        loader = _PROVIDER_TEMPLATE_LOADERS.get(provider_id)
        if not loader:
            log.debug("Integration catalogue: no templates module for provider",
                      user=user_email, provider=provider_id)
            continue
        try:
            templates = loader()
        except Exception as e:
            log.warning("Integration catalogue: failed to load templates",
                        user=user_email, provider=provider_id, error=str(e))
            continue
        label = integ.get("display_label") or provider_id.title()
        for tid, t in templates.items():
            items.append({
                "id": tid,
                "title": t.get("title", tid),
                "description": t.get("description", ""),
                "type": t.get("type", "table"),
                "icon": t.get("icon", "📊"),
                "category": label,
                "provider": provider_id,
            })
    log.info("Integration catalogue built", user=user_email, item_count=len(items))
    return items


@v1.get("/discover")
@_limiter.limit(RL_READ)
def discover(request: Request, user: dict = Depends(current_user)):
    db_config = _resolve_db(user)
    integration_items = _get_integration_catalogue(user["email"])
    cache = get_cache(user["email"], db_config)

    if cache:
        own_catalogue = cache.get("catalogue", [])
        merged = integration_items + own_catalogue
        log.info("Discover: serving merged catalogue",
                 user=user["email"],
                 own_templates=len(own_catalogue),
                 integration_templates=len(integration_items),
                 total=len(merged))
        return {
            "catalogue": merged,
            "from_cache": True,
            "built_at": cache.get("built_at"),
            "template_count": len(merged),
        }

    sk = _status_key(user["email"], db_config)
    build_info = _build_status.get(sk, {})
    if build_info.get("status") == "building":
        log.info("Discover: own-DB cache building, returning integration items only",
                 user=user["email"], integration_templates=len(integration_items))
        return {"catalogue": integration_items, "from_cache": False, "building": True,
                "progress": build_info.get("progress", [])}

    if integration_items:
        log.info("Discover: serving integration-only catalogue (no own-DB cache yet)",
                 user=user["email"], integration_templates=len(integration_items))
        return {"catalogue": integration_items, "from_cache": False,
                "building": False, "needs_build": False, "template_count": len(integration_items)}

    log.info("Discover: no cache and no integrations — needs_build", user=user["email"])
    return {"catalogue": [], "from_cache": False, "building": False, "needs_build": True}


# ══════════════════════════════════════════════════════════════════════════════
# NL QUERY
# ══════════════════════════════════════════════════════════════════════════════

def _run_think_analysis(question: str, columns: list, data: list,
                        llm: str, api_key: str, user_email: str) -> str:
    """Second LLM call for Think Mode: analyse SQL results and answer the question.
    call_llm() handles token charging via charge_ai_usage() automatically."""
    # Format top 50 rows as compact CSV for the prompt
    sample = data[:50]
    header = ", ".join(columns)
    rows_text = "\n".join(
        ", ".join(str(row.get(c, "")) for c in columns)
        for row in sample
    )
    truncation_note = (
        f"\n(Showing first 50 of {len(data)} rows)" if len(data) > 50 else ""
    )
    prompt = (
        f"The user asked: \"{question}\"\n\n"
        f"Here is the query result ({len(data)} rows total):{truncation_note}\n"
        f"{header}\n{rows_text}\n\n"
        "Answer the user's question directly using this data. "
        "Be specific with numbers and values from the results. "
        "If the question asks for advice or recommendations, give 2-3 concrete, "
        "actionable suggestions based on what the data shows. "
        "Keep your response under 150 words. "
        "Write in plain sentences only — no markdown, no asterisks, no bullet symbols."
    )
    return call_llm(
        prompt,
        system=(
            "You are a concise business analyst. Answer based only on the provided data. "
            "Use plain text only — never use markdown, asterisks, bold markers (**), "
            "underscores, or any special formatting symbols."
        ),
        llm=llm,
        max_tokens=400,
        api_key=api_key,
        user_email=user_email,
        operation="think",
    )


class NLQueryRequest(BaseModel):
    question:        str
    llm:             str  = "openai"
    think_mode:      bool = False
    conversation_id: str  = None  # optional — enables conversation memory


@v1.post("/query")
@_limiter.limit(RL_COMPUTE)
def natural_language_query(request: Request, req: NLQueryRequest, user: dict = Depends(current_user)):
    log = get_logger(__name__)   # local — lets us rebind with log = log.bind(...) later without UnboundLocalError
    conn = None
    steps: list = []
    conv_id = req.conversation_id or None

    # ── AI limit check ────────────────────────────────────────────────────────
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        log.warning("AI limit exceeded", user=user["email"])
        return _base_query_response(
            success=False, type="error", conversation_id=conv_id,
            message="You've reached your AI usage limit. Please upgrade your plan to continue.",
        )

    llm = _effective_llm(user, req.llm)
    log.info("NL query", user=user["email"], llm=llm, question=req.question[:80])
    api_key = _resolve_api_key(user, llm)
    history = get_plan_history_limit(user["email"])
    s = user.get("settings", {})
    nl_tenant_id = None
    nl_shop_timezone = "UTC"
    nl_last_sync_at = None
    loyverse_hints: list = []

    # ── DB connection + table scope setup ────────────────────────────────────
    try:
        if s.get("db_configs"):
            conn = get_connection(_resolve_db(user))
            tables_filter = None
        else:
            user_conns = get_user_connections(user["email"])
            prefixes = [c.get("table_prefix", "") for c in user_conns if c.get("table_prefix")]
            if not prefixes:
                return _base_query_response(
                    success=False, type="error", conversation_id=conv_id,
                    message="No data source connected yet. Please connect a provider in Settings first.",
                )

            # SalesPlay uses shared sp_* tables scoped by tenant_id.
            # Loyverse uses per-user views named {prefix}_* (tenant isolation
            # baked into each view's WHERE clause at creation time).
            _SALESPLAY_SHARED_TABLES = [
                "sp_receipts", "sp_receipt_line_items", "sp_products",
                "sp_customers", "sp_categories", "sp_shops", "sp_payment_types",
            ]
            _LOYVERSE_VIEW_SUFFIXES = [
                "receipts", "receipt_line_items", "products",
                "customers", "categories", "stores", "employees", "payment_line_items",
            ]
            tables_filter = []
            nl_tenant_id = None
            loyverse_hints = []
            for conn_info in user_conns:
                pid    = conn_info.get("provider_id", "")
                prefix = conn_info.get("table_prefix", "")
                if pid == "salesplay":
                    tables_filter.extend(_SALESPLAY_SHARED_TABLES)
                    if not nl_tenant_id:
                        nl_tenant_id = prefix
                        _raw_sync = conn_info.get("last_sync_at")
                        if _raw_sync:
                            nl_last_sync_at = str(_raw_sync)
                elif pid == "loyverse" and prefix:
                    tables_filter.extend([f"{prefix}_{_sfx}" for _sfx in _LOYVERSE_VIEW_SUFFIXES])
                    loyverse_hints.append(
                        f"The {prefix}_customers table has pre-aggregated total_spent and "
                        f"total_visits columns — use these directly for customer spending/"
                        f"frequency analysis instead of joining {prefix}_receipts. "
                        f"The {prefix}_receipt_line_items table already has item_name and "
                        f"category_id — prefer these over joining {prefix}_products."
                    )
            tables_filter = list(dict.fromkeys(tables_filter))
            if not tables_filter:
                return _base_query_response(
                    success=False, type="error", conversation_id=conv_id,
                    message="Data sync is not complete yet. Please wait for the sync to finish and try again.",
                )
            log.debug("NL query scoped to shared tables",
                      user=user["email"], table_count=len(tables_filter), tenant_id=nl_tenant_id)
            conn = _get_internal_conn()
    except Exception as _setup_err:
        log.error("NL query setup failed", user=user["email"], error=str(_setup_err))
        return _base_query_response(
            success=False, type="error", conversation_id=conv_id,
            message="Could not connect to your data source. Please check your connection settings.",
        )

    # Bind user + tenant_id to every log call for the rest of this request
    # so we can filter logs by user or tenant without grepping manually.
    log = log.bind(user=user["email"], tenant_id=nl_tenant_id or "own-db")

    try:
        # ── Conversation history ──────────────────────────────────────────────
        conv_history = ""
        if conv_id:
            try:
                conv_history = _conv.get_history_for_prompt(conv_id)
            except Exception as _he:
                log.warning("Could not load conversation history", conv_id=conv_id, error=str(_he))

        # ── Schema fetch ──────────────────────────────────────────────────────
        steps.append({"label": "Loading your data schema", "status": "done"})
        schemas = get_table_schemas(conn, tables_filter)
        all_fkeys = get_foreign_keys(conn)
        if tables_filter is not None:
            user_tables = set(tables_filter)
            fkeys = [
                fk for fk in all_fkeys
                if fk["table"] in user_tables and fk["ref_table"] in user_tables
            ]
        else:
            fkeys = all_fkeys

        # Fetch SalesPlay shop timezone for CONVERT_TZ-aware date comparisons.
        if nl_tenant_id:
            try:
                _tz_cur = conn.cursor()
                _tz_cur.execute(
                    "SELECT timezone FROM sp_shops WHERE tenant_id = %s "
                    "AND timezone IS NOT NULL AND timezone != '' LIMIT 1",
                    (nl_tenant_id,)
                )
                _tz_row = _tz_cur.fetchone()
                if _tz_row:
                    nl_shop_timezone = _tz_row[0] or "UTC"
            except Exception as _tze:
                log.debug("Could not fetch shop timezone, defaulting to UTC", error=str(_tze))

        # ── Question classification ───────────────────────────────────────────
        steps.append({"label": "Analyzing your question", "status": "done"})
        table_names_str = ", ".join(list(schemas.keys())[:20])
        classification = classify_question(
            req.question, table_names_str, llm, api_key, user["email"],
            app_name=_APP_NAME, conversation_history=conv_history,
        )
        q_type = classification.get("type", "data_query")

        row_limit = history["row_limit"]
        extra_hints = " ".join(loyverse_hints) if loyverse_hints else ""
        is_integration = s.get("db_configs") is None

        # ── Conversational / greeting ─────────────────────────────────────────
        if q_type == "conversational":
            response_text = classification.get(
                "response",
                f"Hello! I'm {_APP_NAME}, your AI data assistant. "
                "Ask me anything about your data — for example: "
                "'Show me sales from last month' or 'Who are my top customers?'"
            )
            if conv_id:
                try:
                    _conv.save_message(conv_id, "user", req.question)
                    _conv.save_message(conv_id, "assistant", response_text)
                except Exception:
                    pass
            return _base_query_response(
                success=True, type="conversational", message=response_text,
                steps=steps, conversation_id=conv_id, think_mode=req.think_mode,
            )

        # ── Needs clarification ───────────────────────────────────────────────
        if q_type == "clarification_needed":
            clarification = classification.get(
                "clarification",
                "Could you provide more details about what you're looking for? "
                "For example, specify a time period, a product category, or a metric."
            )
            if conv_id:
                try:
                    _conv.save_message(conv_id, "user", req.question)
                    _conv.save_message(conv_id, "assistant", clarification)
                except Exception:
                    pass
            return _base_query_response(
                success=True, type="clarification", message=clarification,
                steps=steps, conversation_id=conv_id, think_mode=req.think_mode,
            )

        # ── Multi-step query ──────────────────────────────────────────────────
        if q_type == "multi_step":
            sub_questions = classification.get("sub_questions", [])
            if len(sub_questions) < 2:
                q_type = "data_query"  # decomposition failed — fall through to single query
            else:
                steps.append({"label": f"Breaking into {len(sub_questions)} sub-queries", "status": "done"})
                step_results = []
                for i, sub_q in enumerate(sub_questions, 1):
                    steps.append({"label": f"Running query {i}: {sub_q[:60]}", "status": "running"})
                    try:
                        sub_sql = query_to_sql(
                            sub_q, schemas, llm, fkeys, api_key=api_key,
                            user_email=user["email"], history_months=history["months"],
                            tenant_id=nl_tenant_id if is_integration else None,
                            row_limit=row_limit, conversation_history=conv_history,
                            extra_schema_hints=extra_hints, shop_timezone=nl_shop_timezone,
                        )
                        if nl_tenant_id:
                            try:
                                sub_sql = _enforce_tenant_isolation(sub_sql, nl_tenant_id)
                            except ValueError:
                                steps[-1]["status"] = "skipped"
                                continue
                            if nl_tenant_id not in sub_sql:
                                steps[-1]["status"] = "skipped"
                                continue
                            sub_sql = _enforce_date_filter(sub_sql, history["months"])
                        # SEC-04: block mutations in sub-queries too
                        try:
                            _guard_sql(sub_sql)
                        except HTTPException:
                            steps[-1]["status"] = "skipped"
                            continue
                        sub_cursor = conn.cursor()
                        _set_query_timeout(sub_cursor)
                        sub_cursor.execute(sub_sql)
                        sub_cols = [d[0] for d in sub_cursor.description]
                        sub_rows = sub_cursor.fetchall()
                        sub_data = [
                            {k: _safe(v) for k, v in dict(zip(sub_cols, row)).items()}
                            for row in sub_rows
                        ]
                        if len(sub_data) > row_limit:
                            sub_data = sub_data[:row_limit]
                        step_results.append({
                            "question": sub_q,
                            "columns": sub_cols,
                            "data": sub_data,
                            "row_count": len(sub_data),
                        })
                        steps[-1]["status"] = "done"
                    except Exception as _sub_err:
                        log.warning("Multi-step sub-query failed", sub_q=sub_q[:60], error=str(_sub_err))
                        steps[-1]["status"] = "failed"

                if not step_results:
                    return _base_query_response(
                        success=False, type="error", steps=steps, conversation_id=conv_id,
                        message="I couldn't retrieve data for your question. Please try rephrasing it.",
                    )

                steps.append({"label": "Combining results", "status": "running"})
                analysis = synthesize_multi_step_answer(
                    req.question, step_results, llm, api_key, user["email"]
                )
                steps[-1]["status"] = "done"

                _charge_op(user["email"], "nl_query_rows", sum(r["row_count"] for r in step_results))

                if conv_id:
                    try:
                        _conv.save_message(conv_id, "user", req.question)
                        _conv.save_message(
                            conv_id, "assistant",
                            analysis or f"Found results across {len(step_results)} queries.",
                            row_count=sum(r["row_count"] for r in step_results),
                        )
                    except Exception as _ce:
                        log.warning("Failed to save multi-step conversation", conv_id=conv_id, error=str(_ce))

                primary = step_results[0]
                return _base_query_response(
                    success=True, type="multi_step", message=analysis,
                    steps=steps,
                    columns=primary["columns"], data=primary["data"], row_count=primary["row_count"],
                    analysis=analysis, think_mode=req.think_mode,
                    conversation_id=conv_id, data_as_of=nl_last_sync_at,
                    multi_results=step_results,
                )

        # ── Single data query ─────────────────────────────────────────────────
        steps.append({"label": "Generating SQL query", "status": "running"})
        sql = query_to_sql(
            req.question, schemas, llm, fkeys, api_key=api_key,
            user_email=user["email"], history_months=history["months"],
            tenant_id=nl_tenant_id if is_integration else None,
            row_limit=row_limit, conversation_history=conv_history,
            extra_schema_hints=extra_hints, shop_timezone=nl_shop_timezone,
        )
        steps[-1]["status"] = "done"

        # SEC-15: enforce tenant isolation for integration users.
        if nl_tenant_id:
            try:
                sql = _enforce_tenant_isolation(sql, nl_tenant_id)
            except ValueError as _te:
                log.warning("Tenant isolation enforcement failed", user=user["email"], error=str(_te))
                return _base_query_response(
                    success=False, type="error", steps=steps, conversation_id=conv_id,
                    message="I couldn't safely scope your query to your account. Please try rephrasing your question.",
                )
            # Fail-closed: refuse to execute if tenant_id was not successfully injected.
            if nl_tenant_id not in sql:
                log.error("Tenant isolation enforcement failed — refusing to execute",
                          user=user["email"], tenant_id=nl_tenant_id, sql=sql[:300])
                return _base_query_response(
                    success=False, type="error", steps=steps, conversation_id=conv_id,
                    message="Could not generate a safely scoped query. Please rephrase your question.",
                )
            # Mandatory date-window enforcement (plan limit, not advisory).
            sql = _enforce_date_filter(sql, history["months"])

        # SEC-04: block LLM-generated mutations before execution.
        try:
            _guard_sql(sql)
        except HTTPException:
            log.warning("Mutation guard blocked query", user=user["email"], sql=sql[:200])
            return _base_query_response(
                success=False, type="error", steps=steps, conversation_id=conv_id,
                message=(
                    "I can only run read-only queries on your data. "
                    "If you meant to ask about your data, try rephrasing — "
                    "for example: 'Show me all orders' instead of 'Delete all orders'."
                ),
            )

        steps.append({"label": "Running your query", "status": "running"})
        cursor = conn.cursor()
        _set_query_timeout(cursor)
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        data = [{k: _safe(v) for k, v in dict(zip(columns, row)).items()} for row in rows]
        if len(data) > row_limit:
            data = data[:row_limit]
        steps[-1]["status"] = "done"
        log.info("NL query complete", user=user["email"], rows=len(data), conv_id=conv_id or "stateless")
        _charge_op(user["email"], "nl_query_rows", len(data))

        # ── Think mode ────────────────────────────────────────────────────────
        analysis = None
        if req.think_mode and data:
            steps.append({"label": "Analyzing results", "status": "running"})
            try:
                analysis = _run_think_analysis(req.question, columns, data, llm, api_key, user["email"])
                log.info("Think mode analysis complete", user=user["email"])
                steps[-1]["status"] = "done"
            except Exception as _te:
                log.warning("Think mode analysis failed", user=user["email"], error=str(_te))
                steps[-1]["status"] = "failed"

        # ── Build conversation summary & persist ──────────────────────────────
        answer_summary = f"Found {len(data)} result{'s' if len(data) != 1 else ''}."
        if columns and data:
            num_col = next(
                (c for c in columns if isinstance(data[0].get(c), (int, float))), None
            )
            if num_col:
                try:
                    total = sum(float(r.get(num_col, 0) or 0) for r in data)
                    answer_summary += f" {num_col.replace('_', ' ')} = {total:,.2f}"
                except Exception:
                    pass
        if conv_id:
            try:
                stat_col = next(
                    (c for c in columns if isinstance(data[0].get(c), (int, float))), None
                ) if data else None
                _conv.save_message(conv_id, "user", req.question)
                _conv.save_message(
                    conv_id, "assistant", answer_summary,
                    sql_query=sql, row_count=len(data),
                    columns=columns, data=data, stat_col=stat_col,
                )
                convo = _conv.get_conversation(conv_id, user["email"])
                msg_count = convo["message_count"] if convo else 0
                if msg_count == 2:
                    _conv.trigger_title_generation(
                        conv_id, req.question, answer_summary, llm, api_key, user["email"],
                    )
                from conversations import _SUMMARY_THRESHOLD
                if msg_count >= _SUMMARY_THRESHOLD and msg_count % 5 == 0:
                    _conv.trigger_summarisation(conv_id, llm, api_key, user["email"])
            except Exception as _ce:
                log.warning("Failed to save conversation exchange", conv_id=conv_id, error=str(_ce))

        # ── SalesPlay staleness note ──────────────────────────────────────────
        if nl_last_sync_at and data is not None:
            _TIME_KEYWORDS = (
                "today", "yesterday", "this week", "this month", "last 24",
                "last hour", "right now", "current", "latest", "recent",
                "tonight", "this morning", "this afternoon",
            )
            if any(kw in req.question.lower() for kw in _TIME_KEYWORDS):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    _synced = _dt.fromisoformat(nl_last_sync_at.replace("Z", "+00:00"))
                    if _synced.tzinfo is None:
                        _synced = _synced.replace(tzinfo=_tz.utc)
                    _age_min = (_dt.now(_tz.utc) - _synced).total_seconds() / 60
                    if _age_min > 60:
                        _hours = int(_age_min // 60)
                        _mins  = int(_age_min % 60)
                        _age_str = f"{_hours}h {_mins}m" if _hours else f"{_mins}m"
                        _note = (
                            f"Note: your Salesplay data was last synced {_age_str} ago. "
                            f"Transactions added since then are not included in this result."
                        )
                        analysis = f"{analysis}\n\n{_note}" if analysis else _note
                except Exception as _age_err:
                    log.debug("Staleness note skipped", error=str(_age_err))

        response = _base_query_response(
            success=True, type="data",
            steps=steps,
            columns=columns, data=data, row_count=len(data),
            analysis=analysis, think_mode=req.think_mode,
            conversation_id=conv_id, data_as_of=nl_last_sync_at,
        )
        # Only own-DB users get the generated SQL back (used by QueryPage's
        # "Show SQL" debugging view). Integration users query shared internal
        # tables — returning SQL would expose internal names and tenant_id values.
        if s.get("db_configs"):
            response["sql"] = sql
        return response

    except LLMTransientError as e:
        log.warning("NL query failed — all LLM keys exhausted", error=str(e))
        return _base_query_response(
            success=False, type="error", steps=steps, conversation_id=conv_id,
            message="Our AI service is currently busy. Please try again in a moment.",
        )
    except Exception as e:
        log.error("NL query failed", error=str(e))
        return _base_query_response(
            success=False, type="error", steps=steps, conversation_id=conv_id,
            message="Something went wrong while processing your question. Please try rephrasing it or try again shortly.",
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# DEVELOPER API KEY MANAGEMENT  (Pro plan only)
# ══════════════════════════════════════════════════════════════════════════════

def _require_pro(user_email: str):
    """Raise 403 if the user is not on the Pro plan."""
    ok, reason = check_plan_feature(user_email, "partner_api")
    if not ok:
        raise HTTPException(status_code=403,
                            detail="Developer API access requires the Pro plan.")


def _generate_api_key() -> str:
    import secrets
    return f"dm_live_{secrets.token_urlsafe(32)}"


@v1.get("/developer/key")
@_limiter.limit(RL_READ)
def get_developer_key(request: Request, user: dict = Depends(current_user)):
    """Return the user's active API key (masked except the prefix and last 4 chars)."""
    _require_pro(user["email"])
    conn = _get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT api_key, name, created_at, last_used_at
            FROM user_api_keys
            WHERE user_email=%s AND active=1
            ORDER BY id DESC LIMIT 1
            """,
            (user["email"],),
        )
        row = cur.fetchone()
        if not row:
            return {"ok": True, "key": None}
        key = row["api_key"]
        masked = key[:12] + "•" * (len(key) - 16) + key[-4:]
        return {
            "ok": True,
            "key": {
                "masked":       masked,
                "prefix":       key[:12],
                "name":         row["name"],
                "created_at":   str(row["created_at"]),
                "last_used_at": str(row["last_used_at"]) if row["last_used_at"] else None,
            },
        }
    finally:
        cur.close()
        conn.close()


@v1.post("/developer/key")
@_limiter.limit(RL_WRITE)
def generate_developer_key(request: Request, user: dict = Depends(current_user)):
    """
    Generate a new API key for the user.
    Any existing active key is deactivated first.
    The full key is returned ONCE — store it safely.
    """
    _require_pro(user["email"])
    new_key = _generate_api_key()
    conn = _get_internal_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE user_api_keys SET active=0 WHERE user_email=%s AND active=1",
            (user["email"],),
        )
        cur.execute(
            """
            INSERT INTO user_api_keys (user_email, api_key, name, active)
            VALUES (%s, %s, 'Default', 1)
            """,
            (user["email"], new_key),
        )
        conn.commit()
        log.info("API key generated", user=user["email"])
        return JSONResponse(status_code=201, content={"ok": True, "key": new_key})
    finally:
        cur.close()
        conn.close()


@v1.delete("/developer/key")
@_limiter.limit(RL_WRITE)
def revoke_developer_key(request: Request, user: dict = Depends(current_user)):
    """Revoke (deactivate) the user's active API key."""
    _require_pro(user["email"])
    conn = _get_internal_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE user_api_keys SET active=0 WHERE user_email=%s AND active=1",
            (user["email"],),
        )
        conn.commit()
        revoked = cur.rowcount > 0
        return {"ok": True, "revoked": revoked}
    finally:
        cur.close()
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# CONVERSATION MEMORY
# ══════════════════════════════════════════════════════════════════════════════

class CreateConversationRequest(BaseModel):
    id: str  # UUID generated by the frontend


@v1.post("/conversations")
@_limiter.limit(RL_WRITE)
def api_create_conversation(request: Request, body: CreateConversationRequest,
                            user: dict = Depends(current_user)):
    if not body.id or len(body.id) > 64:
        raise HTTPException(status_code=422, detail="Invalid conversation id.")
    try:
        row = _conv.create_conversation(user["email"], body.id)
        return JSONResponse(status_code=201, content={"ok": True, "conversation": row})
    except Exception as e:
        log.error("create_conversation failed", user=user["email"], error=str(e))
        raise _server_error("Could not create conversation.")


@v1.get("/conversations")
@_limiter.limit(RL_READ)
def api_list_conversations(request: Request, user: dict = Depends(current_user)):
    try:
        return {"ok": True, "conversations": _conv.list_conversations(user["email"])}
    except Exception as e:
        log.error("list_conversations failed", user=user["email"], error=str(e))
        raise _server_error("Could not load conversations.")


@v1.get("/conversations/{conv_id}/messages")
@_limiter.limit(RL_READ)
def api_get_conversation_messages(request: Request, conv_id: str,
                                  user: dict = Depends(current_user)):
    try:
        convo = _conv.get_conversation(conv_id, user["email"])
        if not convo:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        msgs = _conv.get_messages(conv_id, user["email"])
        return {"ok": True, "conversation": convo, "messages": msgs}
    except HTTPException:
        raise
    except Exception as e:
        log.error("get_conversation_messages failed", user=user["email"], error=str(e))
        raise _server_error("Could not load messages.")


@v1.delete("/conversations/{conv_id}")
@_limiter.limit(RL_WRITE)
def api_delete_conversation(request: Request, conv_id: str,
                            user: dict = Depends(current_user)):
    try:
        deleted = _conv.delete_conversation(conv_id, user["email"])
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("delete_conversation failed", user=user["email"], error=str(e))
        raise _server_error("Could not delete conversation.")


# ══════════════════════════════════════════════════════════════════════════════
# ANALYTICS TEMPLATES
# ══════════════════════════════════════════════════════════════════════════════

class AnalyticsRunRequest(BaseModel):
    template_id: str
    llm: str = "openai"
    params: Optional[Dict[str, Any]] = {}
    provider: Optional[str] = None


@v1.post("/analytics/run")
@_limiter.limit(RL_COMPUTE)
def run_analytics(request: Request, req: AnalyticsRunRequest, user: dict = Depends(current_user)):
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    _row_limit = get_plan_history_limit(user["email"])["row_limit"]
    log.info("Run analytics", user=user["email"], template=req.template_id, provider=req.provider)

    # Route provider templates straight to the integration analytics handler.
    if req.provider:
        from integrations import get_integration
        integration = get_integration(user["email"], req.provider)
        if not integration:
            log.error("Run analytics: integration not connected",
                      user=user["email"], provider=req.provider)
            raise HTTPException(status_code=404, detail=f"Integration '{req.provider}' not connected")
        table_prefix = integration["table_prefix"]
        conn = _get_internal_conn()
        try:
            loader = _PROVIDER_TEMPLATE_LOADERS.get(req.provider)
            runner_map = {
                "salesplay": lambda: __import__("providers.salesplay.analytics", fromlist=["run_salesplay_analytics"]).run_salesplay_analytics,
                "loyverse":  lambda: __import__("providers.loyverse.analytics",  fromlist=["run_loyverse_analytics"]).run_loyverse_analytics,
            }
            if not loader or req.provider not in runner_map:
                raise HTTPException(status_code=404, detail=f"No analytics available for {req.provider}")
            runner = runner_map[req.provider]()
            result = runner(conn, table_prefix, req.template_id)
            result["source"] = "integration"
            result["provider"] = req.provider
            log.info("Run analytics: integration template served",
                     user=user["email"], provider=req.provider, template=req.template_id,
                     row_count=result.get("row_count"))
            _apply_row_limit(result, _row_limit)
            _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                       result.get("row_count", 0))
            return result
        except HTTPException:
            raise
        except Exception as e:
            log.error("Run analytics: integration template failed",
                      user=user["email"], provider=req.provider,
                      template=req.template_id, error=str(e))
            raise _server_error("Analytics execution failed.")
        finally:
            conn.close()

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
                _apply_row_limit(result, _row_limit)
                _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                           result.get("row_count", 0))
                conn.close()
                return result
            except Exception as sql_err:
                log.warning("Cached SQL failed, trying fallbacks",
                            template=req.template_id, error=str(sql_err))

        # 2. Python analytics
        r = _try_python(req.template_id, conn)
        if r:
            r["source"] = "python"
            _apply_row_limit(r, _row_limit)
            _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                       r.get("row_count", 0))
            conn.close()
            return r

        # 3. Hardcoded fallback
        r = _hardcoded(req.template_id, conn)
        if r:
            r["source"] = "fallback"
            log.warning("Analytics using hardcoded fallback SQL", template=req.template_id)
            _apply_row_limit(r, _row_limit)
            _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                       r.get("row_count", 0))
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
        raise _server_error("Analytics execution failed.")


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
        "discount_analysis":   ("SELECT CASE WHEN totalDiscount IS NULL OR totalDiscount=0 THEN 'No Discount' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.05 THEN '<5%' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.10 THEN '5-10%' WHEN totalDiscount/NULLIF(invoiceTotal,0)<0.20 THEN '10-20%' ELSE '>20%' END as discount_band, COUNT(*) as invoices, ROUND(AVG(invoiceTotal),2) as avg_order_value, ROUND(SUM(invoiceTotal),2) as total_revenue FROM invoices GROUP BY discount_band ORDER BY total_revenue DESC", "Discount Impact Analysis"),
        "margin_by_category":  ("SELECT p.category, COUNT(DISTINCT p.itemCode) as skus, ROUND(AVG(ii.itemPrice),2) as avg_price, ROUND(AVG(ii.itemCost),2) as avg_cost, ROUND(AVG((ii.itemPrice-ii.itemCost)/NULLIF(ii.itemPrice,0)*100),1) as avg_margin_pct, ROUND(SUM(ii.qty*(ii.itemPrice-ii.itemCost)),2) as total_gross_profit FROM invoice_items ii JOIN products p ON ii.itemCode=p.itemCode GROUP BY p.category ORDER BY total_gross_profit DESC", "Margin by Category"),
        "slow_products":       ("SELECT p.name, p.category, COALESCE(SUM(ii.qty),0) as total_units, COALESCE(ROUND(SUM(ii.qty*ii.itemPrice),2),0) as revenue, DATEDIFF(CURDATE(),MAX(i.invoiceDate)) as days_since_last_sale FROM products p LEFT JOIN invoice_items ii ON p.itemCode=ii.itemCode LEFT JOIN invoices i ON ii.invoiceNumber=i.invoiceNumber GROUP BY p.itemCode,p.name,p.category HAVING total_units<10 OR days_since_last_sale>30 OR days_since_last_sale IS NULL ORDER BY total_units ASC LIMIT 25", "Slow-Moving Products"),
        "customer_retention":  ("SELECT DATE_FORMAT(first_month,'%Y-%m') as cohort_month, COUNT(*) as new_customers, SUM(CASE WHEN total_orders>1 THEN 1 ELSE 0 END) as retained, ROUND(SUM(CASE WHEN total_orders>1 THEN 1 ELSE 0 END)/COUNT(*)*100,1) as retention_pct FROM (SELECT customerId, MIN(invoiceDate) as first_month, COUNT(DISTINCT invoiceNumber) as total_orders FROM invoices WHERE customerId IS NOT NULL AND invoiceDate IS NOT NULL GROUP BY customerId) t GROUP BY DATE_FORMAT(first_month,'%Y-%m') ORDER BY cohort_month DESC LIMIT 12", "Customer Retention by Cohort"),
        "loyalty_tiers":       ("SELECT CASE WHEN c.loyaltyPoints IS NULL OR c.loyaltyPoints=0 THEN 'No Points' WHEN c.loyaltyPoints<100 THEN 'Bronze' WHEN c.loyaltyPoints<500 THEN 'Silver' WHEN c.loyaltyPoints<1000 THEN 'Gold' ELSE 'Platinum' END as tier, COUNT(DISTINCT c.customerId) as customers, ROUND(AVG(t.lifetime_value),2) as avg_ltv, ROUND(AVG(t.avg_order),2) as avg_order_value FROM customers c LEFT JOIN (SELECT customerId, SUM(invoiceTotal) as lifetime_value, AVG(invoiceTotal) as avg_order FROM invoices GROUP BY customerId) t ON c.customerId=t.customerId GROUP BY tier ORDER BY avg_ltv DESC", "Loyalty Tier Performance"),
        "credit_outstanding":  ("SELECT DATE_FORMAT(invoiceDate,'%Y-%m') as month, COUNT(*) as credit_invoices, ROUND(SUM(creditAmt),2) as total_credit, ROUND(SUM(CASE WHEN isPaid=1 THEN creditAmt ELSE 0 END),2) as collected, ROUND(SUM(CASE WHEN isPaid=0 OR isPaid IS NULL THEN creditAmt ELSE 0 END),2) as outstanding FROM invoices WHERE creditAmt>0 GROUP BY month ORDER BY month DESC LIMIT 12", "Credit & Outstanding"),
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


@v1.post("/forecast")
@_limiter.limit(RL_COMPUTE)
def forecast(request: Request, req: ForecastRequest, user: dict = Depends(current_user)):
    ok, reason = check_plan_feature(user["email"], "forecast")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    log.info("Forecast (manual)", user=user["email"],
             table=req.table, date_col=req.date_column, value_col=req.value_column)
    try:
        conn = get_connection(_resolve_db(user))
        schemas = get_table_schemas(conn, None)
        _validate_table_column(schemas, req.table, req.date_column)
        _validate_table_column(schemas, req.table, req.value_column)
        history = get_plan_history_limit(user["email"])
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT DATE(`{req.date_column}`) as ds, SUM(`{req.value_column}`) as y "
            f"FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL "
            f"AND `{req.date_column}` >= %s GROUP BY ds ORDER BY ds",
            (history["cutoff_date"],)
        )
        rows = cursor.fetchall()
        conn.close()
        log.info("Forecast data loaded", rows=len(rows), months=history["months"])
        result = run_forecast(rows, req.periods)
        _charge_op(user["email"], "forecast", len(rows))
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error("Forecast failed", error=str(e))
        raise _server_error("Forecast failed. Ensure your table has enough historical data.")


@v1.get("/forecast/auto")
@_limiter.limit(RL_COMPUTE)
def auto_forecast(request: Request, periods: int = 90, user: dict = Depends(current_user)):
    ok, reason = check_plan_feature(user["email"], "forecast")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    s = user.get("settings", {})

    # Provider-only user: run forecast on their synced receipts view
    if not s.get("db_configs"):
        conns = get_user_connections(user["email"])
        if not conns:
            raise HTTPException(status_code=422, detail="No data source connected.")
        primary      = conns[0]
        prefix       = primary.get("table_prefix", "")
        provider_id  = primary.get("provider_id", "salesplay")
        if not prefix:
            raise HTTPException(status_code=422, detail="Integration tables not ready.")
        receipts_tbl = _PROVIDER_RECEIPTS_TABLE.get(
            provider_id, "{prefix}receipts"
        ).format(prefix=prefix)
        log.info("Provider auto forecast", user=user["email"], table=receipts_tbl)
        history = get_plan_history_limit(user["email"])
        try:
            iconn = _get_internal_conn()
            cursor = iconn.cursor()
            cursor.execute(
                f"SELECT DATE(created_at) AS ds, SUM(total_money) AS y "
                f"FROM `{receipts_tbl}` "
                f"WHERE created_at IS NOT NULL AND total_money > 0 AND created_at >= %s "
                f"GROUP BY DATE(created_at) ORDER BY DATE(created_at)",
                (history["cutoff_date"],)
            )
            rows = cursor.fetchall()
            iconn.close()
            if len(rows) < 5:
                raise HTTPException(status_code=422,
                    detail=f"Not enough data for forecasting — got {len(rows)} sale days, need at least 5. Your POS account may be too new or have no sales in the history window.")
            result = run_forecast(rows, periods)
            result.update(used_table=receipts_tbl, used_date_col="created_at",
                          used_value_col="total_money", from_cache=False)
            _charge_op(user["email"], "forecast", len(rows))
            return result
        except HTTPException:
            raise
        except Exception as e:
            log.error("Provider forecast failed", error=str(e))
            raise _server_error("Forecast failed. Ensure your integration has enough historical data.")

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
        history = get_plan_history_limit(user["email"])
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT DATE(`{d_col}`) as ds, SUM(`{v_col}`) as y "
            f"FROM `{table}` WHERE `{d_col}` IS NOT NULL AND `{d_col}` >= %s GROUP BY ds ORDER BY ds",
            (history["cutoff_date"],)
        )
        rows = cursor.fetchall()
        conn.close()
        if len(rows) < 10:
            raise HTTPException(status_code=422,
                detail=f"Not enough data for forecasting — got {len(rows)} daily data points, need at least 10. Ensure your table has sufficient historical data.")
        result = run_forecast(rows, periods)
        result["used_table"]     = table
        result["used_date_col"]  = d_col
        result["used_value_col"] = v_col
        result["from_cache"]     = bool(cache and auto.get("forecast_table"))
        _charge_op(user["email"], "forecast", len(rows))
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error("Auto forecast failed", error=str(e))
        raise _server_error("Forecast failed. Ensure your data source has enough historical data.")


# ══════════════════════════════════════════════════════════════════════════════
# ANOMALIES
# ══════════════════════════════════════════════════════════════════════════════

class AnomalyRequest(BaseModel):
    table: str
    value_column: str
    date_column: Optional[str] = None


@v1.post("/anomalies")
@_limiter.limit(RL_COMPUTE)
def anomalies(request: Request, req: AnomalyRequest, user: dict = Depends(current_user)):
    ok, reason = check_plan_feature(user["email"], "anomaly_detection")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    log.info("Anomaly detection (manual)", user=user["email"], table=req.table)
    try:
        conn = get_connection(_resolve_db(user))
        schemas = get_table_schemas(conn, None)
        _validate_table_column(schemas, req.table, req.value_column)
        if req.date_column:
            _validate_table_column(schemas, req.table, req.date_column)
        history = get_plan_history_limit(user["email"])
        cursor = conn.cursor()
        if req.date_column:
            cursor.execute(
                f"SELECT `{req.date_column}`, `{req.value_column}` "
                f"FROM `{req.table}` WHERE `{req.date_column}` IS NOT NULL "
                f"AND `{req.date_column}` >= %s ORDER BY `{req.date_column}`",
                (history["cutoff_date"],)
            )
        else:
            cursor.execute(
                f"SELECT `{req.value_column}` FROM `{req.table}` LIMIT %s",
                (history["row_limit"],)
            )
        rows = cursor.fetchall()
        conn.close()
        result = run_anomaly_detection(rows, has_date=bool(req.date_column))
        _charge_op(user["email"], "anomaly_detection", len(rows))
        return result
    except Exception as e:
        log.error("Anomaly detection failed", error=str(e))
        raise _server_error("Anomaly detection failed. Ensure your table has enough data.")


@v1.get("/anomalies/auto")
@_limiter.limit(RL_COMPUTE)
def auto_anomalies(request: Request, user: dict = Depends(current_user)):
    ok, reason = check_plan_feature(user["email"], "anomaly_detection")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    s = user.get("settings", {})

    # Provider-only user: run anomaly detection on their synced receipts view
    if not s.get("db_configs"):
        conns = get_user_connections(user["email"])
        if not conns:
            raise HTTPException(status_code=422, detail="No data source connected.")
        primary      = conns[0]
        prefix       = primary.get("table_prefix", "")
        provider_id  = primary.get("provider_id", "salesplay")
        if not prefix:
            raise HTTPException(status_code=422, detail="Integration tables not ready.")
        receipts_tbl = _PROVIDER_RECEIPTS_TABLE.get(
            provider_id, "{prefix}receipts"
        ).format(prefix=prefix)
        log.info("Provider auto anomaly", user=user["email"], table=receipts_tbl)
        history = get_plan_history_limit(user["email"])
        try:
            iconn = _get_internal_conn()
            cursor = iconn.cursor()
            cursor.execute(
                f"SELECT DATE(created_at), SUM(total_money) "
                f"FROM `{receipts_tbl}` "
                f"WHERE created_at IS NOT NULL AND total_money > 0 AND created_at >= %s "
                f"GROUP BY DATE(created_at) ORDER BY DATE(created_at)",
                (history["cutoff_date"],)
            )
            rows = cursor.fetchall()
            iconn.close()
            if len(rows) < 5:
                raise HTTPException(status_code=422,
                    detail=f"Not enough data for anomaly detection — got {len(rows)} daily data points, need at least 5. Sync more data or widen your plan's history window.")
            result = run_anomaly_detection(rows, has_date=True)
            result.update(used_table=receipts_tbl, used_date_col="created_at",
                          used_value_col="total_money", from_cache=False)
            _charge_op(user["email"], "anomaly_detection", len(rows))
            return result
        except HTTPException:
            raise
        except Exception as e:
            log.error("Provider anomaly detection failed", error=str(e))
            raise _server_error("Anomaly detection failed. Ensure your integration has enough historical data.")

    # Own-DB user
    db_config = _resolve_db(user)
    cache = get_cache(user["email"], db_config)
    auto = cache.get("auto_columns", {}) if cache else {}
    table  = auto.get("anomaly_table")    or "invoices"
    d_col  = auto.get("anomaly_date_col") or "invoiceDate"
    v_col  = auto.get("anomaly_value_col")or "invoiceTotal"
    log.info("Auto anomaly detection", user=user["email"],
             table=table, date_col=d_col, value_col=v_col)
    history = get_plan_history_limit(user["email"])
    try:
        conn = get_connection(db_config)
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT `{d_col}`, SUM(`{v_col}`) FROM `{table}` "
            f"WHERE `{d_col}` IS NOT NULL AND `{d_col}` >= %s GROUP BY `{d_col}` ORDER BY `{d_col}`",
            (history["cutoff_date"],)
        )
        rows = cursor.fetchall()
        conn.close()
        if len(rows) < 5:
            raise HTTPException(status_code=422,
                detail=f"Not enough data for anomaly detection — got {len(rows)} daily data points, need at least 5. Ensure your table has sufficient historical data.")
        result = run_anomaly_detection(rows, has_date=True)
        result["used_table"]     = table
        result["used_date_col"]  = d_col
        result["used_value_col"] = v_col
        result["from_cache"]     = bool(cache and auto.get("anomaly_table"))
        _charge_op(user["email"], "anomaly_detection", len(rows))
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error("Auto anomaly detection failed", error=str(e))
        raise _server_error("Anomaly detection failed. Ensure your data source has enough data.")


# ══════════════════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════════════════

class ReportRequest(BaseModel):
    title: str
    sections: List[str]
    llm: str = "openai"
    format: str = "full"


@v1.post("/report")
@_limiter.limit(RL_COMPUTE)
def generate_report(request: Request, req: ReportRequest, user: dict = Depends(current_user)):
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    llm = _effective_llm(user, req.llm)
    log.info("Generate report", user=user["email"], llm=llm,
             title=req.title, sections=req.sections)
    api_key = _resolve_api_key(user, llm)
    s = user.get("settings", {})
    try:
        # ── Provider-only path (SalesPlay / Loyverse / etc.) ─────────────────
        if not s.get("db_configs"):
            conns = get_user_connections(user["email"])
            if not conns:
                raise HTTPException(status_code=422, detail="No data source connected.")
            primary    = conns[0]
            provider_id  = primary.get("provider_id", "")
            table_prefix = primary.get("table_prefix", "")
            if not table_prefix:
                raise HTTPException(status_code=422, detail="Integration tables not ready. Run a sync first.")
            log.info("Report: provider-only path", user=user["email"],
                     provider=provider_id, prefix=table_prefix)

            conn = _get_internal_conn()
            section_data: Dict = {}
            loader_fn  = _PROVIDER_TEMPLATE_LOADERS.get(provider_id)
            runner_fn  = _PROVIDER_RUNNER_FACTORIES.get(provider_id)
            section_map = _PROVIDER_SECTION_MAP.get(provider_id, {})

            if loader_fn and runner_fn:
                try:
                    templates = loader_fn()
                    runner    = runner_fn()
                    for sid in req.sections:
                        tid = section_map.get(sid) or (sid if sid in templates else None)
                        if not tid or tid not in templates:
                            log.debug("Report: no provider template for section", sid=sid, provider=provider_id)
                            continue
                        try:
                            r = runner(conn, table_prefix, tid)
                            r["source"] = "provider"
                            section_data[sid] = r
                        except Exception as e:
                            log.warning("Provider report section failed", sid=sid, tid=tid, error=str(e))
                except Exception as e:
                    log.warning("Provider report runner error", provider=provider_id, error=str(e))

            kpis = _get_provider_kpis(conn, table_prefix, provider_id)
            conn.close()
            narrative = generate_report_summary(
                title=req.title, kpis=kpis, section_data=section_data,
                llm=llm, format=req.format, api_key=api_key,
                user_email=user["email"]
            )
            log.info("Report generated (provider)", user=user["email"],
                     provider=provider_id, sections=len(section_data))
            return {"title": req.title, "kpis": kpis, "sections": section_data, "narrative": narrative}

        # ── Own-DB path ──────────────────────────────────────────────────────
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
            llm=llm, format=req.format, api_key=api_key,
            user_email=user["email"]
        )
        log.info("Report generated", user=user["email"], sections=len(section_data))
        return {"title": req.title, "kpis": kpis, "sections": section_data, "narrative": narrative}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Report generation failed", error=str(e))
        raise _server_error("Report generation failed.")


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


@v1.get("/providers")
@_limiter.limit(RL_READ)
def get_providers(request: Request):
    """List all available external API providers with their manifests."""
    try:
        providers = list_providers()
        log.info("Listed providers", count=len(providers))
        return {"providers": providers}
    except Exception as e:
        log.error("Failed to list providers", error=str(e))
        raise _server_error("Failed to load providers.")


@v1.get("/providers/connected")
@_limiter.limit(RL_READ)
def get_connected_providers(request: Request, user: dict = Depends(current_user)):
    """List all providers this user has connected. Returns empty list if DB not configured."""
    try:
        connections = get_user_connections(user["email"])
        log.debug("Got connected providers", user=user["email"], count=len(connections))
        return {"connections": connections}
    except Exception as e:
        log.warning("Could not load connections (integration DB may not be configured)",
                    user=user["email"], error=str(e))
        return {"connections": []}  # safe fallback — never break the UI


@v1.post("/providers/validate")
@_limiter.limit(RL_WRITE)
def validate_provider_credentials(request: Request, req: ProviderConnectRequest, user: dict = Depends(current_user)):
    """Test provider credentials before saving."""
    from providers import get_provider as gp
    log.info("Validating provider credentials", user=user["email"], provider=req.provider_id)
    try:
        provider = gp(req.provider_id)
        result = provider.validate_credentials(req.credentials)
        return {"ok": result.ok, "error": result.error, "details": result.details}
    except Exception as e:
        log.error("Provider validation error", provider=req.provider_id, error=str(e))
        return {"ok": False, "error": "Failed to validate credentials. Check your API key and try again."}


@v1.post("/providers/connect")
@_limiter.limit(RL_WRITE)
def connect_provider_route(request: Request, req: ProviderConnectRequest,
                           background_tasks: BackgroundTasks,
                           user: dict = Depends(current_user)):
    """
    Connect a provider: validate, create tables, trigger full sync.
    Sync runs in background.
    """
    ok, reason = check_plan_feature(user["email"], "external_api")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_db_limit(user["email"], 0)
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    log.info("Connecting provider", user=user["email"], provider=req.provider_id)
    try:
        connection_id = connect_provider(user["email"], req.provider_id, req.credentials)
        background_tasks.add_task(trigger_sync, user["email"], connection_id, full=True)
        return JSONResponse(status_code=201, content={"ok": True, "connection_id": connection_id})
    except Exception as e:
        log.error("Provider connect failed", provider=req.provider_id, error=str(e))
        raise _server_error("Failed to connect provider.")


@v1.get("/providers/stats")
@_limiter.limit(RL_READ)
def provider_stats(request: Request, user: dict = Depends(current_user)):
    """Return aggregate stats across all user integrations (total rows across all providers)."""
    count = get_user_total_rows(user["email"])
    if count is None:
        raise _server_error("Failed to load provider stats.")
    return {"total_rows": count}


@v1.delete("/providers/{connection_id}")
@_limiter.limit(RL_WRITE)
def disconnect_provider_route(request: Request, connection_id: str, user: dict = Depends(current_user)):
    """Disconnect a provider and drop all its synced tables."""
    log.info("Disconnecting provider", user=user["email"], connection_id=connection_id)
    try:
        from integrations import disconnect_integration
        disconnect_integration(user["email"], connection_id, drop_tables=True)
        return {"ok": True}
    except Exception as e:
        log.error("Disconnect provider failed", user=user["email"], connection_id=connection_id, error=str(e))
        raise _server_error("Failed to disconnect provider.")


@v1.delete("/auth/account")
@_limiter.limit(RL_WRITE)
def delete_account(request: Request, user: dict = Depends(current_user)):
    """Permanently delete the current user and all their data."""
    email = user["email"]
    log.info("Account deletion requested", user=email)
    try:
        delete_user_data(email)
        from cache import list_caches
        import os
        for p in list_caches(email):
            try: os.remove(p)
            except Exception: pass
        delete_user(email)
        log.info("Account deleted", user=email)
        return {"ok": True}
    except Exception as e:
        log.error("Account deletion failed", user=email, error=str(e))
        raise _server_error("Account deletion failed.")


@v1.post("/providers/{connection_id}/sync")
@_limiter.limit(RL_WRITE)
def manual_sync(request: Request, connection_id: str, background_tasks: BackgroundTasks,
                user: dict = Depends(current_user)):
    """Manually trigger a delta sync for a connection."""
    ok, reason = check_db_limit(user["email"], 0)
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    log.info("Manual sync triggered", user=user["email"], connection_id=connection_id)
    background_tasks.add_task(trigger_sync, user["email"], connection_id, full=False)
    return {"ok": True, "message": "Sync started in background"}


@v1.get("/providers/{connection_id}/status")
@_limiter.limit(RL_READ)
def provider_status(request: Request, connection_id: str, user: dict = Depends(current_user)):
    """Get live sync status and stats for a connection."""
    try:
        status = get_connection_status(user["email"], connection_id)
        return status
    except Exception as e:
        log.error("Get sync status failed", user=user["email"], connection_id=connection_id, error=str(e))
        raise _server_error("Failed to get sync status.")


@v1.get("/providers/{connection_id}/history")
@_limiter.limit(RL_READ)
def provider_history(request: Request, connection_id: str, user: dict = Depends(current_user)):
    """Get sync history for a connection."""
    try:
        history = get_sync_history(user["email"], connection_id)
        return {"history": history}
    except Exception as e:
        log.error("Get sync history failed", user=user["email"], connection_id=connection_id, error=str(e))
        raise _server_error("Failed to get sync history.")



# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION-SPECIFIC ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@v1.get("/integrations/{provider_id}/analytics/templates")
@_limiter.limit(RL_READ)
def list_integration_templates(
    request: Request,
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
                    "type": t.get("type", "table"),
                    "icon": t.get("icon", "📊"),
                }
                for tid, t in templates.items()
            ]
        }
    except Exception as e:
        log.error("List integration templates failed",
                  provider=provider_id,
                  error=str(e))
        raise _server_error("Failed to load integration templates.")


class IntegrationAnalyticsRequest(BaseModel):
    template_id: str


@v1.post("/integrations/{provider_id}/analytics/run")
@_limiter.limit(RL_COMPUTE)
def run_integration_analytics(
    request: Request,
    provider_id: str,
    req: IntegrationAnalyticsRequest,
    user: dict = Depends(current_user)
):
    """Run analytics on integration data."""
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)

    from integrations import get_integration, _get_internal_conn
    _row_limit = get_plan_history_limit(user["email"])["row_limit"]

    try:
        integration = get_integration(user["email"], provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found or not connected.")

        table_prefix = integration["table_prefix"]

        # Check result cache BEFORE borrowing a DB connection.
        # Cached or not, the user is always billed — _charge_op fires either way.
        if provider_id == "salesplay":
            from providers.salesplay.analytics import run_salesplay_analytics, _cache_get
            cached_result = _cache_get(table_prefix, req.template_id)
            if cached_result is not None:
                result = {**cached_result, "source": "integration", "provider": provider_id, "cached": True}
                _apply_row_limit(result, _row_limit)
                _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                           result.get("row_count", 0))
                return result

        conn = _get_internal_conn()
        try:
            if provider_id == "salesplay":
                result = run_salesplay_analytics(conn, table_prefix, req.template_id)
            elif provider_id == "loyverse":
                from providers.loyverse.analytics import run_loyverse_analytics
                result = run_loyverse_analytics(conn, table_prefix, req.template_id)
            else:
                raise HTTPException(status_code=404, detail=f"No analytics available for {provider_id}")

            result["source"]   = "integration"
            result["provider"] = provider_id
            result["cached"]   = False
            _apply_row_limit(result, _row_limit)
            _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                       result.get("row_count", 0))
            return result

        finally:
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        log.error("Integration analytics failed", provider=provider_id,
                  template=req.template_id, user=user["email"], error=str(e))
        raise _server_error("Integration analytics failed.")


class IntegrationForecastRequest(BaseModel):
    table: str
    date_column: str
    value_column: str
    periods: int = 90


@v1.post("/integrations/{provider_id}/forecast")
@_limiter.limit(RL_COMPUTE)
def forecast_integration(
    request: Request,
    provider_id: str,
    req: IntegrationForecastRequest,
    user: dict = Depends(current_user)
):
    """Run forecast on integration data."""
    ok, reason = check_plan_feature(user["email"], "forecast")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        raise HTTPException(status_code=402, detail=reason)

    from integrations import get_integration, _get_internal_conn
    history = get_plan_history_limit(user["email"])

    try:
        integration = get_integration(user["email"], provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found or not connected.")

        conn = _get_internal_conn()
        table_prefix = integration["table_prefix"]

        try:
            # SEC-03: validate table/column names against actual schema before use in SQL
            full_table = f"{table_prefix}_{req.table}"
            schemas = get_table_schemas(conn, [full_table])
            _validate_table_column(schemas, full_table, req.date_column)
            _validate_table_column(schemas, full_table, req.value_column)

            cursor = conn.cursor()
            cursor.execute(
                f"SELECT DATE(`{req.date_column}`) as date, SUM(`{req.value_column}`) as value "
                f"FROM `{full_table}` "
                f"WHERE `{req.date_column}` IS NOT NULL AND `{req.date_column}` >= %s "
                f"GROUP BY DATE(`{req.date_column}`) ORDER BY date",
                (history["cutoff_date"],)
            )
            rows = cursor.fetchall()

            if len(rows) < 10:
                raise HTTPException(status_code=400, detail="Need at least 10 data points for forecasting")

            result = run_forecast(rows, req.periods)
            result["source"]   = "integration"
            result["provider"] = provider_id
            _charge_op(user["email"], "forecast", len(rows))
            return result

        finally:
            conn.close()

    except HTTPException:
        raise
    except Exception as e:
        log.error("Integration forecast failed", provider=provider_id,
                  user=user["email"], error=str(e))
        raise _server_error("Integration forecast failed.")


# ── BILLING ───────────────────────────────────────────────────────────────────

@v1.get("/billing/plans")
@_limiter.limit(RL_READ)
def billing_plans(request: Request):
    try:
        return {"plans": get_subscription_plans()}
    except Exception as e:
        log.error("Get billing plans failed", error=str(e))
        return {"plans": []}


@v1.get("/billing/subscription")
@_limiter.limit(RL_READ)
def billing_subscription(request: Request, user: dict = Depends(current_user)):
    try:
        return get_user_subscription(user["email"])
    except Exception as e:
        log.error("Get subscription failed", user=user["email"], error=str(e))
        return {"status": "no_subscription"}


class SubscribeRequest(BaseModel):
    plan_id: int

@v1.post("/billing/subscribe")
@_limiter.limit(RL_WRITE)
def billing_subscribe(request: Request, req: SubscribeRequest, user: dict = Depends(current_user)):
    plan = get_plan_by_id(req.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    subscribe_to_plan(user["email"], req.plan_id)
    return {"ok": True}


class AddonRequest(BaseModel):
    addon_type: str
    quantity: int = 1

@v1.post("/billing/addon")
@_limiter.limit(RL_WRITE)
def billing_addon(request: Request, req: AddonRequest, user: dict = Depends(current_user)):
    purchase_addon(user["email"], req.addon_type, req.quantity)
    return {"ok": True}


@v1.get("/billing/usage")
@_limiter.limit(RL_READ)
def billing_usage(request: Request, user: dict = Depends(current_user)):
    return {
        "history":     get_token_usage_history(user["email"]),
        "llm_history": get_llm_usage_history(user["email"]),
        "pricing":     get_addon_pricing(),
    }


@v1.get("/billing/config")
@_limiter.limit(RL_READ)
def billing_config_get(request: Request, _user: dict = Depends(current_user)):
    return {"ai_credit_rate": get_ai_credit_rate()}


class BillingConfigRequest(BaseModel):
    ai_credit_rate: float

@v1.post("/billing/config")
@_limiter.limit(RL_WRITE)
def billing_config_set(request: Request, req: BillingConfigRequest, _user: dict = Depends(current_user)):
    if req.ai_credit_rate <= 0:
        raise HTTPException(status_code=400, detail="ai_credit_rate must be positive")
    set_ai_credit_rate(req.ai_credit_rate)
    return {"ok": True, "ai_credit_rate": req.ai_credit_rate}


# Register all user-facing v1 routes on the app
app.include_router(v1)
