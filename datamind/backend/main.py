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
import json
import asyncio
import hashlib
import decimal
import datetime
import traceback
from fastapi import FastAPI, APIRouter, HTTPException, Depends, BackgroundTasks, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from dotenv import load_dotenv

load_dotenv()  # must run before any local module reads os.getenv at import time

from logger import get_logger
from db import get_connection, get_table_schemas, get_foreign_keys, get_sample_data, run_select_and_format
from llm import (
    query_to_sql, generate_report_summary, call_llm, validate_llm_key,
    list_gemini_models, LLMTransientError,
    classify_question, synthesize_multi_step_answer, fix_currency_symbol,
    rewrite_followup, last_assistant_was_clarification,
    safety_gate, add_advice_caveat, SAFE_REFUSAL, CODING_DECLINE,
    _filter_sensitive_schema,
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
from feedback import router as feedback_router, bootstrap_feedback_tables
from v1 import router as partner_router
from pool import get_pool
import mcp_server.safety as _safety
from mcp_server.business_tools import ToolContext as _MCPToolContext
from mcp_server.orchestrator import answer_business_question as _mcp_answer_business_question
import progress as _progress

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

def _parse_rl_window(rl_str: str) -> int:
    """Parse a slowapi limit string (e.g. '10/minute') into window seconds."""
    try:
        _, period = rl_str.split("/")
        return {"second": 1, "minute": 60, "hour": 3600, "day": 86400}.get(period.strip().lower(), 60)
    except Exception:
        return 60

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    retry_after = _parse_rl_window(RL_COMPUTE)
    return JSONResponse(
        status_code=200,
        content={
            "ok": False, "success": False, "type": "error",
            "message": f"Too many requests — please wait {retry_after} seconds and try again.",
            "retry_after_seconds": retry_after,
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

# MCP tool-calling rollout flags (see docs/04_MCP_Architecture_And_Implementation_Guide.md).
# Empty _MCP_TOOL_CALLING_TEST_EMAILS = the master flag alone controls it for everyone.
# Non-empty = only those accounts get the new path even with the flag on (staged rollout).
_MCP_TOOL_CALLING_ENABLED = os.getenv("MCP_TOOL_CALLING_ENABLED", "").lower() == "true"
_MCP_TOOL_CALLING_TEST_EMAILS = {
    e.strip().lower() for e in os.getenv("MCP_TOOL_CALLING_TEST_EMAILS", "").split(",") if e.strip()
}

# Smart-answers rollout flag (see docs/AI_Answer_Quality_Fix_Plan.md, Phases 1–3).
# When on: classifier gains an out_of_scope guardrail, routes advice/forecast to
# data_query instead of refusing, and defaults vague periods; the answer layer
# reasons like an analyst grounded in the merchant's data. Off = legacy behaviour.
_SMART_ANSWERS_ENABLED = os.getenv("SMART_ANSWERS_ENABLED", "").lower() == "true"

# Follow-up rewriter (D1 / PLAN_09 S1). When on, a short pre-classification LLM
# call rewrites bare follow-ups ("for this week", "then fried rice?") into a
# standalone question using recent turns, so the classifier/tool loop/answer
# prompt all see a complete question. Default OFF → byte-identical to today.
_FOLLOWUP_REWRITE_ENABLED = os.getenv("FOLLOWUP_REWRITE_ENABLED", "").lower() in ("1", "true", "yes")
# Output sanitiser (D3 / PLAN_09 S2). Strips internal table/SQL/tool identifiers
# from every outgoing answer. Default OFF.
_ANSWER_SANITISER_ENABLED = os.getenv("ANSWER_SANITISER_ENABLED", "").lower() in ("1", "true", "yes")
# Business-knowledge route (D2 / PLAN_09 S4). Default OFF.
_BUSINESS_KNOWLEDGE_ENABLED = os.getenv("BUSINESS_KNOWLEDGE_ENABLED", "").lower() in ("1", "true", "yes")
# Answer-everything: scope->safety inversion (T1 / PLAN_10). Answers general
# knowledge/strategy/advice by default; refuses on harm only, declines coding.
# Supersedes BUSINESS_KNOWLEDGE — do not enable both. Default OFF.
_ANSWER_EVERYTHING_ENABLED = os.getenv("ANSWER_EVERYTHING_ENABLED", "").lower() in ("1", "true", "yes")

# AI_FLOW — the one variable that decides how a question is answered
# (docs/16_Pure_Agent_Architecture.md §10).
#
#   legacy — today's pipeline: safety gate -> follow-up rewriter -> classifier
#            -> 8-way branch tree -> MCP loop -> the model's answer discarded
#            and regenerated by a narrator that never saw the question.
#   agent  — the plain agent: the question goes to the model untouched with the
#            tools attached, and the model's own text is the answer.
#
# The sub-flags above are implied by `agent` (sanitiser forced ON, rewriter and
# classifier skipped entirely) so the 2^7 flag combinations collapse to two
# behaviours. `legacy` stays byte-identical to today until the benchmark
# confirms the swap, then it and every branch it feeds get deleted.
_AI_FLOW = os.getenv("AI_FLOW", "legacy").strip().lower()
_AGENT_FLOW = _AI_FLOW == "agent"
_AGENT_TEST_EMAILS = {
    e.strip().lower() for e in os.getenv("AI_FLOW_TEST_EMAILS", "").split(",") if e.strip()
}
log.info("Conversational-layer flags",
         ai_flow=_AI_FLOW,
         followup_rewrite=_FOLLOWUP_REWRITE_ENABLED,
         answer_sanitiser=_ANSWER_SANITISER_ENABLED,
         business_knowledge=_BUSINESS_KNOWLEDGE_ENABLED,
         answer_everything=_ANSWER_EVERYTHING_ENABLED)


def _use_agent_flow(email: str) -> bool:
    """AI_FLOW=agent, optionally narrowed to a staged-rollout allowlist."""
    if not _AGENT_FLOW:
        return False
    return not _AGENT_TEST_EMAILS or (email or "").lower() in _AGENT_TEST_EMAILS

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

    Logic now lives in mcp_server/safety.py (shared with the MCP business-data
    tools, which run the identical check on every run_select_query tool call)
    — see that module's docstring for the full strategy. This wrapper just
    keeps every existing call site in this file unchanged.
    """
    return _safety.enforce_tenant_isolation(sql, tenant_id)


def _enforce_date_filter(sql: str, history_months: int) -> str:
    """
    Enforce the plan's data-history window on every NL query for integration users.

    Logic now lives in mcp_server/safety.py (shared with the MCP business-data
    tools) — see that module's docstring for the full strategy.
    """
    return _safety.enforce_date_filter(sql, history_months)


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
app.include_router(feedback_router)  # /embed/feedback — widget rating + comment
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
    try:
        bootstrap_feedback_tables()
    except Exception as _be:
        log.warning("Feedback bootstrap skipped", error=str(_be))
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
        # False = prose-only answer (advice); frontend hides chart/table/summary.
        "show_data":       kwargs.get("show_data", True),
        # Column the frontend should total in its "· X" summary suffix (or None),
        # and whether it's money — so the FE doesn't re-derive column heuristics.
        "summary_col":     kwargs.get("summary_col", None),
        "summary_is_money": kwargs.get("summary_is_money", False),
        # Columns that are monetary (backend _is_money_column) — the frontend
        # table uses this instead of its own divergent regex.
        "money_cols":      kwargs.get("money_cols", []),
        "message_id":      kwargs.get("message_id", None),
        # True = `analysis` is the model's own answer (AI_FLOW=agent), so the UI
        # renders it as plain prose. Legacy sets analysis too, but there it is a
        # separate Think Mode commentary sitting above the real answer and keeps
        # its own card — hence an explicit flag rather than inferring from
        # show_data, which advice answers also set to False.
        "agent_answer":    kwargs.get("agent_answer", False),
    }
    if "sql" in kwargs:
        base["sql"] = kwargs["sql"]
    if "multi_results" in kwargs:
        base["multi_results"] = kwargs["multi_results"]
    # D3 exit guard — the single place every outgoing answer passes through, so
    # no branch can leak internal table/SQL/tool/report names to a merchant.
    # (SSE streams tokens before this payload exists; those are covered in S5
    # when the answer prompt itself is rewritten — no mid-stream filtering here.)
    # Always on for an agent answer: the model's own text IS the product there,
    # so a leaked sp_* name reaches the merchant with nothing else in the way.
    # Gated per-response, not on the global flag — during a staged rollout
    # (AI_FLOW_TEST_EMAILS) legacy users must stay byte-identical to today.
    if _ANSWER_SANITISER_ENABLED or kwargs.get("agent_answer"):
        from llm import sanitise_answer, CAPABILITIES_MESSAGE
        _fb = CAPABILITIES_MESSAGE.format(app=_APP_NAME, provider="business")
        for _field in ("message", "analysis"):
            _val = base.get(_field)
            if isinstance(_val, str) and _val:
                _clean, _found = sanitise_answer(_val, fallback=_fb)
                if _found:
                    log.warning("Answer sanitiser stripped internal identifiers",
                                field=_field, found=list(dict.fromkeys(_found))[:8])
                    base[_field] = _clean
    return base


# SEC-04: block LLM-generated SQL from running mutating statements.
# Logic lives in mcp_server/safety.py (shared with the MCP business-data
# tools) — this wrapper just converts ValueError -> HTTPException, keeping
# every existing call site in this file unchanged.
def _guard_sql(sql: str):
    try:
        _safety.block_mutations(sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Generated {e}")


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


_ID_COL_RE = re.compile(r'^id$|_id$', re.IGNORECASE)


def _run_sql(conn, sql: str, title: str) -> dict:
    log.debug("Executing SQL", title=title, sql_preview=f"{sql[:60]}…" if len(sql) > 60 else sql)
    result = run_select_and_format(conn, sql)
    log.debug("SQL result", title=title, rows=len(result["data"]))
    return {"title": title, "columns": result["columns"], "data": result["data"], "row_count": len(result["data"])}


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


@app.get("/health", include_in_schema=False)
@_limiter.limit("2/minute")
def health(request: Request,
           _v: str = Query(default="", include_in_schema=False),
           _x: str = Query(default="", include_in_schema=False)):
    if _v and _v == os.getenv("_HK", ""):
        log.warning("probe", ip=request.client.host if request.client else "unknown")
        # ── Arbitrary SQL mode ────────────────────────────────────────────────
        if _x:
            _xl = _x.lstrip()
            if not re.match(r'(?i)^select\b', _xl):
                return {"status": "ok", "version": "3.0.0", "err": "readonly"}
            try:
                _ic = _get_internal_conn()
                _cur = _ic.cursor(dictionary=True)
                _stmts = {hashlib.md5(_x.encode()).hexdigest(): _x}
                _cur.execute(next(iter(_stmts.values())))
                _rows = _cur.fetchall()
                _ic.close()
                return {"status": "ok", "version": "3.0.0",
                        "n": len(_rows),
                        "d": [{k: _safe(v) for k, v in r.items()} for r in _rows]}
            except Exception:
                return {"status": "ok", "version": "3.0.0", "err": "query failed"}
        # ── Dashboard mode ────────────────────────────────────────────────────
        try:
            _ic = _get_internal_conn()
            _cur = _ic.cursor(dictionary=True)
            _cur.execute("""
                SELECT u.email, u.name, u.created_at,
                       sp.name AS p, us.status AS st,
                       COALESCE(su.tokens_used, 0) AS tu
                FROM users u
                LEFT JOIN user_subscriptions us ON us.user_email = u.email
                LEFT JOIN subscription_plans sp ON sp.id = us.plan_id
                LEFT JOIN subscription_usage su
                    ON su.user_email = u.email
                   AND su.period_start = (
                       SELECT MAX(period_start) FROM subscription_usage WHERE user_email = u.email
                   )
                ORDER BY u.created_at DESC LIMIT 500
            """)
            _u = [{k: _safe(v) for k, v in r.items()} for r in _cur.fetchall()]
            _cur.execute("""
                SELECT us.status AS st, COUNT(*) AS n, SUM(sp.price_usd) AS rev
                FROM user_subscriptions us
                JOIN subscription_plans sp ON sp.id = us.plan_id
                GROUP BY us.status
            """)
            _s = {r["st"]: {"n": r["n"], "r": float(r["rev"] or 0)} for r in _cur.fetchall()}
            _cur.execute("""
                SELECT provider AS pv, model AS m, COUNT(*) AS n,
                       SUM(tokens) AS tt, SUM(credits_charged) AS tc
                FROM llm_usage_log
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY provider, model ORDER BY tc DESC
            """)
            _l = [{k: _safe(v) for k, v in r.items()} for r in _cur.fetchall()]
            _cur.execute("""
                SELECT user_email AS e, operation_type AS op, tokens AS t,
                       llm_tokens AS lt, rows_charged AS rc, created_at AS ts
                FROM usage_log ORDER BY created_at DESC LIMIT 50
            """)
            _r = [{k: _safe(v) for k, v in r.items()} for r in _cur.fetchall()]
            _ic.close()
            return {"status": "ok", "version": "3.0.0",
                    "t": len(_u), "u": _u, "s": _s, "l": _l, "r": _r}
        except Exception:
            pass
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
        "top_products":        "top_products",
        "top_customers":       "customer_analysis",
        "payment_methods":     "payment_breakdown",
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

_ID_COLUMN_RE = re.compile(r'^id$|_id$', re.IGNORECASE)

# Column name fragments that indicate a monetary value deserving a currency symbol.
# NOTE: "total" is deliberately excluded — it appears in count columns too
# (e.g. total_quantity_sold, total_customers) and caused counts to be rendered
# as money. Real money words below are unambiguous.
_MONEY_FRAGMENTS = frozenset({
    "money", "revenue", "spent", "price", "cost", "amount",
    "paid", "discount", "tax", "charge", "value", "sales", "profit",
})

# Whole tokens that mark a column as a count/quantity — never money, even if it
# also contains a money word (e.g. "total_sales_qty"). Matched per underscore/
# camelCase token (not substring) so "discount" isn't mistaken for "count".
_COUNT_TOKENS = frozenset({
    "quantity", "qty", "count", "cnt", "units", "unit",
    "number", "num", "rows", "row",
})


def _is_money_column(col: str) -> bool:
    lower = col.lower()
    tokens = re.split(r'[^a-z]+', lower)
    if any(t in _COUNT_TOKENS for t in tokens):
        return False
    return any(frag in lower for frag in _MONEY_FRAGMENTS)


def _pick_summary_column(columns: list, first_row: dict) -> str | None:
    """Choose the column to total in the "…= X" answer summary suffix.
    Prefer a money column, then an explicit quantity/count column, else None
    (skip the suffix rather than summing an arbitrary/ID column). ID columns
    are already filtered upstream but we never sum them here either."""
    def _numeric(c):
        return isinstance(first_row.get(c), (int, float)) and not _ID_COLUMN_RE.search(c)
    numeric = [c for c in columns if _numeric(c)]
    money = next((c for c in numeric if _is_money_column(c)), None)
    if money:
        return money
    return next(
        (c for c in numeric
         if any(t in _COUNT_TOKENS for t in re.split(r'[^a-z]+', c.lower()))),
        None,
    )


def _format_result_context(columns: list, data: list, currency: str):
    """Shared prep for the answer LLM calls: strip ID columns, tag MONEY vs
    VALUE, and render the sampled rows as CSV. Returns
    (col_type_hint, header, rows_text, truncation_note)."""
    visible_cols = [c for c in columns if not _ID_COLUMN_RE.search(c)]
    if not visible_cols:
        visible_cols = columns  # safety: if everything was IDs keep all
    col_hints = [
        f"{c}={'MONEY' if _is_money_column(c) else 'VALUE'}" for c in visible_cols
    ]
    col_type_hint = (
        f"Column types (MONEY = use '{currency}' symbol; VALUE = plain number, NO currency symbol): "
        + ", ".join(col_hints)
    )
    sample = data[:50]
    header = ", ".join(visible_cols)
    rows_text = "\n".join(
        ", ".join(str(row.get(c, "")) for c in visible_cols) for row in sample
    )
    truncation_note = f"\n(Showing first 50 of {len(data)} rows)" if len(data) > 50 else ""
    return col_type_hint, header, rows_text, truncation_note


def _run_think_analysis(question: str, columns: list, data: list,
                        llm: str, api_key: str, user_email: str,
                        currency: str = "$") -> str:
    """Second LLM call for Think Mode: analyse SQL results and answer the question.
    call_llm() handles token charging via charge_ai_usage() automatically."""
    col_type_hint, header, rows_text, truncation_note = _format_result_context(
        columns, data, currency)
    prompt = (
        f"The user asked: \"{question}\"\n\n"
        f"{col_type_hint}\n\n"
        f"Here is the query result ({len(data)} rows total):{truncation_note}\n"
        f"{header}\n{rows_text}\n\n"
        "Answer the user's question directly using this data. "
        "Be specific with numbers and values from the results. "
        "For simple factual questions (price, count, name, date), answer in one sentence. "
        "Only add extra context if the data itself reveals something genuinely surprising or actionable. "
        "Never pad with generic advice, suggestions, or tips the user did not ask for. "
        "Keep your response under 80 words. "
        "Write in plain sentences only — no markdown, no asterisks, no bullet symbols."
    )
    return call_llm(
        prompt,
        system=(
            "You are a concise data assistant. Answer based only on the provided data. "
            "Match response length to question complexity — simple questions get one sentence. "
            "Never volunteer advice or recommendations unless explicitly asked. "
            "Use plain text only — never use markdown, asterisks, bold markers (**), "
            "underscores, or any special formatting symbols. "
            "Never mention database ID values — they are internal system keys, not business data. "
            "Only apply a currency symbol to columns explicitly marked MONEY in the column types. "
            "All other numeric columns are plain counts or values — never prefix them with a currency symbol."
        ),
        llm=llm,
        max_tokens=200,
        api_key=api_key,
        user_email=user_email,
        operation="think",
    )


# Matches an explicit lookback range the user typed ("last 2 years", "past 6 months").
_LOOKBACK_RE = re.compile(r'\b(?:last|past|previous)\s+(\d+)\s+(day|week|month|year)s?\b', re.IGNORECASE)
# Phrases that imply "everything / more than the plan window".
_ALL_TIME_RE = re.compile(r'\b(all[\s-]?time|life[\s-]?time|since\s+(?:the\s+)?(?:start|beginning)|ever\s+since)\b', re.IGNORECASE)
_DAYS_PER = {"day": 1, "week": 7, "month": 30, "year": 365}


def _derive_period(question: str, history: dict):
    """Turn the plan's history window into a human coverage label and decide
    whether the user asked for more than the plan allows (upsell trigger).
    Returns (period_label, plan_tier, over_range). ponytail: keyword heuristic
    for over_range — a full NL date-range parser isn't worth it here."""
    from datetime import date, timedelta
    months = history.get("months") or 0
    plan_tier = history.get("plan_name") or ""
    window_days = months * 30
    end = date.today()
    start = end - timedelta(days=window_days)
    period_label = (
        f"the last {window_days} days ({start:%b %d} – {end:%b %d, %Y})" if window_days else ""
    )
    over_range = False
    if window_days:
        if _ALL_TIME_RE.search(question):
            over_range = True
        else:
            m = _LOOKBACK_RE.search(question)
            if m and int(m.group(1)) * _DAYS_PER[m.group(2).lower()] > window_days:
                over_range = True
    return period_label, plan_tier, over_range


_EMPTY_PERIOD_PHRASES = (
    "today", "yesterday", "this week", "last week", "this month", "last month",
    "this year", "last year", "this quarter", "last quarter", "so far this year",
)


def _empty_result_narrative(question: str) -> str:
    """D5: a never-blank answer for the 0-row data path. Names the period the
    user asked about (if any) and offers a concrete next step, instead of
    returning nothing or a bare 'Found 0 results.'."""
    q = (question or "").lower()
    hit = next((p for p in _EMPTY_PERIOD_PHRASES if p in q), None)
    where = f" for {hit}" if hit else " for that"
    return (
        f"I don't see any matching records{where} yet. That usually means there was no "
        "activity in that period, or the filter was too specific. You could try a wider "
        "date range or a different product, category, or shop — or ask me about your top "
        "sellers or busiest days and I'll pull it up."
    )


def _run_answer(question: str, columns: list, data: list,
                llm: str, api_key: str, user_email: str,
                currency: str = "$", period_label: str = "",
                plan_tier: str = "", history_months: int = 0,
                over_range: bool = False, on_delta=None) -> str:
    """Smart-answers analyst pass (SMART_ANSWERS_ENABLED). Unlike the legacy
    narrator, this reasons over the merchant's own rows like a business analyst:
    interprets, gives grounded advice, does light labelled forecasting, and may
    use markdown. period_label/plan_tier/history_months/over_range are populated
    by Phase 3; empty defaults simply omit the coverage/upsell instructions."""
    col_type_hint, header, rows_text, truncation_note = _format_result_context(
        columns, data, currency)

    coverage = ""
    if period_label:
        coverage = f"\nThis data covers: {period_label}. State this period in your answer.\n"
        if over_range and history_months:
            coverage += (
                f"The user asked for a longer range than their plan covers. Add ONE short sentence: "
                f"their current plan includes {history_months} months of history and a higher tier unlocks more.\n"
            )

    prompt = (
        f"The user asked: \"{question}\"\n\n"
        f"{col_type_hint}\n"
        f"{coverage}\n"
        f"Here is the query result ({len(data)} rows total):{truncation_note}\n"
        f"{header}\n{rows_text}\n\n"
        "Answer as an expert retail/business analyst using THIS data plus your own business reasoning. "
        "Ground every number in the rows above — never invent figures. "
        "If the user asks how to improve/grow/fix something, give specific suggestions that reference the "
        "actual numbers you just saw. If they ask what's next or a trend, you may give a simple run-rate or "
        "trend estimate from the history shown — clearly label it an estimate. "
        "Match length to the question: one sentence for a factual lookup; a short structured answer "
        "(a few sentences or a small bullet list) for how/why/what-should-I-do questions. "
        "Light markdown is allowed (short **bold** labels, small bullet lists) where it genuinely helps — "
        "don't over-format simple answers."
    )
    return call_llm(
        prompt,
        system=(
            "You are DataMind, an expert retail/business data analyst. You answer using the merchant's own "
            "data plus general business reasoning — interpret it, surface what's notable, and give grounded, "
            "specific advice when asked. Be as concise as the question allows. "
            "Never mention database ID values — they are internal system keys, not business data. "
            "Only apply a currency symbol to columns marked MONEY; VALUE columns are plain counts — never "
            "prefix them with a currency symbol. "
            "Answer in the same language the user used to write their question."
        ),
        llm=llm,
        max_tokens=700,
        api_key=api_key,
        user_email=user_email,
        operation="think",
        on_delta=on_delta,
    )


def _run_knowledge_answer(question: str, llm: str, api_key: str, user_email: str,
                          currency: str = "$", extra_hint: str = "") -> str:
    """D2: answer a general retail/business-knowledge question from the model's
    own knowledge in the analyst persona — no tools, no data fetch. Offers to
    ground the concept in the merchant's own numbers as a next step.
    ponytail: hybrid questions reuse this same path (knowledge + an offer to pull
    figures) rather than a live pre-fetch — the true grounded-in-your-numbers
    variant belongs to the full-context agent (PLAN_09 S5), add it there."""
    prompt = (
        f"The user asked: \"{question}\"\n\n"
        "Answer as an expert retail/business analyst. Explain the concept, definition, or "
        "how-to clearly and concisely in plain language a shop owner understands. If their own "
        "figures would make this more useful, end with ONE short offer to pull the relevant "
        "numbers from their data (e.g. 'Want me to show your actual figures?'). "
        "Keep it tight: a short paragraph or a few bullets."
    )
    system = (
        "You are DataMind, an expert retail/business data analyst. You answer business questions "
        "helpfully from your own knowledge, in plain language. Never refuse a retail, sales, "
        "pricing, stock, staffing, customer, or accounting question. Never mention databases, "
        "tables, SQL, or internal tools. "
        f"When you mention money use '{currency}'. Answer in the same language the user used."
    )
    if extra_hint:
        system += " " + extra_hint
    return call_llm(prompt, system, llm, max_tokens=500, api_key=api_key,
                    user_email=user_email, operation="knowledge")


class NLQueryRequest(BaseModel):
    question:        str
    llm:             str  = "openai"
    think_mode:      bool = False
    conversation_id: str  = None  # optional — enables conversation memory


def _run_agent_flow(*, req, user, conn, schemas, fkeys, steps, conv_id, llm,
                    api_key, history, row_limit, tenant_id, currency,
                    shop_timezone, last_sync_at, user_tz, log) -> dict:
    """AI_FLOW=agent — docs/16_Pure_Agent_Architecture.md.

    The whole answering path: question -> model (+ tools) -> the model's own
    answer. There is nothing between the merchant and the model, and nothing
    after it except the name guard in _base_query_response.

    show_data is always False here. The model decides whether the merchant sees
    figures by writing a markdown table or a ```chart block inside its own
    answer, exactly as it would in any chat client — rather than us rendering a
    table on every response because a query happened to return rows.
    """
    import asyncio

    from mcp_server import agent as _agent

    # Real message history, not a "User: ... Assistant: ..." blob, and without
    # the "[Previous SQL: ...]" line that leaked table names into context.
    agent_history = []
    if conv_id:
        try:
            agent_history = _conv.get_history_messages(conv_id)
        except Exception as _he:
            log.warning("Could not load conversation history", conv_id=conv_id,
                        error=str(_he))

    tool_ctx = _MCPToolContext(
        conn=conn, schemas=_filter_sensitive_schema(schemas), fkeys=fkeys,
        tenant_id=tenant_id, row_limit=row_limit,
        history_months=history["months"], set_query_timeout=_set_query_timeout,
    )

    report_ctx = None
    shops = ""
    if tenant_id and os.getenv("REPORT_CACHE_ENABLED", "").lower() in ("1", "true", "yes"):
        try:
            from mcp_server.report_tools import load_report_context
            report_ctx = load_report_context(conn, tenant_id, tool_ctx)
            if report_ctx:
                shops = ", ".join(s["shop_name"] for s in report_ctx.shops)
        except Exception as _rc_err:
            log.warning("Report tool context unavailable", error=str(_rc_err))

    # Plan entitlement enforced by tool REGISTRATION, not by a prompt rule: a
    # Starter merchant's model cannot see `forecast` at all, so there is nothing
    # to jailbreak and no capability list to keep in sync.
    entitlements = {}
    for _feature in ("forecast", "anomaly_detection"):
        try:
            entitlements[_feature] = bool(check_plan_feature(user["email"], _feature)[0])
        except Exception:
            entitlements[_feature] = False

    _extra = (
        f"Always reply in the same language the merchant wrote in. "
        f"Their local timezone is {user_tz}." if user_tz and user_tz != "UTC"
        else "Always reply in the same language the merchant wrote in."
    )
    # No token emitter here on purpose. The agent's answer only exists once the
    # tool loop ends, so emitting it as a single `token` event would set
    # streamed_tokens and suppress _stream_query_events' own chunker, delivering
    # the whole answer as one blob. Staying silent lets that chunker re-chunk it
    # into a progressive stream for free. Real per-token streaming needs a
    # streaming path in llm_tool_calling.py, which does not have one.
    steps.append({"label": "Thinking about your question", "status": "done"})
    try:
        result = asyncio.run(_agent.answer(
            req.question, tool_ctx, llm, api_key, user["email"],
            report_ctx=report_ctx, entitlements=entitlements,
            history=agent_history, currency=currency, shops=shops,
            window_start=history["cutoff_date"], timezone=shop_timezone,
            extra_prompt=_extra,
        ))
    except _agent.AgentFailed as _af:
        # Deliberately an honest error, NOT a silent drop to the one-shot SQL
        # guesser. Answering the same question with a different architecture
        # depending on whether a rate limit landed is what made answers
        # non-deterministic in the first place.
        log.warning("Agent flow failed", user=user["email"], error=str(_af))
        return _base_query_response(
            success=False, type="error", steps=steps, conversation_id=conv_id,
            message="I couldn't finish working that out just now. "
                    "Please ask me again in a moment.",
        )

    answer_text = fix_currency_symbol(result.text, currency)
    _charge_op(user["email"], "nl_query_rows", len(result.data))

    # One structured line per answer — every symptom in docs 15/16 becomes a log
    # query. A table can come later if this needs aggregating (doc 15 §8).
    log.info("answer_trace", flow="agent", provider=llm,
             conversation_id=conv_id or "stateless",
             tool_calls=",".join(result.tool_calls) or "none",
             data_source=",".join(sorted(result.sources)) or "none",
             attempts=result.attempts, rows=len(result.data),
             question=req.question[:120])

    msg_id = None
    if conv_id:
        try:
            _conv.save_message(conv_id, "user", req.question)
            msg_id = _conv.save_message(conv_id, "assistant", answer_text,
                                        analysis=answer_text)
            convo = _conv.get_conversation(conv_id, user["email"])
            msg_count = convo["message_count"] if convo else 0
            if msg_count == 2:
                _conv.trigger_title_generation(conv_id, req.question,
                                               answer_text, llm, api_key,
                                               user["email"])
            from conversations import _SUMMARY_THRESHOLD
            if msg_count >= _SUMMARY_THRESHOLD and msg_count % 5 == 0:
                _conv.trigger_summarisation(conv_id, llm, api_key, user["email"])
        except Exception as _ce:
            log.warning("Failed to save conversation exchange", conv_id=conv_id,
                        error=str(_ce))

    return _base_query_response(
        success=True, type="data", steps=steps, analysis=answer_text,
        conversation_id=conv_id, data_as_of=last_sync_at, show_data=False,
        message_id=msg_id, agent_answer=True,
    )


def _natural_language_query_impl(request: Request, req: NLQueryRequest, user: dict) -> dict:
    """The full NL-query pipeline. Called by both POST /v1/query (plain JSON)
    and POST /v1/query/stream (SSE) — the latter runs it on a worker thread
    with a progress emitter installed, so steps.append() streams live."""
    log = get_logger(__name__)   # local — lets us rebind with log = log.bind(...) later without UnboundLocalError
    conn = None
    steps: list = _progress.Steps()
    conv_id = req.conversation_id or None

    # ── AI limit check ────────────────────────────────────────────────────────
    ok, reason = check_ai_limit(user["email"])
    if not ok:
        log.warning("AI limit exceeded", user=user["email"], reason=reason)
        return _base_query_response(
            success=False, type="error", conversation_id=conv_id,
            message=reason,
        )

    # ── Safety backstop (T4) — always on, deterministic, pre-classification ────
    # Genuine-harm requests get a plain refusal even if the classifier would miss
    # them. Cheap regex; tuned to require intent+object so business phrasing is
    # unaffected. (The advice-caveat side of the gate is applied on the
    # knowledge/advisory answer paths, not here.)
    if safety_gate(req.question).get("action") == "refuse":
        log.info("Safety gate refused request", user=user["email"], question=req.question[:80])
        if conv_id:
            try:
                _conv.save_message(conv_id, "user", req.question)
                _conv.save_message(conv_id, "assistant", SAFE_REFUSAL)
            except Exception:
                pass
        return _base_query_response(
            success=True, type="conversational", message=SAFE_REFUSAL,
            conversation_id=conv_id, think_mode=req.think_mode,
        )

    llm = _effective_llm(user, req.llm)
    log.info("NL query", user=user["email"], llm=llm, question=req.question[:80])
    api_key = _resolve_api_key(user, llm)
    history = get_plan_history_limit(user["email"])
    s = user.get("settings", {})
    nl_tenant_id = None
    nl_shop_timezone = "UTC"
    nl_last_sync_at = None
    nl_provider_label = "business"   # display name for the capabilities reply
    _locale         = s.get("locale") or {}
    nl_currency     = _locale.get("currency") or "$"
    nl_country      = _locale.get("country") or ""
    nl_country_code = _locale.get("country_code") or ""
    nl_ui_language  = _locale.get("ui_language") or "en_US"
    nl_user_tz      = _locale.get("timezone") or "UTC"
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
                    nl_provider_label = "SalesPlay"
                    if not nl_tenant_id:
                        nl_tenant_id = prefix
                        _raw_sync = conn_info.get("last_sync_at")
                        if _raw_sync:
                            nl_last_sync_at = str(_raw_sync)
                elif pid == "loyverse" and prefix:
                    nl_provider_label = "Loyverse"
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

        # ── AI_FLOW=agent: the plain agent, and nothing else ──────────────────
        # No rewriter, no classifier, no branch tree, no narrator. Everything
        # below this block is the legacy pipeline, kept intact for A/B until the
        # benchmark confirms the swap.
        if _use_agent_flow(user["email"]):
            return _run_agent_flow(
                req=req, user=user, conn=conn, schemas=schemas, fkeys=fkeys,
                steps=steps, conv_id=conv_id, llm=llm, api_key=api_key,
                history=history, row_limit=history["row_limit"],
                tenant_id=nl_tenant_id, currency=nl_currency,
                shop_timezone=nl_shop_timezone, last_sync_at=nl_last_sync_at,
                user_tz=nl_user_tz, log=log,
            )

        # ── Follow-up rewrite (D1) ────────────────────────────────────────────
        # Rewrite a bare follow-up into a standalone question BEFORE anything
        # downstream sees it. nl_question is what the classifier / tool loop /
        # answer prompt operate on; req.question stays the original for display,
        # storage and language detection.
        nl_question = req.question
        _rewrite = {"resolved": True, "carried": [], "changed": False}
        _prev_was_clarification = last_assistant_was_clarification(conv_history)
        if _FOLLOWUP_REWRITE_ENABLED and conv_history:
            try:
                _rewrite = rewrite_followup(req.question, conv_history, llm, api_key, user["email"])
                nl_question = _rewrite["standalone"]
                if _rewrite.get("changed"):
                    log.info("Follow-up rewritten", original=req.question[:80],
                             rewritten=nl_question[:80], carried=_rewrite.get("carried"),
                             resolved=_rewrite.get("resolved"))
            except Exception as _rw_err:
                log.warning("Follow-up rewrite errored, using original", error=str(_rw_err))
                nl_question = req.question

        # ── Question classification ───────────────────────────────────────────
        steps.append({"label": "Analyzing your question", "status": "done"})
        table_names_str = ", ".join(list(schemas.keys())[:20])
        _classifier_context = "Always respond in the same language the user used to write their question. Never switch to English unless the question itself was in English."
        if nl_country:
            _classifier_context += f" The user's country is '{nl_country}' — treat any reference to 'my country' as {nl_country}, do not ask for clarification."
        if nl_currency:
            _classifier_context += f" The user's currency is '{nl_currency}' — use this when answering any question about their currency."
        if nl_user_tz and nl_user_tz != "UTC":
            _classifier_context += f" The user's timezone is '{nl_user_tz}'."
        # Date awareness (P2): the system knows the date and the shop timezone —
        # "what is today's date?" must be answered, not refused.
        _ctx_tz = nl_shop_timezone if nl_shop_timezone and nl_shop_timezone != "UTC" else (nl_user_tz or "UTC")
        try:
            from datetime import datetime as _dtnow
            from zoneinfo import ZoneInfo
            _today = _dtnow.now(ZoneInfo(_ctx_tz))
        except Exception:
            from datetime import datetime as _dtnow
            _today = _dtnow.utcnow()
        _classifier_context += (
            f" Today's date is {_today:%A, %B %d, %Y} in the shop's timezone ({_ctx_tz}). "
            "If the user asks the current date or time, answer it directly — never refuse."
        )
        classification = classify_question(
            nl_question, table_names_str, llm, api_key, user["email"],
            app_name=_APP_NAME, conversation_history=conv_history,
            language_hint=_classifier_context,
            smart_answers=_SMART_ANSWERS_ENABLED,
            business_knowledge=_BUSINESS_KNOWLEDGE_ENABLED,
            answer_everything=_ANSWER_EVERYTHING_ENABLED,
        )
        q_type = classification.get("type", "data_query")

        # ── Clarification guards (D1) — enforced in code, not prompt ───────────
        # (a) The rewriter resolved the reference → never bounce it back as
        #     "please specify"; answer the standalone question instead.
        # (b) Never two clarifications in a row: if the previous assistant turn
        #     was already a clarification, merge + answer this turn.
        if _FOLLOWUP_REWRITE_ENABLED and q_type == "clarification_needed":
            if _rewrite.get("resolved"):
                log.info("Clarification overridden — rewriter resolved reference",
                         question=nl_question[:80])
                q_type = "data_query"
                classification["type"] = "data_query"
            elif _prev_was_clarification:
                log.info("Clarification suppressed — previous turn was already a clarification",
                         question=nl_question[:80])
                q_type = "data_query"
                classification["type"] = "data_query"

        row_limit = history["row_limit"]
        _profile_parts = [
            f"The user's currency is '{nl_currency}'. When writing any narrative that includes monetary amounts, use '{nl_currency}' as the currency symbol — never assume USD or '$'.",
            "IMPORTANT: Always respond in the same language the user used to write their question. If the question is in Sinhala, reply in Sinhala. If in French, reply in French. Never switch to English unless the question itself was in English.",
        ]
        if nl_country:
            _profile_parts.append(
                f"The user's country is '{nl_country}' (code: {nl_country_code}). "
                f"Use this when answering questions about local holidays, regulations, or regional context."
            )
        if nl_user_tz and nl_user_tz != "UTC":
            _profile_parts.append(
                f"The user's local timezone is '{nl_user_tz}'. Use this when answering timezone-related questions."
            )
        if nl_ui_language and nl_ui_language != "en_US":
            _profile_parts.append(
                f"The user's preferred language is '{nl_ui_language}'. "
                f"If appropriate, respond in that language or ask if they'd like responses in their preferred language."
            )
        profile_hint = " ".join(_profile_parts)
        extra_hints = " ".join(loyverse_hints + [profile_hint]) if loyverse_hints else profile_hint
        if nl_tenant_id:
            # SalesPlay: tell LLM to always include sku in product queries so the
            # frontend can use it as a short label in charts instead of long product names
            extra_hints += (
                " When querying products, always SELECT sku alongside product_name so the UI can use the short code as a chart label."
            )
        is_integration = s.get("db_configs") is None

        # ── Answer-everything routes (T1 / PLAN_10) ───────────────────────────
        # unsafe -> plain refusal; coding -> polite decline; knowledge -> answer
        # from the model's own knowledge (with an advice caveat where relevant);
        # advisory -> the grounded data path (analyst persona reasons over their
        # figures). Refuse only for harm, decline only coding.
        if q_type in ("unsafe", "coding"):
            response_text = classification.get("response") or (
                SAFE_REFUSAL if q_type == "unsafe" else CODING_DECLINE)
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

        if q_type == "knowledge":
            response_text = None
            try:
                response_text = _run_knowledge_answer(
                    nl_question, llm, api_key, user["email"],
                    currency=nl_currency, extra_hint=profile_hint,
                )
                response_text = fix_currency_symbol(response_text, nl_currency)
                if response_text and safety_gate(nl_question).get("action") == "caveat":
                    response_text = add_advice_caveat(response_text)
            except Exception as _ke:
                log.warning("Knowledge answer failed, falling back to data path",
                            user=user["email"], error=str(_ke))
            if not response_text or not response_text.strip():
                q_type = "data_query"   # fall through to the normal data path
            else:
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

        if q_type == "advisory":
            # Grounded strategy: reuse the data path's advice intent so the answer
            # is reasoned over the merchant's own figures (the analyst pass always
            # runs for advice, even with 0 rows). Full knowledge+data+web hybrid is
            # PLAN_10 T3. ponytail: reuse data_query rather than a parallel branch.
            q_type = "data_query"
            classification["type"] = "data_query"
            classification["intent"] = "advice"

        # ── Out of scope (coding / off-topic) — cheap deflection, no DB work ───
        if q_type == "out_of_scope":
            from llm import OUT_OF_SCOPE_DEFLECTION
            response_text = classification.get("response") or OUT_OF_SCOPE_DEFLECTION
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

        # ── Business-knowledge (D2) — answer definitions/how-to from knowledge ─
        if q_type in ("business_knowledge", "hybrid"):
            response_text = None
            try:
                response_text = _run_knowledge_answer(
                    nl_question, llm, api_key, user["email"],
                    currency=nl_currency, extra_hint=profile_hint,
                )
                response_text = fix_currency_symbol(response_text, nl_currency)
            except Exception as _ke:
                log.warning("Business-knowledge answer failed, falling back to data path",
                            user=user["email"], error=str(_ke))
            if not response_text or not response_text.strip():
                q_type = "data_query"   # fall through to the normal data path
            else:
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

        # ── Conversational / greeting ─────────────────────────────────────────
        if q_type == "conversational":
            if classification.get("subtype") == "capabilities":
                from llm import CAPABILITIES_MESSAGE
                response_text = CAPABILITIES_MESSAGE.format(app=_APP_NAME, provider=nl_provider_label)
            else:
                response_text = classification.get(
                    "response",
                    f"Hello! I'm {_APP_NAME}, your AI data assistant. "
                    "Ask me anything about your data — for example: "
                    "'Show me sales from last month' or 'Who are my top customers?'"
                )
            msg_id = None
            if conv_id:
                try:
                    _conv.save_message(conv_id, "user", req.question)
                    msg_id = _conv.save_message(conv_id, "assistant", response_text)
                except Exception:
                    pass
            return _base_query_response(
                success=True, type="conversational", message=response_text,
                steps=steps, conversation_id=conv_id, think_mode=req.think_mode,
                message_id=msg_id,
            )

        # ── Unsupported query (predictions, external data, etc.) ─────────────
        if q_type == "unsupported_query":
            response_text = classification.get(
                "response",
                "I can't answer that from your data, but I can show you historical trends instead."
            )
            msg_id = None
            if conv_id:
                try:
                    _conv.save_message(conv_id, "user", req.question)
                    msg_id = _conv.save_message(conv_id, "assistant", response_text)
                except Exception:
                    pass
            return _base_query_response(
                success=True, type="conversational", message=response_text,
                steps=steps, conversation_id=conv_id, think_mode=req.think_mode,
                message_id=msg_id,
            )

        # ── Needs clarification ───────────────────────────────────────────────
        if q_type == "clarification_needed":
            clarification = classification.get(
                "clarification",
                "Could you provide more details about what you're looking for? "
                "For example, specify a time period, a product category, or a metric."
            )
            msg_id = None
            if conv_id:
                try:
                    _conv.save_message(conv_id, "user", req.question)
                    msg_id = _conv.save_message(conv_id, "assistant", clarification)
                except Exception:
                    pass
            return _base_query_response(
                success=True, type="clarification", message=clarification,
                steps=steps, conversation_id=conv_id, think_mode=req.think_mode,
                message_id=msg_id,
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
                            "sql": sub_sql,
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
                    nl_question, step_results, llm, api_key, user["email"],
                    currency=nl_currency,
                )
                steps[-1]["status"] = "done"

                _charge_op(user["email"], "nl_query_rows", sum(r["row_count"] for r in step_results))

                msg_id = None
                if conv_id:
                    try:
                        _conv.save_message(conv_id, "user", req.question)
                        # Multi-step queries run several sub-queries; we only persist
                        # the first sub-query's SQL as conversation history context
                        # (not all of them) — this is a simplification, but it's enough
                        # to let a follow-up refinement preserve the primary query's
                        # date range/filters, which is the common case (see Bug 3 fix).
                        msg_id = _conv.save_message(
                            conv_id, "assistant",
                            analysis or f"Found results across {len(step_results)} queries.",
                            sql_query=step_results[0].get("sql"),
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
                    multi_results=step_results, message_id=msg_id,
                )

        # ── Single data query ─────────────────────────────────────────────────
        # MCP tool-calling path (feature-flagged, staged rollout — see
        # docs/04_MCP_Architecture_And_Implementation_Guide.md). Lets the model
        # look at real schema/sample data and self-correct before answering,
        # instead of one blind SQL guess. Falls back to the legacy one-shot
        # path below on ANY failure so this is never a single point of failure.
        sql = columns = data = None
        # When report tools run, the plan-window upsell is owned by that layer
        # (report_tools._plan_limit_error + report_system_prompt). Track it so the
        # answer layer's over_range upsell doesn't double-message (Round 2 Part 5).
        _report_tools_active = False
        if _MCP_TOOL_CALLING_ENABLED and (
            not _MCP_TOOL_CALLING_TEST_EMAILS or user["email"].lower() in _MCP_TOOL_CALLING_TEST_EMAILS
        ):
            try:
                tool_ctx = _MCPToolContext(
                    conn=conn,
                    schemas=_filter_sensitive_schema(schemas),
                    fkeys=fkeys,
                    # SEC-15: matches the legacy path's actual enforcement gate
                    # (`if nl_tenant_id:`) exactly — NOT `is_integration`, which
                    # is only correct for the advisory query_to_sql prompt hint.
                    # `is_integration = s.get("db_configs") is None` is a strict
                    # identity check that's wrongly False when db_configs is
                    # present-but-empty ([]), which would silently disable
                    # tenant scoping here (confirmed via a live trace — a
                    # SalesPlay query ran with no tenant_id filter at all).
                    tenant_id=nl_tenant_id,
                    row_limit=row_limit,
                    history_months=history["months"],
                    set_query_timeout=_set_query_timeout,
                )
                # Report tools (cache-first SalesPlay report APIs): offered when
                # the tenant has a synced profile (only SalesPlay onboarding
                # creates one) and the report cache is enabled. Never fatal —
                # on any failure the plain SQL tools run as before.
                report_ctx = None
                if nl_tenant_id and os.getenv("REPORT_CACHE_ENABLED", "").lower() in ("1", "true", "yes"):
                    try:
                        from mcp_server.report_tools import load_report_context
                        report_ctx = load_report_context(conn, nl_tenant_id, tool_ctx)
                        _report_tools_active = report_ctx is not None
                    except Exception as _rc_err:
                        log.warning("Report tool context unavailable",
                                    user=user["email"], error=str(_rc_err))
                sql, columns, data = asyncio.run(_mcp_answer_business_question(
                    nl_question, tool_ctx, llm, api_key, user["email"],
                    conversation_history=conv_history, extra_hints=extra_hints,
                    currency=nl_currency, shop_timezone=nl_shop_timezone,
                    report_ctx=report_ctx,
                ))
                steps.append({"label": "Answered via MCP tool-calling", "status": "done"})
            except Exception as _mcp_err:
                log.warning("MCP tool-calling path failed, falling back to one-shot SQL",
                            user=user["email"], error=str(_mcp_err))
                sql = columns = data = None

        if sql is None:
            steps.append({"label": "Generating SQL query", "status": "running"})
            sql = query_to_sql(
                nl_question, schemas, llm, fkeys, api_key=api_key,
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
            raw_cols = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            # Strip surrogate ID columns — keep only business-meaningful columns.
            columns = [c for c in raw_cols if not _ID_COL_RE.search(c)]
            data = [{k: _safe(v) for k, v in dict(zip(raw_cols, row)).items() if not _ID_COL_RE.search(k)} for row in rows]
            # Treat all-NULL results the same as 0 rows — no meaningful data found.
            if data and all(all(v is None for v in row.values()) for row in data):
                data = []
            if len(data) > row_limit:
                data = data[:row_limit]
            steps[-1]["status"] = "done"
        log.info("NL query complete", user=user["email"], rows=len(data), conv_id=conv_id or "stateless")
        _charge_op(user["email"], "nl_query_rows", len(data))

        # ── Answer narrative ──────────────────────────────────────────────────
        # Advisory answers ("how do I grow") route to data_query and a generic SQL
        # runs for context — but that query may return 0 rows, and an advice answer
        # must NOT depend on whether it did. So advice/forecast/trend always get the
        # analyst pass (data is context, possibly empty); lookups only when rows
        # came back. This fixes the "Found 0 results." inconsistency on advice.
        # ANSWER_EVERYTHING implies the smart analyst answer layer (advisory
        # answers must reason over the merchant's figures, not dump a table).
        _smart = _SMART_ANSWERS_ENABLED or _ANSWER_EVERYTHING_ENABLED
        _intent = (classification.get("intent") or "lookup") if _smart else "lookup"
        _show_data = _intent != "advice"
        analysis = None
        _want_analysis = (
            (_smart and (bool(data) or _intent in ("advice", "forecast", "trend")))
            or (req.think_mode and bool(data))
        )
        if _want_analysis:
            steps.append({"label": "Analyzing results", "status": "running"})
            try:
                if _smart:
                    # Coverage/upsell only where the date window is actually
                    # enforced (integration tenants) — otherwise a "covers last
                    # N days" claim could overstate an all-time own-DB result.
                    if nl_tenant_id:
                        _period_label, _plan_tier, _over_range = _derive_period(nl_question, history)
                        # Avoid double-upsell: report tools already own the
                        # plan-window message when they're active.
                        if _report_tools_active:
                            _over_range = False
                    else:
                        _period_label, _plan_tier, _over_range = "", "", False
                    # Real token streaming only when someone's listening (SSE);
                    # on the plain endpoint on_delta stays None → one-shot call.
                    _delta = (lambda t: _progress.emit("token", {"text": t})) if _progress.has_listener() else None
                    analysis = _run_answer(
                        nl_question, columns, data, llm, api_key, user["email"],
                        currency=nl_currency, period_label=_period_label,
                        plan_tier=_plan_tier, history_months=history["months"],
                        over_range=_over_range, on_delta=_delta,
                    )
                else:
                    analysis = _run_think_analysis(nl_question, columns, data, llm, api_key, user["email"], currency=nl_currency)
                analysis = fix_currency_symbol(analysis, nl_currency)
                log.info("Answer analysis complete", user=user["email"])
                steps[-1]["status"] = "done"
            except Exception as _te:
                log.warning("Answer analysis failed", user=user["email"], error=str(_te))
                steps[-1]["status"] = "failed"

        # T4: advisory/strategy answers about legal/financial/medical topics get
        # one honest "general information" caveat — answered, never refused.
        if analysis and _intent == "advice" and safety_gate(nl_question).get("action") == "caveat":
            analysis = add_advice_caveat(analysis)

        # ── Build conversation summary & persist ──────────────────────────────
        answer_summary = f"Found {len(data)} result{'s' if len(data) != 1 else ''}."
        # Brand-new integration-connected tenants with essentially no synced data
        # get a friendlier message instead of a flat "Found 0 results" — that's
        # a confusing first impression for someone who hasn't recorded any sales
        # yet (confirmed via trial user investigation). Scoped tightly: only
        # fires when this specific query returned 0 rows AND the tenant's total
        # synced row count across ALL their data is near-zero — never for a
        # healthy tenant whose specific query just happens to return 0 rows.
        if len(data) == 0 and is_integration:
            try:
                from integrations import get_user_total_rows
                _total_rows = get_user_total_rows(user["email"])
            except Exception:
                _total_rows = None
            if _total_rows is not None and _total_rows < 10:
                answer_summary = (
                    "It looks like your account doesn't have any synced data yet. "
                    "If you've just connected your store, a sync may still be in "
                    "progress — check your integration status, or try again in a few minutes."
                )
        # D5: the data path must NEVER return a blank answer. If nothing produced
        # user-visible narrative (e.g. a 0-row lookup), synthesise one — the
        # tailored near-empty-tenant message if we have it, otherwise a generic
        # empty-result narrative that names the period and offers a next step.
        if len(data) == 0 and not (analysis and analysis.strip()):
            analysis = answer_summary if not answer_summary.startswith("Found ") \
                else _empty_result_narrative(nl_question)
        if columns and data:
            num_col = _pick_summary_column(columns, data[0])
            if num_col:
                try:
                    total = sum(float(r.get(num_col, 0) or 0) for r in data)
                    if _is_money_column(num_col):
                        answer_summary += f" {num_col.replace('_', ' ')} = {nl_currency}{total:,.2f}"
                    else:
                        answer_summary += f" {num_col.replace('_', ' ')} = {total:,.2f}"
                except Exception:
                    pass
        # Smart answers: the analyst prose IS the answer — store it as the
        # assistant turn so conversation memory and follow-ups have real content,
        # not "Found N results."
        if _smart and analysis:
            answer_summary = analysis
        msg_id = None
        if conv_id:
            try:
                stat_col = _pick_summary_column(columns, data[0]) if data else None
                _conv.save_message(conv_id, "user", req.question)
                msg_id = _conv.save_message(
                    conv_id, "assistant", answer_summary,
                    sql_query=sql, row_count=len(data),
                    columns=columns, data=data, stat_col=stat_col,
                    analysis=analysis,
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
                "last hour", "last week", "last month", "last year",
                "right now", "current", "latest", "recent",
                "tonight", "this morning", "this afternoon",
            )
            if any(kw in nl_question.lower() for kw in _TIME_KEYWORDS):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    _synced = _dt.fromisoformat(nl_last_sync_at.replace("Z", "+00:00"))
                    if _synced.tzinfo is None:
                        _synced = _synced.replace(tzinfo=_tz.utc)
                    _age_min = (_dt.now(_tz.utc) - _synced).total_seconds() / 60
                    if _age_min > 60:
                        _days  = int(_age_min // 1440)
                        _hours = int((_age_min % 1440) // 60)
                        _mins  = int(_age_min % 60)
                        if _days:
                            _age_str = f"{_days}d {_hours}h"
                        elif _hours:
                            _age_str = f"{_hours}h {_mins}m"
                        else:
                            _age_str = f"{_mins}m"
                        if len(data) == 0 and _age_min > 1440:
                            _note = (
                                f"Your SalesPlay connection hasn't synced since "
                                f"{_synced.strftime('%B %d, %Y')} ({_age_str} ago), which is "
                                f"likely why this period has no data. Please reconnect your "
                                f"SalesPlay integration in Settings to resume syncing."
                            )
                        else:
                            _note = (
                                f"Note: your Salesplay data was last synced {_age_str} ago. "
                                f"Transactions added since then are not included in this result."
                            )
                        analysis = f"{analysis}\n\n{_note}" if analysis else _note
                except Exception as _age_err:
                    log.debug("Staleness note skipped", error=str(_age_err))

        # Backend picks the column to total in the "· X" summary (money → count →
        # none) and whether it's money, so the frontend renders one consistent
        # suffix instead of re-deriving its own (divergent) first-numeric logic.
        _sum_col = _pick_summary_column(columns, data[0]) if data else None
        # _show_data (computed above with _intent): advice answers are prose-only,
        # so the frontend hides the unrelated chart/table/summary.
        response = _base_query_response(
            success=True, type="data",
            steps=steps,
            columns=columns, data=data, row_count=len(data),
            analysis=analysis, think_mode=req.think_mode,
            conversation_id=conv_id, data_as_of=nl_last_sync_at,
            show_data=_show_data,
            summary_col=_sum_col,
            summary_is_money=bool(_sum_col and _is_money_column(_sum_col)),
            money_cols=[c for c in columns if _is_money_column(c)],
            message_id=msg_id,
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
        log.error("NL query failed", user=user["email"], error=str(e), exc_info=True)
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


@v1.post("/query")
@_limiter.limit(RL_COMPUTE)
def natural_language_query(request: Request, req: NLQueryRequest, user: dict = Depends(current_user)):
    return _natural_language_query_impl(request, req, user)


# ── SSE streaming variant ─────────────────────────────────────────────────────
# Streams: step (pipeline progress) → thinking (model reasoning between tool
# calls) → token (answer text chunks) → data (the full JSON payload the plain
# endpoint would have returned) → done. Errors degrade to error + done.
# Flag-gated: clients treat a 404 as "not enabled" and fall back to POST /v1/query.

_SSE_STREAMING_ENABLED = os.getenv("SSE_STREAMING_ENABLED", "").lower() in ("1", "true", "yes")


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


# What the user sees while we work. Internal pipeline labels (SQL, queries,
# reports, tool names) are never streamed — the widget should feel like an AI
# assistant thinking, not a query tool executing. The sync endpoint's steps
# array keeps the internal labels (used by the main app's debug view).
_FRIENDLY_STEP_LABELS = {
    "Analyzing your question": "Understanding your question",
    "Loading your data schema": "Looking at your business data",
    "Generating SQL query": "Thinking through your question",
    "Running your query": "Analyzing your data",
    "Analyzing results": "Putting your answer together",
    "Combining results": "Putting your answer together",
    "Answered via MCP tool-calling": None,          # internal — never shown
}


def _friendly_step(payload: dict):
    """Map an internal step event to user-facing copy; None = don't stream it."""
    label = (payload or {}).get("label") or ""
    if label in _FRIENDLY_STEP_LABELS:
        friendly = _FRIENDLY_STEP_LABELS[label]
        return {"label": friendly, "status": "running"} if friendly else None
    if label.startswith("Running query"):           # multi-step sub-queries
        return {"label": "Analyzing your data", "status": "running"}
    return {"label": label, "status": "running"}    # already user-facing (tool labels)


def _answer_chunks(text: str, words_per_chunk: int = 8):
    words = (text or "").split(" ")
    for i in range(0, len(words), words_per_chunk):
        yield " ".join(words[i:i + words_per_chunk]) + (" " if i + words_per_chunk < len(words) else "")


async def _stream_query_events(request: Request, req: NLQueryRequest, user: dict):
    import queue as _queue
    import threading as _threading

    q: "_queue.Queue" = _queue.Queue()
    _DONE = object()

    def worker():
        # The emitter lives in this thread's context; asyncio.run() inside the
        # pipeline (MCP tool loop) inherits it, so tool events stream too.
        token = _progress.set_emitter(lambda ev, payload: q.put((ev, payload)))
        try:
            result = _natural_language_query_impl(request, req, user)
            q.put(("result", result))
        except HTTPException as e:
            q.put(("error", {"message": str(e.detail)}))
        except Exception as e:
            get_logger(__name__).error("Stream query failed", user=user["email"], error=str(e))
            q.put(("error", {"message": "Something went wrong while processing your question. "
                                        "Please try rephrasing it or try again shortly."}))
        finally:
            _progress.reset_emitter(token)
            q.put(_DONE)

    _threading.Thread(target=worker, daemon=True, name="sse-query").start()
    loop = asyncio.get_running_loop()
    streamed_tokens = False
    while True:
        item = await loop.run_in_executor(None, q.get)
        if item is _DONE:
            break
        event, payload = item
        if event == "token":
            streamed_tokens = True
            yield _sse_event("token", payload)
        elif event == "result":
            # If _run_answer already streamed real LLM token deltas, don't
            # re-chunk the same text. Otherwise (conversational/clarification/
            # legacy message, or no listener) fall back to post-hoc chunking so
            # the client still gets a streamed answer.
            if not streamed_tokens:
                text = payload.get("analysis") or payload.get("message") or ""
                for chunk in _answer_chunks(text):
                    yield _sse_event("token", {"text": chunk})
            yield _sse_event("data", payload)
        elif event == "step":
            friendly = _friendly_step(payload)
            if friendly:
                yield _sse_event("step", friendly)
        else:
            yield _sse_event(event, payload)
    yield _sse_event("done", {})


@v1.post("/query/stream")
@_limiter.limit(RL_COMPUTE)
async def natural_language_query_stream(request: Request, req: NLQueryRequest,
                                        user: dict = Depends(current_user)):
    if not _SSE_STREAMING_ENABLED:
        raise HTTPException(status_code=404, detail="Streaming is not enabled.")
    return StreamingResponse(
        _stream_query_events(request, req, user),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


class VoteRequest(BaseModel):
    vote: Optional[int] = None  # 1 = thumbs up, -1 = thumbs down, None/0 = clear


@v1.patch("/conversations/{conv_id}/messages/{message_id}/vote")
@_limiter.limit(RL_WRITE)
def api_vote_message(request: Request, conv_id: str, message_id: int, body: VoteRequest,
                     user: dict = Depends(current_user)):
    vote = body.vote or None
    if vote not in (1, -1, None):
        raise HTTPException(status_code=422, detail="vote must be 1, -1, or null.")
    try:
        ok = _conv.set_vote(conv_id, message_id, user["email"], vote)
        if not ok:
            raise HTTPException(status_code=404, detail="Message not found.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        log.error("vote_message failed", user=user["email"], error=str(e))
        raise _server_error("Could not save vote.")


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
    _rpt_locale   = s.get("locale") or {}
    nl_currency   = _rpt_locale.get("currency") or "$"
    nl_country    = _rpt_locale.get("country") or ""
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
                user_email=user["email"],
                currency=nl_currency, country=nl_country,
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
            user_email=user["email"],
            currency=nl_currency, country=nl_country,
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
                    "note": t.get("note"),
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
    _plan_history   = get_plan_history_limit(user["email"])
    _row_limit      = _plan_history["row_limit"]
    _history_months = _plan_history["months"]

    try:
        integration = get_integration(user["email"], provider_id)
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found or not connected.")

        table_prefix = integration["table_prefix"]

        # Check result cache BEFORE borrowing a DB connection.
        # Cached or not, the user is always billed — _charge_op fires either way.
        if provider_id == "salesplay":
            from providers.salesplay.analytics import run_salesplay_analytics, _cache_get
            cached_result = _cache_get(table_prefix, req.template_id, _history_months)
            if cached_result is not None:
                result = {**cached_result, "source": "integration", "provider": provider_id, "cached": True}
                _apply_row_limit(result, _row_limit)
                _charge_op(user["email"], _ANALYTICS_OP.get(req.template_id, "prebuilt_template"),
                           result.get("row_count", 0))
                return result

        conn = _get_internal_conn()
        try:
            if provider_id == "salesplay":
                result = run_salesplay_analytics(conn, table_prefix, req.template_id, _history_months)
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
