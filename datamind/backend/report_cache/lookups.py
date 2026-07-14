"""
report_cache/lookups.py
=========================
Name -> id resolution and shop authorization for the answer layer
(docs/plan/PLAN_02_Profile_And_Subscription_Sync.md Step 2, doc 08 §3.6).

Reads tenant_profile/tenant_shop/tenant_cashier — populated by
report_cache/profile.py:sync_tenant_profile(). All functions are read-only
and safe to call from a request path (no external HTTP calls).

Tenant safety (doc 06/08, PLAN_00 §0.6): is_shop_allowed() is the guard that
must run before any model-suggested shop_id is forwarded to ReportAPIClient —
shop_id is never trusted as a model-visible parameter without this check.
"""

import json
from typing import Optional

import pool
from logger import get_logger

log = get_logger(__name__)

_DEFAULT_CURRENCY_SYMBOL = "$"  # same "no correction available" sentinel llm.py:fix_currency_symbol uses


def get_profile(tenant_id: str) -> Optional[dict]:
    """Full tenant_profile row (JSON columns parsed), or None if never synced."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM tenant_profile WHERE tenant_id=%s", (tenant_id,))
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    if row is None:
        return None

    for json_col in ("number_format", "profile_json"):
        value = row.get(json_col)
        if isinstance(value, str):
            try:
                row[json_col] = json.loads(value)
            except (TypeError, ValueError):
                log.warning("get_profile: unparseable JSON column", tenant=tenant_id, column=json_col)
    return row


def list_shops(tenant_id: str) -> list:
    """[{shop_id, shop_name}, ...] for a tenant, ordered by name."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT shop_id, shop_name FROM tenant_shop WHERE tenant_id=%s ORDER BY shop_name",
            (tenant_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return rows


def resolve_shop(tenant_id: str, text: str) -> Optional[str]:
    """"Colombo" -> "1072". Exact shop_id match first, then exact (case-insensitive)
    name match, then case-insensitive substring match. Returns None if nothing
    matches or more than one shop matches the substring (ambiguous — caller
    should ask the user to clarify rather than guess)."""
    if not text:
        return None
    text = text.strip()
    shops = list_shops(tenant_id)
    if not shops:
        return None

    for shop in shops:
        if shop["shop_id"] == text:
            return shop["shop_id"]

    text_lower = text.lower()
    for shop in shops:
        if (shop["shop_name"] or "").strip().lower() == text_lower:
            return shop["shop_id"]

    matches = [s for s in shops if text_lower in (s["shop_name"] or "").strip().lower()]
    if len(matches) == 1:
        return matches[0]["shop_id"]
    return None


def is_shop_allowed(tenant_id: str, shop_id: str) -> bool:
    """Authorization guard (doc 08 §3.6): is shop_id one this tenant actually
    owns? "all"/blank is always allowed — it's the report APIs' own
    all-shops sentinel (BaseReportRequest::getShops() default), not a
    specific shop a tenant could spoof."""
    if not shop_id or shop_id.strip().lower() == "all":
        return True
    return any(s["shop_id"] == shop_id for s in list_shops(tenant_id))


def list_cashiers(tenant_id: str) -> list:
    """[{cashier_id, cashier_name}, ...] for a tenant, ordered by name.
    NOTE: report_cache/profile.py's module docstring explains that report
    APIs' `cashier_id` query param actually expects cashier_name's value, not
    cashier_id — read that before wiring this into a report fetch call."""
    conn = pool.get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT cashier_id, cashier_name FROM tenant_cashier WHERE tenant_id=%s ORDER BY cashier_name",
            (tenant_id,),
        )
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return rows


def currency_symbol(tenant_id: str) -> str:
    """Tenant's display currency symbol, e.g. "$", "Rs.". Falls back to "$"
    (llm.py:fix_currency_symbol's own no-op sentinel) if the tenant has no
    synced profile yet."""
    profile = get_profile(tenant_id)
    if profile and profile.get("currency_symbol"):
        return profile["currency_symbol"]
    log.warning("currency_symbol: no profile found, defaulting", tenant=tenant_id)
    return _DEFAULT_CURRENCY_SYMBOL
