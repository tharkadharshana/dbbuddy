"""
partner_api.py — which provider instance a brand talks to.

A whitelabel does not necessarily share its parent's provider backend.
Salesplay runs on predev2, its whitelabel Sellmo on predev1, and in production
each partner has its own backoffice host. So the provider API base URLs cannot
be process-global env vars the way they were: one deployment has to serve both
at once, keyed on the brand.

These URLs are internal. They are never returned to the browser and never
appear in a client-facing response — exposing the provider's hosts is exactly
what the integration asked us not to do. That is why they live in their own
`api_config` column rather than in `branding`, which /embed/context serialises
straight to the widget.

Kept in its own module because report_cache/ and providers/ both need it and
both are imported by embed.py.

Env stays the fallback for every key, so a single-brand deployment with no
api_config set behaves exactly as it did before.
"""

import os
import time
import threading

from logger import get_logger
from pool import get_internal_conn as _get_conn

log = get_logger(__name__)

# Same TTL as the partner row cache: long enough to keep this off the hot path,
# short enough that changing a URL in the DB does not need a restart.
_TTL = int(os.getenv("PARTNER_CACHE_TTL", "60"))

_cache: dict = {}
_lock = threading.Lock()


def cache_clear() -> None:
    with _lock:
        _cache.clear()


def _defaults() -> dict:
    """Read env at call time, not import time, so a test can monkeypatch it."""
    proxy = os.getenv("SALESPLAY_EMBED_PROXY_BASE",
                      "https://api.salesplaypos.com/v2.0/public/app")
    return {
        # External data-sync API (v1.0) — providers/salesplay/sync.py
        "sync_base": os.getenv("SALESPLAY_BASE_URL",
                               "https://api.salesplaypos.com/v1.0"),
        # Internal backoffice app API — embed proxies + report_cache
        "proxy_base": proxy,
        # Subscription endpoints; historically defaulted to the proxy base.
        "subscription_base": os.getenv("SALESPLAY_SUBSCRIPTION_BASE_URL", proxy),
    }


def for_partner(partner) -> dict:
    """Resolve one partner row's API bases over the env defaults.

    `partner` is a row dict from embed_partners (its api_config may be a JSON
    string or an already-decoded dict, depending on the connector), or None.
    """
    out = _defaults()
    raw = (partner or {}).get("api_config") or {}
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            log.warning("Partner api_config is not valid JSON — using env defaults",
                        partner=(partner or {}).get("partner_name"))
            raw = {}
    for key in out:
        value = (raw.get(key) or "").strip() if isinstance(raw.get(key), str) else raw.get(key)
        if value:
            out[key] = value.rstrip("/")
    return out


def _lookup(sql: str, param) -> dict:
    """Run a one-column partner_key lookup and resolve that partner's bases."""
    key = (sql, param)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > now:
            return hit[0]

    row = None
    try:
        conn = _get_conn()
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql, (param,))
            row = cursor.fetchone()
            cursor.close()
        finally:
            conn.close()
    except Exception as exc:
        # Never fail a sync or a report fetch because this lookup broke — the
        # env default is what the whole system used before this existed.
        log.warning("Partner API lookup failed — using env defaults", error=str(exc))

    out = for_partner(row)
    with _lock:
        _cache[key] = (out, now + _TTL)
    return out


def for_user(user_email: str) -> dict:
    """API bases for the brand that owns this account (an account_key)."""
    if not user_email:
        return _defaults()
    return _lookup(
        "SELECT p.api_config, p.partner_name FROM users u "
        "JOIN embed_partners p ON p.partner_key = u.partner_key "
        "WHERE u.account_key = %s",
        user_email,
    )


def for_tenant(tenant_id: str) -> dict:
    """API bases for the brand behind a tenant_id.

    report_cache only ever carries tenant_id, and tenant_id is the stored
    user_integrations.table_prefix — so this is the one hop back to the brand.
    """
    if not tenant_id:
        return _defaults()
    return _lookup(
        "SELECT p.api_config, p.partner_name FROM user_integrations i "
        "JOIN users u ON u.account_key = i.user_email "
        "JOIN embed_partners p ON p.partner_key = u.partner_key "
        "WHERE i.table_prefix = %s",
        tenant_id,
    )
