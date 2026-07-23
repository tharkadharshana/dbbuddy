"""
report_cache/profile.py — sync the tenant's SalesPlay profile (currency,
language, timezone, number format, shops, cashiers) into the core DB, and
stash the live session's `aat` (encrypted) for chat-time report fetches.

Called from embed.py's salesplay_onboard on every widget open — the only
moment a working /app/* token exists (see client.py's auth notes). Everything
here is best-effort from the caller's perspective: onboarding must never fail
because of a profile-sync problem.

Response shape (verified against ProfileController@profile source):
  - user.currency / user.ui_language / user.timezone / user.number_format /
    user.date_format / user.time_format  (all under 'user', not top-level)
  - shop_list:    [{location_id, name, is_enable}]   (top-level)
  - cashier_list: [{user_name, name, user_id}]       (top-level)

The report APIs' `cashier_id` query param is matched against
invoice_cashier_name — a display NAME — so tenant_cashier.cashier_name
(from cashier_list[].name) is the value to send, not user_id.
"""

import json
from datetime import datetime

from integrations import _decrypt, _encrypt
from logger import get_logger
from pool import get_internal_conn

from .client import ReportAPIClient

log = get_logger(__name__)


def map_profile(raw: dict) -> dict:
    """Pure mapping of the /app/profile response to our storage shape."""
    user = raw.get("user") or {}
    nf = user.get("number_format")
    return {
        "currency":      user.get("currency"),
        "ui_language":   user.get("ui_language"),
        "timezone":      user.get("timezone"),
        "number_format": json.dumps(nf) if nf is not None else None,
        "date_format":   user.get("date_format"),
        "time_format":   user.get("time_format"),
        "shops": [
            {
                "shop_id":    str(s.get("location_id") or ""),
                "shop_name":  s.get("name") or "",
                "is_enabled": 1 if str(s.get("is_enable", "1")) == "1" else 0,
            }
            for s in raw.get("shop_list") or []
            if s.get("location_id") is not None
        ],
        "cashiers": [
            {
                "user_name":    c.get("user_name") or "",
                "cashier_id":   str(c.get("user_id") or "") or None,
                "cashier_name": c.get("name") or c.get("user_name") or "",
            }
            for c in raw.get("cashier_list") or []
            if c.get("user_name")
        ],
    }


def save_profile(conn, tenant_id: str, mapped: dict, access_token: str = None) -> None:
    """Upsert tenant_profile and replace shops/cashiers. Caller owns commit."""
    cursor = conn.cursor()
    token_enc = _encrypt({"aat": access_token}) if access_token else None
    now = datetime.utcnow()
    cursor.execute(
        """
        INSERT INTO tenant_profile
            (tenant_id, currency, ui_language, timezone, number_format,
             date_format, time_format, session_token_enc, session_token_at, synced_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            currency = VALUES(currency), ui_language = VALUES(ui_language),
            timezone = VALUES(timezone), number_format = VALUES(number_format),
            date_format = VALUES(date_format), time_format = VALUES(time_format),
            session_token_enc = COALESCE(VALUES(session_token_enc), session_token_enc),
            session_token_at  = COALESCE(VALUES(session_token_at), session_token_at),
            synced_at = VALUES(synced_at)
        """,
        (tenant_id, mapped["currency"], mapped["ui_language"], mapped["timezone"],
         mapped["number_format"], mapped["date_format"], mapped["time_format"],
         token_enc, now if token_enc else None, now),
    )
    cursor.execute("DELETE FROM tenant_shop WHERE tenant_id = %s", (tenant_id,))
    if mapped["shops"]:
        cursor.executemany(
            "INSERT INTO tenant_shop (tenant_id, shop_id, shop_name, is_enabled) "
            "VALUES (%s, %s, %s, %s)",
            [(tenant_id, s["shop_id"], s["shop_name"], s["is_enabled"])
             for s in mapped["shops"]],
        )
    cursor.execute("DELETE FROM tenant_cashier WHERE tenant_id = %s", (tenant_id,))
    if mapped["cashiers"]:
        cursor.executemany(
            "INSERT INTO tenant_cashier (tenant_id, user_name, cashier_id, cashier_name) "
            "VALUES (%s, %s, %s, %s)",
            [(tenant_id, c["user_name"], c["cashier_id"], c["cashier_name"])
             for c in mapped["cashiers"]],
        )


def sync_tenant_profile(tenant_id: str, access_token: str) -> dict:
    """Fetch /app/profile with the live session token, persist it, and stash
    the (encrypted) token for later chat-time report fetches. Returns the
    mapped profile."""
    raw = ReportAPIClient(access_token).fetch_profile()
    mapped = map_profile(raw)
    conn = get_internal_conn()
    try:
        save_profile(conn, tenant_id, mapped, access_token)
        conn.commit()
    finally:
        conn.close()
    log.info("Tenant profile synced", tenant=tenant_id,
             shops=len(mapped["shops"]), cashiers=len(mapped["cashiers"]))
    return mapped


def get_session_token(tenant_id: str, max_age_hours: int = 12):
    """The tenant's stashed live-session `aat`, or None if absent/older than
    max_age_hours (expired tokens just 401 anyway — the age cut avoids
    pointless calls with a token from days ago)."""
    conn = get_internal_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT session_token_enc, session_token_at FROM tenant_profile "
            "WHERE tenant_id = %s", (tenant_id,),
        )
        row = cursor.fetchone()
    finally:
        conn.close()
    if not row or not row["session_token_enc"] or not row["session_token_at"]:
        return None
    age = datetime.utcnow() - row["session_token_at"]
    if age.total_seconds() > max_age_hours * 3600:
        return None
    try:
        return _decrypt(row["session_token_enc"]).get("aat")
    except Exception:
        return None
