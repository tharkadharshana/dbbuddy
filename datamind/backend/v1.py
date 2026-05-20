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

import json
import time
import collections
import datetime
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

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
    get_token_usage_history,
    check_ai_limit,
    check_plan_feature,
    get_plan_history_limit,
    calculate_tokens,
    charge_tokens,
)

log = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["partner-api-v1"])

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
_SYNC_RATE_WINDOW = 300  # 5 minutes
_sync_rate_store: dict = collections.defaultdict(float)  # key → last_trigger_ts


def _check_sync_rate(user_email: str, provider: str):
    key = f"{user_email}:{provider}"
    last = _sync_rate_store.get(key, 0)
    now = time.time()
    remaining = _SYNC_RATE_WINDOW - (now - last)
    if remaining > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Sync rate limit: wait {int(remaining)}s before triggering another sync.",
        )
    _sync_rate_store[key] = now


# ── API key auth ──────────────────────────────────────────────────────────────

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
    """Normalise and validate user_email. Raises 400 if missing."""
    if not user_email or not user_email.strip():
        raise HTTPException(status_code=400, detail="user_email is required.")
    return user_email.strip().lower()


def _require_pro(user_email: str):
    """Raise 402 if the user is not on the Pro plan."""
    ok, reason = check_plan_feature(user_email, "partner_api")
    if not ok:
        raise HTTPException(status_code=402, detail=reason)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(v):
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if hasattr(v, "__float__"):
        return float(v)
    return v


def _charge_op(email: str, op_type: str, rows: int):
    try:
        tokens = calculate_tokens(op_type, rows_returned=rows)
        charge_tokens(email, tokens, op_type, rows_returned=rows)
    except Exception as _ce:
        log.warning("v1 _charge_op failed silently", op=op_type, error=str(_ce))


# ── GET /v1/integrations ──────────────────────────────────────────────────────

@router.get("/integrations")
def v1_list_integrations(
    user_email: str = Query(..., description="End-user email address"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    List all integrations connected for a user.
    Returns provider_id, status, last_sync_at, and synced row count.
    """
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    rows = list_integrations(email)
    return {
        "ok": True,
        "data": [
            {
                "provider_id":  r.get("provider_id"),
                "status":       r.get("status"),
                "last_sync":    r.get("last_sync_at"),
                "row_count":    r.get("last_sync_rows", 0),
                "connected_at": r.get("created_at"),
            }
            for r in rows
        ],
        "total": len(rows),
    }


# ── POST /v1/sync/{provider} ──────────────────────────────────────────────────

class SyncRequest(BaseModel):
    user_email: str
    full: bool = False


@router.post("/sync/{provider}")
def v1_trigger_sync(
    provider: str,
    req: SyncRequest,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Trigger a manual sync for a user's integration.
    Rate-limited to 1 sync per provider per user per 5 minutes.
    Returns immediately; sync runs in background.
    """
    _require_partner(x_api_key)
    email = _resolve_user_email(req.user_email)
    _require_pro(email)

    integration = get_integration(email, provider)
    if not integration:
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
    return {
        "ok": True,
        "status": "started",
        "provider": provider,
        "sync_type": "full" if req.full else "delta",
    }


# ── GET /v1/records/{provider}/{type} ─────────────────────────────────────────

@router.get("/records/{provider}/{record_type}")
def v1_get_records(
    provider: str,
    record_type: str,
    user_email: str = Query(..., description="End-user email address"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    store_id: Optional[str] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Fetch paginated synced records from the unified integration_records table.
    Supports optional filtering by store_id and from (ISO date).
    Row count is capped at the user's plan row_limit.
    """
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

    # Build query with optional filters on the JSON data column
    where = ["tenant_id=%s", "provider_id=%s", "record_type=%s"]
    params: list = [tenant_id, provider, record_type]

    if from_date:
        where.append("external_created_at >= %s")
        params.append(from_date)

    where_sql = " AND ".join(where)
    count_sql = f"SELECT COUNT(*) FROM integration_records WHERE {where_sql}"
    data_sql  = (
        f"SELECT external_id, data, external_created_at, external_updated_at, synced_at "
        f"FROM integration_records WHERE {where_sql} "
        f"ORDER BY external_created_at DESC LIMIT %s OFFSET %s"
    )

    conn = _get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(count_sql, params)
        total = cur.fetchone()["COUNT(*)"]

        cur.execute(data_sql, params + [limit, offset])
        raw_rows = cur.fetchall()
    finally:
        conn.close()

    records = []
    for row in raw_rows:
        data = row.get("data")
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                pass
        record = {"external_id": row["external_id"], **(data if isinstance(data, dict) else {"raw": data})}
        # Apply store_id filter (JSON field — done post-fetch since MariaDB 10.4 JSON support varies)
        if store_id:
            if str(record.get("store_id", record.get("shop_id", ""))) != str(store_id):
                continue
        records.append(record)

    return {
        "ok": True,
        "provider": provider,
        "record_type": record_type,
        "data": records,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ── GET /v1/analytics/{template_id} ──────────────────────────────────────────

@router.get("/analytics/{template_id}")
def v1_run_analytics(
    template_id: str,
    user_email: str = Query(..., description="End-user email address"),
    provider: str = Query(..., description="Provider id, e.g. 'loyverse' or 'salesplay'"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Run an analytics template for a user's integration and return the result.
    Deducts credits exactly as the iframe analytics endpoint does.
    """
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
        runner = runner_map[provider]()
        result = runner(conn, table_prefix, template_id)
    except HTTPException:
        raise
    except Exception as e:
        log.error("v1 analytics failed", user=email, provider=provider,
                  template=template_id, error=str(e))
        raise HTTPException(status_code=500, detail="Analytics execution failed.")
    finally:
        conn.close()

    # Truncate to plan row limit
    if result.get("data") and len(result["data"]) > row_limit:
        result["data"] = result["data"][:row_limit]
        result["truncated"] = True

    result["source"] = "partner_api"
    result["provider"] = provider

    _charge_op(email, _ANALYTICS_OP.get(template_id, "prebuilt_template"),
                result.get("row_count", 0))

    log.info("v1 analytics served", user=email, provider=provider, template=template_id)
    return {"ok": True, **result}


# ── GET /v1/usage ─────────────────────────────────────────────────────────────

@router.get("/usage")
def v1_get_usage(
    user_email: str = Query(..., description="End-user email address"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    """
    Return token/credit usage for a user.
    Same data as GET /billing/usage but authenticated via partner API key.
    """
    _require_partner(x_api_key)
    email = _resolve_user_email(user_email)
    _require_pro(email)

    sub = get_user_subscription(email)
    return {
        "ok": True,
        "plan":               sub.get("plan_name"),
        "status":             sub.get("status"),
        "tokens_used":        sub.get("tokens_used", 0),
        "tokens_remaining":   round(
            float(sub.get("tokens_total_available", 0)) - float(sub.get("tokens_used", 0)), 2
        ),
        "tokens_limit":       sub.get("tokens_limit", 0),
        "tokens_pct":         sub.get("tokens_pct", 0),
        "trial_ends_at":      sub.get("period_end"),
        "period_start":       sub.get("period_start"),
        "period_end":         sub.get("period_end"),
    }
