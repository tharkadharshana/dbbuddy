"""
v1.py
=====
DataMind Partner API v1 — server-to-server endpoints for embed partners.

Auth: X-API-Key header mapped to embed_partners.partner_key.
User context: user_email query param or request body (backend-to-backend call;
the partner already knows which of their users is requesting).

Endpoints:
  GET  /v1/integrations               — list user's connected integrations
  POST /v1/sync/{provider}            — trigger a manual sync
  GET  /v1/records/{provider}/{type}  — fetch paginated synced records
  GET  /v1/analytics/{template_id}    — run an analytics template
  GET  /v1/usage                      — get token/credit usage for a user
"""

import time
import collections
import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from limiter import limiter as _limiter, RL_V1

from logger import get_logger
from pool import get_internal_conn as _get_conn
from integrations import (
    list_integrations,
    get_integration,
    trigger_sync,
    _get_internal_conn,
)
from billing import (
    get_user_subscription,
    check_ai_limit,
    check_plan_feature,
    get_plan_history_limit,
    calculate_tokens,
    charge_tokens,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["Partner API v1"])

# ── Analytics op → billing op type mapping ────────────────────────────────────
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

# ── Sync rate limiter: 1 sync per (user_email, provider) per 5 min ────────────
_SYNC_RATE_WINDOW = 300
_sync_rate_store: dict = collections.defaultdict(float)


def _check_sync_rate(user_email: str, provider: str):
    key = f"{user_email}:{provider}"
    now = time.time()
    remaining = _SYNC_RATE_WINDOW - (now - _sync_rate_store.get(key, 0))
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Sync rate limit: wait {int(remaining)}s before triggering another sync.",
        )
    _sync_rate_store[key] = now


# ── Auth / plan guards ────────────────────────────────────────────────────────

def _require_partner(x_api_key: Optional[str]) -> dict:
    """Validate X-API-Key against embed_partners. Returns partner row or raises 401."""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header.")
    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM embed_partners WHERE partner_key=%s AND active=1",
            (x_api_key,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key.")
    return row


def _resolve_user_email(user_email: Optional[str]) -> str:
    if not user_email or not user_email.strip():
        raise HTTPException(status_code=400, detail="user_email is required.")
    return user_email.strip().lower()


def _require_pro(user_email: str):
    """Raise 402 if the user is not on the Pro plan."""
    ok, reason = check_plan_feature(user_email, "partner_api")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _charge_op(email: str, op_type: str, rows: int):
    try:
        tokens = calculate_tokens(op_type, rows_returned=rows)
        charge_tokens(email, tokens, op_type, rows_returned=rows)
    except Exception as _ce:
        log.warning("v1 _charge_op failed silently", op=op_type, error=str(_ce))


# ── Response models ───────────────────────────────────────────────────────────

class IntegrationItem(BaseModel):
    provider_id: str
    status: str
    last_sync: Optional[str] = None
    row_count: int = 0
    connected_at: Optional[str] = None


class IntegrationsResponse(BaseModel):
    ok: bool
    data: List[IntegrationItem]
    total: int


class SyncRequest(BaseModel):
    user_email: str
    full: bool = Field(False, description="True = full re-sync, False = delta")


class SyncResponse(BaseModel):
    ok: bool
    status: str
    provider: str
    sync_type: str


class RecordsResponse(BaseModel):
    ok: bool
    provider: str
    record_type: str
    data: List[Dict[str, Any]]
    total: int
    limit: int
    offset: int


class AnalyticsResponse(BaseModel):
    ok: bool
    source: str
    provider: str
    title: Optional[str] = None
    row_count: Optional[int] = None
    truncated: Optional[bool] = None
    data: Optional[List[Dict[str, Any]]] = None
    columns: Optional[List[str]] = None


class UsageResponse(BaseModel):
    ok: bool
    plan: Optional[str] = None
    status: Optional[str] = None
    tokens_used: float
    tokens_remaining: float
    tokens_limit: float
    tokens_pct: float
    trial_ends_at: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None


# ── GET /v1/integrations ──────────────────────────────────────────────────────

@router.get(
    "/integrations",
    response_model=IntegrationsResponse,
    summary="List user integrations",
    description="Returns all provider integrations connected for the given user.",
)
@_limiter.limit(RL_V1)
def v1_list_integrations(
    request: Request,
    user_email: str = Query(..., description="End-user email address"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    rows = list_integrations(email)
    return IntegrationsResponse(
        ok=True,
        total=len(rows),
        data=[
            IntegrationItem(
                provider_id=r.get("provider_id", ""),
                status=r.get("status", "unknown"),
                last_sync=str(r["last_sync_at"]) if r.get("last_sync_at") else None,
                row_count=r.get("last_sync_rows") or 0,
                connected_at=str(r["created_at"]) if r.get("created_at") else None,
            )
            for r in rows
        ],
    )


# ── POST /v1/sync/{provider} ──────────────────────────────────────────────────

@router.post(
    "/sync/{provider}",
    response_model=SyncResponse,
    summary="Trigger a manual sync",
    description=(
        "Trigger a delta or full sync for a user's provider integration. "
        "Returns immediately; sync runs in background. "
        "Rate-limited to 1 request per provider per user per 5 minutes."
    ),
)
@_limiter.limit(RL_V1)
def v1_trigger_sync(
    request: Request,
    provider: str,
    req: SyncRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _require_partner(x_api_key)
    email = _resolve_user_email(req.user_email)
    _require_pro(email)

    if not get_integration(email, provider):
        raise HTTPException(
            status_code=404,
            detail=f"Integration '{provider}' not connected for this user.",
        )

    _check_sync_rate(email, provider)

    try:
        trigger_sync(email, provider, full=req.full)
    except Exception as e:
        log.error("v1 trigger_sync failed", user=email, provider=provider, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to start sync.")

    log.info("v1 sync triggered", user=email, provider=provider, full=req.full)
    return SyncResponse(
        ok=True,
        status="started",
        provider=provider,
        sync_type="full" if req.full else "delta",
    )


# ── GET /v1/records/{provider}/{type} ─────────────────────────────────────────

@router.get(
    "/records/{provider}/{record_type}",
    response_model=RecordsResponse,
    summary="Fetch synced records",
    description=(
        "Paginated records from the unified integration_records table. "
        "Filter by `store_id` (pushed to SQL) and `from` (ISO date). "
        "Providers: `loyverse`, `salesplay`. "
        "Record types: `products`, `receipts`, `customers`, `employees`, `categories`, etc."
    ),
)
@_limiter.limit(RL_V1)
def v1_get_records(
    request: Request,
    provider: str,
    record_type: str,
    user_email: str = Query(..., description="End-user email address"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    store_id: Optional[str] = Query(None, description="Filter by store/shop ID"),
    from_date: Optional[str] = Query(None, alias="from", description="ISO date lower bound on external_created_at"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    integration = get_integration(email, provider)
    if not integration:
        raise HTTPException(
            status_code=404,
            detail=f"Integration '{provider}' not connected for this user.",
        )

    tenant_id = integration["table_prefix"]

    where: List[str] = ["tenant_id=%s", "provider_id=%s", "record_type=%s"]
    params: list = [tenant_id, provider, record_type]

    if from_date:
        where.append("external_created_at >= %s")
        params.append(from_date)

    # Push store_id filter to SQL using JSON_EXTRACT — covers both field naming conventions
    # (Loyverse uses store_id, SalesPlay uses shop_id inside the data JSON blob)
    if store_id:
        where.append(
            "(JSON_UNQUOTE(JSON_EXTRACT(data, '$.store_id')) = %s "
            "OR JSON_UNQUOTE(JSON_EXTRACT(data, '$.shop_id')) = %s)"
        )
        params.extend([store_id, store_id])

    where_sql = " AND ".join(where)
    conn = _get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT COUNT(*) AS cnt FROM integration_records WHERE {where_sql}", params)
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"SELECT external_id, data, external_created_at, external_updated_at, synced_at "
            f"FROM integration_records WHERE {where_sql} "
            f"ORDER BY external_created_at DESC LIMIT %s OFFSET %s",
            params + [limit, offset],
        )
        raw_rows = cur.fetchall()
    finally:
        conn.close()

    records = []
    for row in raw_rows:
        data = row.get("data") or {}
        if isinstance(data, str):
            try:
                import json
                data = json.loads(data)
            except Exception:
                data = {"raw": data}
        records.append({"external_id": row["external_id"], **(data if isinstance(data, dict) else {"raw": data})})

    return RecordsResponse(
        ok=True,
        provider=provider,
        record_type=record_type,
        data=records,
        total=total,
        limit=limit,
        offset=offset,
    )


# ── GET /v1/analytics/{template_id} ──────────────────────────────────────────

@router.get(
    "/analytics/{template_id}",
    response_model=AnalyticsResponse,
    summary="Run an analytics template",
    description=(
        "Run a pre-built analytics template against a user's integration and return the result. "
        "Deducts credits the same way the iframe analytics endpoint does. "
        "Available template IDs depend on the provider — see `GET /discover` for the full list."
    ),
)
@_limiter.limit(RL_V1)
def v1_run_analytics(
    request: Request,
    template_id: str,
    user_email: str = Query(..., description="End-user email address"),
    provider: str = Query(..., description="Provider id: `loyverse` or `salesplay`"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    ok, reason = check_ai_limit(email)
    if not ok:
        raise HTTPException(status_code=402, detail=reason)

    integration = get_integration(email, provider)
    if not integration:
        raise HTTPException(
            status_code=404,
            detail=f"Integration '{provider}' not connected for this user.",
        )
    table_prefix = integration["table_prefix"]

    try:
        row_limit = get_plan_history_limit(email)["row_limit"]
    except Exception:
        row_limit = 10_000

    runner_map = {
        "salesplay": lambda: __import__(
            "providers.salesplay.analytics", fromlist=["run_salesplay_analytics"]
        ).run_salesplay_analytics,
        "loyverse": lambda: __import__(
            "providers.loyverse.analytics", fromlist=["run_loyverse_analytics"]
        ).run_loyverse_analytics,
    }
    if provider not in runner_map:
        raise HTTPException(status_code=404, detail=f"No analytics available for provider '{provider}'.")

    conn = _get_internal_conn()
    try:
        result = runner_map[provider]()(conn, table_prefix, template_id)
    except HTTPException:
        raise
    except Exception as e:
        log.error("v1 analytics failed", user=email, provider=provider,
                  template=template_id, error=str(e))
        raise HTTPException(status_code=500, detail="Analytics execution failed.")
    finally:
        conn.close()

    if result.get("data") and len(result["data"]) > row_limit:
        result["data"] = result["data"][:row_limit]
        result["truncated"] = True

    _charge_op(email, _ANALYTICS_OP.get(template_id, "prebuilt_template"),
                result.get("row_count", 0))

    log.info("v1 analytics served", user=email, provider=provider, template=template_id)
    return AnalyticsResponse(
        ok=True,
        source="partner_api",
        provider=provider,
        title=result.get("title"),
        row_count=result.get("row_count"),
        truncated=result.get("truncated"),
        data=result.get("data"),
        columns=result.get("columns"),
    )


# ── GET /v1/usage ─────────────────────────────────────────────────────────────

@router.get(
    "/usage",
    response_model=UsageResponse,
    summary="Get token usage",
    description=(
        "Return token and credit usage for a user. "
        "Same data as `GET /billing/usage` but authenticated via partner API key."
    ),
)
@_limiter.limit(RL_V1)
def v1_get_usage(
    request: Request,
    user_email: str = Query(..., description="End-user email address"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    sub = get_user_subscription(email)
    tokens_total = float(sub.get("tokens_total_available") or 0)
    tokens_used  = float(sub.get("tokens_used") or 0)
    return UsageResponse(
        ok=True,
        plan=sub.get("plan_name"),
        status=sub.get("status"),
        tokens_used=tokens_used,
        tokens_remaining=round(tokens_total - tokens_used, 2),
        tokens_limit=float(sub.get("tokens_limit") or 0),
        tokens_pct=float(sub.get("tokens_pct") or 0),
        trial_ends_at=str(sub["period_end"]) if sub.get("period_end") else None,
        period_start=str(sub["period_start"]) if sub.get("period_start") else None,
        period_end=str(sub["period_end"]) if sub.get("period_end") else None,
    )
