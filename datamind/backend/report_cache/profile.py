"""
report_cache/profile.py
=========================
Syncs tenant_profile / tenant_shop / tenant_cashier from the POS's
GET /app/profile (docs/plan/PLAN_02_Profile_And_Subscription_Sync.md).

Response shape verified against the actual controller/repository source
(salesplay-internal-api-v2), not guessed:
  - App/Service/ProfileController@profile — top-level envelope: {status, user,
    pages, shop_list, cashier_list, employee_list, pos_employees,
    support_email, developer_doc_url, access_info}. currency/ui_language/
    number_format/timezone are nested under `user` (ProfileController copies
    them there from ProfileDataHelper before returning), NOT top-level.
  - Repositories/Mysql/ProfileDataRepository@getShopList — shop_list rows:
    {location_id, name, is_enable}. Already filtered to enabled shops
    server-side (WHERE l.is_enable='1'), so no further filtering needed here.
  - Repositories/Mysql/ProfileDataRepository@getCashierList — cashier_list
    rows: {user_name, name, user_id}.

IMPORTANT — cashier_id is a name, not a numeric id. Every report controller
(e.g. SalesSummaryController@getMainSalesData) filters cashiers with
`invoice_cashier_name = '<cashier_id param>'` — i.e. the report APIs'
`cashier_id` query param is actually matched against a cashier's *display
name*, not a numeric id. tenant_cashier.cashier_id below stores the stable
`user_id` (safe as a DB key, avoids collisions), but tenant_cashier.cashier_name
holds the value that must actually be sent as the `cashier_id` query param
to ReportAPIClient.fetch_report(). Whoever wires cashier filtering in PLAN 05
must pass cashier_name, not cashier_id, to the report API — this is a POS API
naming quirk, not a bug here.

IMPORTANT — no ISO currency code is available from this endpoint.
`user.currency` and `user.number_format.profile_currency` are the same
underlying value (both trace back to device_backup_profile.profile_currency
in the POS DB) and both are a *display symbol* (e.g. "$", "Rs."), used
elsewhere in this exact codebase the same way — see llm.py:fix_currency_symbol,
which substitutes it directly for a literal '$' in text. tenant_profile.currency
and .currency_symbol are therefore populated with the same value; there is no
separate ISO 4217 code to store.

See PLAN_02's warning: the AI subscription tier is NEVER read from this
payload (that would be the POS back-office plan) — it comes from
report_cache.tiers, which reads DataMind's own billing.py.
"""

import json
from datetime import datetime, timedelta
from typing import Optional

import pool
from logger import get_logger
from report_cache.auth import get_report_token
from report_cache.client import ReportAPIClient
from report_cache.tiers import get_ai_tier, history_months_for

log = get_logger(__name__)

_DEFAULT_STALE_HOURS = 24


def map_profile(raw: dict):
    """Pure mapping: raw /app/profile JSON -> (profile_row, shop_rows, cashier_rows).
    No I/O — easy to unit test against a captured fixture."""
    user = raw.get("user") or {}
    number_format = user.get("number_format") or {}
    currency = (user.get("currency") or number_format.get("profile_currency") or "").strip()
    master_username = (user.get("master_username") or user.get("user_name") or "").strip()

    profile_row = {
        "master_username": master_username,
        "currency": currency,
        "currency_symbol": currency,  # same value — see module docstring
        "number_format": number_format,
        "ui_language": (user.get("ui_language") or "en_US").strip(),
        "timezone": (user.get("timezone") or "UTC").strip(),
        "profile_json": raw,
    }

    shop_rows = [
        {"shop_id": str(s["location_id"]), "shop_name": (s.get("name") or str(s["location_id"])).strip()}
        for s in (raw.get("shop_list") or [])
        if s.get("location_id") is not None
    ]

    cashier_rows = [
        {
            "cashier_id": str(c["user_id"]),
            # the value to pass as the report API's cashier_id query param — see module docstring
            "cashier_name": (c.get("name") or c.get("user_name") or str(c["user_id"])).strip(),
        }
        for c in (raw.get("cashier_list") or [])
        if c.get("user_id") is not None
    ]

    return profile_row, shop_rows, cashier_rows


def _upsert_profile_row(conn, tenant_id: str, profile_row: dict, tier: str, history_months: Optional[int]) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO tenant_profile
            (tenant_id, master_username, currency, currency_symbol, number_format,
             ui_language, timezone, subscription_tier, history_months, profile_json, refreshed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
            master_username=VALUES(master_username), currency=VALUES(currency),
            currency_symbol=VALUES(currency_symbol), number_format=VALUES(number_format),
            ui_language=VALUES(ui_language), timezone=VALUES(timezone),
            subscription_tier=VALUES(subscription_tier), history_months=VALUES(history_months),
            profile_json=VALUES(profile_json), refreshed_at=NOW()
        """,
        (
            tenant_id, profile_row["master_username"], profile_row["currency"],
            profile_row["currency_symbol"], json.dumps(profile_row["number_format"]),
            profile_row["ui_language"], profile_row["timezone"], tier, history_months,
            json.dumps(profile_row["profile_json"]),
        ),
    )
    cursor.close()


def _replace_shops(conn, tenant_id: str, shop_rows: list) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tenant_shop WHERE tenant_id=%s", (tenant_id,))
    if shop_rows:
        cursor.executemany(
            "INSERT INTO tenant_shop (tenant_id, shop_id, shop_name) VALUES (%s, %s, %s)",
            [(tenant_id, r["shop_id"], r["shop_name"]) for r in shop_rows],
        )
    cursor.close()


def _replace_cashiers(conn, tenant_id: str, cashier_rows: list) -> None:
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tenant_cashier WHERE tenant_id=%s", (tenant_id,))
    if cashier_rows:
        cursor.executemany(
            "INSERT INTO tenant_cashier (tenant_id, cashier_id, cashier_name) VALUES (%s, %s, %s)",
            [(tenant_id, r["cashier_id"], r["cashier_name"]) for r in cashier_rows],
        )
    cursor.close()


def sync_tenant_profile(tenant_id: str, access_token: Optional[str] = None) -> dict:
    """Fetch /app/profile, map to tenant_profile/tenant_shop/tenant_cashier rows,
    upsert. Returns the stored profile dict. Idempotent (re-running updates in
    place, no dupes — tenant_profile upserts by PK, shops/cashiers are
    delete-then-insert per tenant)."""
    token = access_token or get_report_token(tenant_id)
    if not token:
        raise ValueError(f"No SalesPlay API token found for tenant {tenant_id!r}")

    raw = ReportAPIClient(token).fetch_profile()
    profile_row, shop_rows, cashier_rows = map_profile(raw)

    # AI tier resolved separately from DataMind's own billing — never from `raw`.
    tier = get_ai_tier(tenant_id)
    months = history_months_for(tenant_id)

    conn = pool.get_internal_conn()
    try:
        _upsert_profile_row(conn, tenant_id, profile_row, tier, months)
        _replace_shops(conn, tenant_id, shop_rows)
        _replace_cashiers(conn, tenant_id, cashier_rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    log.info("Synced tenant profile", tenant=tenant_id, shops=len(shop_rows),
              cashiers=len(cashier_rows), tier=tier, history_months=months)

    return {
        **profile_row,
        "tenant_id": tenant_id,
        "subscription_tier": tier,
        "history_months": months,
        "shops": shop_rows,
        "cashiers": cashier_rows,
    }


def ensure_profile_fresh(tenant_id: str, max_age_hours: int = _DEFAULT_STALE_HOURS) -> None:
    """Sync only if the tenant's profile row is missing or refreshed_at is older
    than max_age_hours. Never raises — logs and no-ops on failure (PLAN_00
    fallback rule: safe to call before answering a question)."""
    try:
        conn = pool.get_internal_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT refreshed_at FROM tenant_profile WHERE tenant_id=%s", (tenant_id,))
            row = cursor.fetchone()
            cursor.close()
        finally:
            conn.close()

        if row and row.get("refreshed_at"):
            age = datetime.utcnow() - row["refreshed_at"]
            if age < timedelta(hours=max_age_hours):
                return

        sync_tenant_profile(tenant_id)
    except Exception as exc:
        log.warning("ensure_profile_fresh failed — leaving existing profile in place",
                    tenant=tenant_id, error=str(exc))
