"""
qa_routes.py — /qa/* development-only test harness.

Lets a developer put an account into any billing/cache state on demand — change
plan, expire a subscription, drain or refill tokens, move the subscription
window, wipe or inspect the report cache — so the scenarios in doc 14 §B2 can
actually be exercised instead of waited for.

═══════════════════════════════════════════════════════════════════════════════
THIS ROUTER MUTATES BILLING STATE. IT MUST NEVER MOUNT IN PRODUCTION.
═══════════════════════════════════════════════════════════════════════════════

Three INDEPENDENT locks, all of which must pass. This is deliberate
belt-and-braces: a single wrong line in a .env file must not be enough to
expose plan/token mutation to the internet.

  1. QA_ROUTES_ENABLED=true            — explicit opt-in, default off
  2. no production signal present      — refuses if FORCE_HTTPS=true or the
                                         DB host looks production-like
  3. caller in QA_ROUTES_EMAILS        — per-request allowlist, never blank

`is_qa_enabled()` is evaluated at import time for mounting (locks 1+2) and the
allowlist is re-checked on EVERY request (lock 3), so revoking access does not
require a restart.
"""

import os
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import billing
from auth import current_user
from logger import get_logger
from pool import get_internal_conn

log = get_logger(__name__)

router = APIRouter(prefix="/qa", tags=["qa-dev-only"])

# Hosts that look like production. Substring match — a QA route must fail
# CLOSED on anything ambiguous, so this list is deliberately broad.
_PROD_DB_MARKERS = ("prod", "rds.amazonaws", "cloudsql", "salesplaypos.com")


def _prod_signal() -> Optional[str]:
    """Return a reason string if anything suggests this is production."""
    if os.getenv("FORCE_HTTPS", "").lower() == "true":
        return "FORCE_HTTPS=true"
    host = (os.getenv("DATAMIND_DB_HOST") or "").lower()
    for marker in _PROD_DB_MARKERS:
        if marker in host:
            return f"DATAMIND_DB_HOST contains '{marker}'"
    return None


def is_qa_enabled() -> bool:
    """Locks 1 and 2 — evaluated by main.py before mounting the router."""
    if os.getenv("QA_ROUTES_ENABLED", "").lower() not in ("1", "true", "yes"):
        return False
    signal = _prod_signal()
    if signal:
        log.error("QA routes REFUSED to mount — production signal detected",
                  signal=signal)
        return False
    if not _allowlist():
        log.error("QA routes REFUSED to mount — QA_ROUTES_EMAILS is empty. "
                  "An allowlist is mandatory; there is no 'allow everyone' mode.")
        return False
    return True


def _allowlist() -> set:
    return {e.strip().lower()
            for e in os.getenv("QA_ROUTES_EMAILS", "").split(",") if e.strip()}


def qa_user(user: dict = Depends(current_user)) -> dict:
    """Lock 3 — re-checked on every request, so access can be revoked without
    a restart. Also re-checks locks 1+2 in case the process was mounted and the
    environment changed underneath it."""
    if os.getenv("QA_ROUTES_ENABLED", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(404, "Not found")
    signal = _prod_signal()
    if signal:
        log.error("QA route call blocked — production signal", signal=signal)
        raise HTTPException(404, "Not found")
    email = (user.get("email") or "").lower()
    if email not in _allowlist():
        log.warning("QA route call denied — caller not in allowlist", email=email)
        raise HTTPException(403, "Not authorised for QA routes.")
    return user


def _target(user: dict, email: Optional[str]) -> str:
    """QA actions default to the caller, but may target another account so one
    dev login can drive several seeded test tenants."""
    return (email or user["email"]).strip()


# ── inspection ────────────────────────────────────────────────────────────────

@router.get("/state")
def qa_state(email: Optional[str] = None, user: dict = Depends(qa_user)):
    """Everything that decides how a question gets answered, in one payload:
    plan, window, tokens, feature gates, and report-cache coverage."""
    target = _target(user, email)
    sub = billing.get_user_subscription(target)
    limits = billing.get_plan_history_limit(target)

    features = {}
    for feat in ("forecast", "anomaly_detection", "external_api",
                 "partner_api", "web_widget"):
        try:
            features[feat] = bool(billing.check_plan_feature(target, feat)[0])
        except Exception as exc:
            features[feat] = f"error: {exc}"

    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT table_prefix, provider_id, last_sync_at, status "
            "FROM user_integrations WHERE user_email=%s", (target,))
        integrations = cur.fetchall()
        tenant = next((i["table_prefix"] for i in integrations
                       if i.get("table_prefix")), None)

        cache = {}
        if tenant:
            cur.execute(
                "SELECT report_id, COUNT(*) AS months, MIN(month) AS oldest, "
                "MAX(month) AS newest, MAX(fetched_at) AS last_fetched "
                "FROM report_sync_state WHERE tenant_id=%s AND status='final' "
                "GROUP BY report_id ORDER BY report_id", (tenant,))
            cache["by_report"] = cur.fetchall()
            cur.execute(
                "SELECT COUNT(*) AS n FROM tenant_profile WHERE tenant_id=%s "
                "AND session_token_enc IS NOT NULL "
                "AND session_token_at > DATE_SUB(NOW(), INTERVAL 12 HOUR)",
                (tenant,))
            cache["live_token_valid"] = bool(cur.fetchone()["n"])
    finally:
        conn.close()

    return {
        "email": target,
        "plan": sub.get("plan_name"),
        "status": sub.get("status"),
        "period_start": str(sub.get("period_start")),
        "period_end": str(sub.get("period_end")),
        "tokens_used": sub.get("tokens_used"),
        "tokens_total_available": sub.get("tokens_total_available",
                                          sub.get("tokens_limit")),
        "history_months": limits["months"],
        "row_limit": limits["row_limit"],
        "window_start": str(limits["cutoff_date"]),
        "features": features,
        "tenant_id": tenant,
        "integrations": integrations,
        "report_cache": cache,
        "ai_flow": os.getenv("AI_FLOW", "legacy"),
        "report_cache_enabled": os.getenv("REPORT_CACHE_ENABLED", ""),
    }


# ── subscription mutation ─────────────────────────────────────────────────────

class PlanReq(BaseModel):
    email:        Optional[str] = None
    plan:         str                       # Starter | Growth | Pro
    status:       Optional[str] = "active"  # trial|active|expired|cancelled
    period_start: Optional[str] = None      # YYYY-MM-DD
    period_end:   Optional[str] = None


@router.post("/plan")
def qa_set_plan(req: PlanReq, user: dict = Depends(qa_user)):
    """Force an account onto a plan, with an arbitrary status and period.

    Writes user_subscriptions directly rather than going through
    subscribe_to_plan() on purpose: QA needs to create states the real payment
    flow cannot, such as an already-expired period or a window that started
    eight months ago, to test the plan-window boundaries in doc 14 §B2.
    """
    target = _target(user, req.email)
    if req.status not in ("trial", "active", "expired", "cancelled"):
        raise HTTPException(400, "status must be trial|active|expired|cancelled")

    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM subscription_plans WHERE name=%s", (req.plan,))
        plan = cur.fetchone()
        if not plan:
            cur.execute("SELECT name FROM subscription_plans")
            names = [r["name"] for r in cur.fetchall()]
            raise HTTPException(400, f"Unknown plan '{req.plan}'. Available: {names}")

        start = (date.fromisoformat(req.period_start) if req.period_start
                 else date.today())
        end = (date.fromisoformat(req.period_end) if req.period_end
               else start + timedelta(days=30))

        cur.execute("DELETE FROM user_subscriptions WHERE user_email=%s", (target,))
        cur.execute(
            "INSERT INTO user_subscriptions (user_email, plan_id, status, "
            "period_start, period_end) VALUES (%s,%s,%s,%s,%s)",
            (target, plan["id"], req.status, start, end))
        conn.commit()
    finally:
        conn.close()

    billing.invalidate_sub_cache(target)
    log.warning("QA: subscription overwritten", actor=user["email"],
                target=target, plan=req.plan, status=req.status)
    return qa_state(email=target, user=user)


@router.post("/expire")
def qa_expire(email: Optional[str] = None, user: dict = Depends(qa_user)):
    """Backdate the current period so the subscription reads as expired."""
    target = _target(user, email)
    conn = get_internal_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE user_subscriptions SET status='expired', "
            "period_end=%s WHERE user_email=%s",
            (date.today() - timedelta(days=1), target))
        conn.commit()
    finally:
        conn.close()
    billing.invalidate_sub_cache(target)
    log.warning("QA: subscription expired", actor=user["email"], target=target)
    return qa_state(email=target, user=user)


# ── token mutation ────────────────────────────────────────────────────────────

class TokenReq(BaseModel):
    email:  Optional[str] = None
    action: str                      # drain | reset | set
    value:  Optional[float] = None   # required for 'set'


@router.post("/tokens")
def qa_tokens(req: TokenReq, user: dict = Depends(qa_user)):
    """drain = burn the whole allowance (tests the limit gate),
    reset = zero usage, set = an exact tokens_used figure."""
    target = _target(user, req.email)
    sub = billing.get_user_subscription(target)
    period_start = sub.get("period_start") or date.today().replace(day=1)

    if req.action == "drain":
        used = float(sub.get("tokens_total_available")
                     or sub.get("tokens_limit") or 0)
    elif req.action == "reset":
        used = 0.0
    elif req.action == "set":
        if req.value is None:
            raise HTTPException(400, "value is required when action='set'")
        used = float(req.value)
    else:
        raise HTTPException(400, "action must be drain|reset|set")

    conn = get_internal_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO subscription_usage (user_email, period_start, tokens_used) "
            "VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE tokens_used=VALUES(tokens_used)",
            (target, period_start, used))
        conn.commit()
    finally:
        conn.close()

    billing.invalidate_sub_cache(target)
    log.warning("QA: tokens overwritten", actor=user["email"], target=target,
                action=req.action, tokens_used=used)
    return qa_state(email=target, user=user)


# ── report cache mutation ─────────────────────────────────────────────────────

class CacheReq(BaseModel):
    email:     Optional[str] = None
    report_id: Optional[str] = None   # omit = every report
    month:     Optional[str] = None   # 'YYYY-MM'; omit = every month


@router.post("/cache/clear")
def qa_cache_clear(req: CacheReq, user: dict = Depends(qa_user)):
    """Delete cached months so the next question takes the live path — the
    only way to re-test a cold-cache scenario without waiting a month."""
    target = _target(user, req.email)
    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT table_prefix FROM user_integrations WHERE user_email=%s "
            "AND table_prefix IS NOT NULL LIMIT 1", (target,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, f"No integration/tenant for {target}")
        tenant = row["table_prefix"]

        where, params = ["tenant_id=%s"], [tenant]
        if req.report_id:
            where.append("report_id=%s"); params.append(req.report_id)
        if req.month:
            where.append("month=%s"); params.append(f"{req.month}-01")
        clause = " AND ".join(where)

        deleted = {}
        for table in ("report_sync_state", "report_fact", "report_dim_fact"):
            col = "period_start" if table == "report_fact" else "month"
            sql = f"DELETE FROM {table} WHERE " + clause.replace("month=", f"{col}=")
            cur.execute(sql, tuple(params))
            deleted[table] = cur.rowcount
        conn.commit()
    finally:
        conn.close()

    log.warning("QA: report cache cleared", actor=user["email"], target=target,
                tenant=tenant, deleted=deleted)
    return {"tenant_id": tenant, "deleted": deleted}


@router.post("/cache/age")
def qa_cache_age(req: CacheReq, days: int = 30, user: dict = Depends(qa_user)):
    """Backdate fetched_at so months look stale — exercises the deep
    re-finalization path (REPORT_CACHE_DEEP_REFINALIZE_DAYS) without waiting."""
    target = _target(user, req.email)
    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT table_prefix FROM user_integrations WHERE user_email=%s "
            "AND table_prefix IS NOT NULL LIMIT 1", (target,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(400, f"No integration/tenant for {target}")
        tenant = row["table_prefix"]

        sql = ("UPDATE report_sync_state SET fetched_at="
               "DATE_SUB(NOW(), INTERVAL %s DAY) WHERE tenant_id=%s")
        params = [days, tenant]
        if req.report_id:
            sql += " AND report_id=%s"; params.append(req.report_id)
        cur.execute(sql, tuple(params))
        conn.commit()
        aged = cur.rowcount
    finally:
        conn.close()

    log.warning("QA: report cache aged", actor=user["email"], target=target,
                days=days, rows=aged)
    return {"tenant_id": tenant, "aged_rows": aged, "days": days}
