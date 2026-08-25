"""
embed.py
Partner embed integration for DataMind AI.

Provides:
  - embed_partners table bootstrap (partner key registry)
  - GET  /embed/context?pk=...  — validate partner key, return context
  - POST /embed/init            — one-shot new-user onboarding (create account
                                  + connect Salesplay + start trial + return JWT)

All existing endpoints (/auth, /query, /providers/*, /billing/*) are reused
unchanged by the iframe after init. This module only handles the embed-specific
bootstrap flow.
"""

import os
import re
import json
import time
import threading
import collections
from typing import Optional

import mysql.connector
import requests as _http
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from logger import get_logger
from auth import create_user, authenticate_user, create_token, resolve_account_key, update_user_settings, current_user, optional_current_user, _column_exists
from billing import start_trial, subscribe_to_plan, cancel_subscription, SUBSCRIPTION_FREE
from integrations import connect_provider, connect_integration
from pool import get_internal_conn as _get_conn
import partner_api

log = get_logger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])

# ── Report-cache profile sync (feature-flagged, always non-fatal) ─────────────
_REPORT_CACHE_ENABLED = os.getenv("REPORT_CACHE_ENABLED", "false").strip().lower() in ("1", "true", "yes")


def _sync_report_profile(table_prefix: Optional[str], email: str, aat: str) -> None:
    """Sync the tenant's SalesPlay profile (shops/cashiers/currency), stash
    the live session token for chat-time report fetches, and kick the
    plan-window report backfill on a background thread. Runs on every widget
    open — the only moment a working /app/* token exists. Never fatal:
    onboarding proceeds unchanged on any failure. Backfill is idempotent, so
    repeat widget opens only fetch months that aren't cached yet."""
    if not _REPORT_CACHE_ENABLED or not table_prefix:
        return
    try:
        from report_cache.profile import sync_tenant_profile
        sync_tenant_profile(table_prefix, aat)
    except Exception as e:
        log.warning("Report cache: profile sync skipped", email=email, error=str(e))
        return
    try:
        from billing import get_plan_history_limit
        from report_cache.ingest import start_backfill_async
        # No `or 3` — get_plan_history_limit owns the one logged fallback. A
        # second one here silently downgraded a Pro tenant to Starter depth.
        months = get_plan_history_limit(email)["months"]
        start_backfill_async(table_prefix, aat, months)
    except Exception as e:
        log.warning("Report cache: backfill kick skipped", email=email, error=str(e))

# ── Simple in-memory rate limiter (no external deps) ─────────────────────────
# Tracks per-IP request timestamps for /embed/init (5 requests/minute max).
_rate_store: dict = collections.defaultdict(list)
_RATE_LIMIT   = int(os.getenv("SALESPLAY_EMBED_RATE_LIMIT", "5"))
_RATE_WINDOW  = int(os.getenv("SALESPLAY_EMBED_RATE_WINDOW", "60"))

def _client_ip(request: "Request") -> str:
    """
    Extract the real client IP, preferring X-Forwarded-For so the rate limiter
    works correctly behind nginx / ALB / CloudFront.
    Falls back to request.client.host, then 'unknown'.
    Strips the port number so IPv4:port and bare IPv4 normalise to the same key.
    """
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        host = request.client.host
        # Strip port (e.g. "127.0.0.1:51234" → "127.0.0.1")
        return host.rsplit(":", 1)[0] if ":" in host and not host.startswith("[") else host
    return "unknown"


def _check_rate(ip: str):
    now = time.time()
    calls = _rate_store[ip]
    # Purge timestamps outside the window
    _rate_store[ip] = [t for t in calls if now - t < _RATE_WINDOW]
    if len(_rate_store[ip]) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
    _rate_store[ip].append(now)


# ── Bootstrap ─────────────────────────────────────────────────────────────────

# ── Brand resolution ──────────────────────────────────────────────
# A brand is one embed_partners row. Many brands can share one provider_id:
# Salesplay, Sellmo and any future whitelabel all run provider_id='salesplay'.
# Branding lives in the DB, so adding a brand is a row, not a deploy.
#
# Every default here is deliberately brand-neutral. Nothing falls back to a
# brand name, because a fallback naming one brand would leak it into another
# brand's widget. Salesplay carries its own values in its own row.
_BRAND_DEFAULTS = {
    "product_name":      None,   # falls back to partner_name
    "company_name":      None,   # the host system's own name, for body copy
    "brand_slug":        None,   # short label for domain mapping and logs
    "logo_url":          None,
    "logo_mark_url":     None,   # small square mark for the assistant avatar
    "favicon_url":       None,
    "app_url":           None,   # this brand's standalone app
    "app_domains":       [],     # hostnames that resolve to this brand
    "terms_url":         None,
    "privacy_url":       None,
    "support_email":     None,
    "primary_color":     "#0058BE",
    "colors":            {},
    "show_beta_badge":   False,
    "welcome_message":   None,
    "suggestions":       [],
    "subscription_free": None,   # None means inherit the process-wide default
}


def _brand(partner: dict) -> dict:
    """Resolve a partner row's branding JSON over the neutral defaults.

    One place, so /embed/context, the LLM persona and the billing gate all see
    the same values.
    """
    raw = partner.get("branding") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    brand = dict(_BRAND_DEFAULTS)
    brand.update({k: v for k, v in raw.items() if v is not None})
    # partner_name is the only name guaranteed to exist, so it backstops both.
    brand["product_name"] = brand["product_name"] or partner.get("partner_name")
    brand["company_name"] = brand["company_name"] or partner.get("partner_name")
    return brand


def brand_subscription_free(partner) -> bool:
    """Whether this brand is in free mode.

    Per-brand, because a new whitelabel usually wants a free launch period
    while an established brand is already charging. Falls back to the
    process-wide env flag when a brand does not override it.
    """
    if partner:
        value = _brand(partner).get("subscription_free")
        if value is not None:
            return bool(value)
    return SUBSCRIPTION_FREE


def bootstrap_embed_tables():
    """Create embed_partners table. Safe to call on every startup."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS embed_partners (
            partner_key     VARCHAR(64) PRIMARY KEY,
            partner_name    VARCHAR(128) NOT NULL,
            provider_id     VARCHAR(50)  NOT NULL,
            allowed_origins TEXT         NOT NULL,
            branding        JSON,
            api_config      JSON,
            active          TINYINT(1)   DEFAULT 1,
            created_at      DATETIME     DEFAULT NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Existing installs predate api_config. Kept out of `branding` on purpose:
    # branding is serialised to the widget, and these are the provider hosts we
    # promised not to expose.
    if not _column_exists(cursor, "embed_partners", "api_config"):
        cursor.execute("ALTER TABLE embed_partners ADD COLUMN api_config JSON AFTER branding")
        log.info("Embed: added embed_partners.api_config")
    conn.commit()
    cursor.close()
    conn.close()
    log.info("Embed tables bootstrapped")


# ── Merchant-facing copy ──────────────────────────────────────────
# These strings reach the merchant's error box AND the response body, so a
# whitelabel merchant must never see the integration's brand in them. Every
# handler that raises one has already resolved its partner row.
ERR_SESSION_EXPIRED = "{company} session expired. Please refresh the page."
ERR_UNREACHABLE     = "Could not reach {company}. Please try again."
ERR_WRONG_PARTNER   = "This endpoint is not available for this partner."
ERR_NO_TOKEN        = "{company} did not return an API token."
ERR_NO_EMAIL        = "Could not retrieve email from your {company} profile."
ERR_TOKEN_CREATE    = "Could not create a {company} API token. Please try again."
ERR_TOKEN_INVALID   = "Invalid {company} API token."


def _msg(partner, template: str) -> str:
    """Fill merchant-facing copy with the caller's own brand name."""
    company = _brand(partner)["company_name"] if partner else "your provider"
    return template.format(company=company)


# ── Partner lookup ────────────────────────────────────────────────

# Short TTL rather than lru_cache: _get_partner is now on the chat path, but
# setting active=0 must still take effect without a restart.
_PARTNER_CACHE_TTL = int(os.getenv("PARTNER_CACHE_TTL", "60"))
_partner_cache: dict = {}
_partner_cache_lock = threading.Lock()


def _partner_cache_clear() -> None:
    with _partner_cache_lock:
        _partner_cache.clear()


def _get_partner(partner_key: str):
    """Return partner row dict or None if not found / inactive."""
    now = time.monotonic()
    with _partner_cache_lock:
        hit = _partner_cache.get(partner_key)
        if hit and hit[1] > now:
            return hit[0]

    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM embed_partners WHERE partner_key=%s AND active=1",
            (partner_key,)
        )
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    with _partner_cache_lock:
        _partner_cache[partner_key] = (row, now + _PARTNER_CACHE_TTL)
    return row


def resolve_partner_by_host(host):
    """Map a request Host header to the brand that owns it.

    The embed gets its brand from ?pk= because the iframe runs on the partner's
    own domain. The standalone app gets it from Host instead: ai.sellmo.com is
    Sellmo, ai.salesplay.com is Salesplay. That is what lets a merchant type
    their real email at either one and land in the right account, with no brand
    picker and no change to the login UI.

    A hostname must belong to exactly one brand; add_brand.py enforces that.
    """
    if not host:
        return None
    hostname = host.split(":", 1)[0].strip().lower()
    if not hostname:
        return None

    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM embed_partners WHERE active=1")
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    for row in rows:
        for domain in _brand(row).get("app_domains") or []:
            if str(domain).strip().lower() == hostname:
                return row
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/context")
def get_embed_context(pk: str):
    """
    Called by the iframe on load to validate the partner key.
    Returns partner name, provider_id, branding config, and allowed origins.
    Returns 404 if key is invalid or inactive — iframe shows an error screen.
    """
    partner = _get_partner(pk)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")

    brand = _brand(partner)

    # Parse comma-separated origins for use in postMessage security checks
    raw_origins = partner.get("allowed_origins", "")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    log.info("Embed context requested", partner=partner["partner_name"])
    return {
        "partner_name":    partner["partner_name"],
        # "flow", not "provider_id": the widget only ever asked which layout to
        # render, and provider_id is server-side vocabulary that would put the
        # word "salesplay" in every whitelabel merchant's network tab.
        "flow":            "partner" if partner["provider_id"] == "salesplay" else "generic",
        "partner_key":     pk,
        "app_name":        brand["product_name"],
        "branding":        brand,
        "allowed_origins": allowed_origins,
        # Per-brand launch-period switch, falling back to the process default.
        "subscription_free": brand_subscription_free(partner),
    }


class EmbedValidateTokenRequest(BaseModel):
    partner_key: str
    api_token:   str


@router.post("/validate-token")
def embed_validate_token(request: Request, req: EmbedValidateTokenRequest):
    """
    Validate a provider API token without requiring a DataMind account.
    Called in Step 0 of the onboarding wizard — before the user has created
    an account or received a JWT. The partner_key acts as the only gate.
    Rate-limited to prevent brute-forcing provider tokens and abusing the
    external provider API at our cost.
    """
    _check_rate(_client_ip(request))

    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid partner key.")

    from providers import get_provider
    try:
        provider = get_provider(partner["provider_id"])
        result = provider.validate_credentials({"api_token": req.api_token.strip()})
        log.info("Embed: provider token validated",
                 partner=partner["partner_name"], ok=result.ok)
        return {"ok": result.ok, "error": result.error, "details": result.details}
    except Exception as e:
        log.warning("Embed: provider token validation failed", error=str(e))
        return {"ok": False, "error": "Token validation failed. Check your API key and try again."}


class EmbedInitRequest(BaseModel):
    partner_key: str
    api_token:   str              # Salesplay API token
    name:        str              # Full name for DataMind account
    email:       str
    password:    str = Field(min_length=8)


class SalesplayCheckUserRequest(BaseModel):
    partner_key: str
    email:       str


class SalesplayAutoInitRequest(BaseModel):
    partner_key:          str
    email:                str
    name:                 str
    salesplay_api_token:  Optional[str] = None


@router.post("/init")
def embed_init(request: Request, req: EmbedInitRequest):
    """
    One-shot onboarding for first-time embed users. In a single call:
      1. Validates the partner key
      2. Creates a DataMind account (or re-authenticates if email exists)
      3. Starts the free trial
      4. Connects the provider (validates API token + kicks off background sync)
      5. Returns a JWT token

    The iframe uses this token for all subsequent API calls via the standard
    endpoints (/query, /providers/*, /billing/*, etc.).
    """
    # Rate limit: 5 calls/min per IP (blocks automated account creation abuse)
    _check_rate(_client_ip(request))

    # 1. Validate partner key
    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid partner key.")

    # 2. Create user account (or re-authenticate if already registered)
    # Scoped to this partner: the same address under another brand is a
    # different merchant and must not be matched here.
    email = req.email.strip().lower()
    try:
        account_key = create_user(req.name, email, req.password, partner["partner_key"])["email"]
        log.info("Embed: user created", email=email, partner=partner["partner_name"])
    except HTTPException as e:
        if "already registered" in str(e.detail):
            # Email exists for THIS brand — authenticate with provided password
            # instead. Handles a user who started onboarding but stopped.
            try:
                account_key = authenticate_user(email, req.password, partner["partner_key"])["email"]
                log.info("Embed: existing user re-authenticated", email=email)
            except HTTPException:
                raise HTTPException(
                    status_code=400,
                    detail="An account with this email already exists. "
                           "Please use a different email or log in at datamind.ai."
                )
        else:
            raise

    # 3. Start free trial (silently skip if already active)
    try:
        start_trial(account_key)
    except Exception as _te:
        log.warning("Embed: trial start skipped", email=email, error=str(_te))

    # 4. Connect the provider (validates API token, creates tables, starts sync)
    # connect_provider() raises ValueError on bad credentials, Exception on other failures
    try:
        connect_provider(
            user_email  = account_key,
            provider_id = partner["provider_id"],
            credentials = {"api_token": req.api_token.strip()},
        )
        log.info("Embed: provider connected",
                 email=req.email, provider=partner["provider_id"])
    except ValueError as e:
        log.warning("Embed: provider connect validation error", email=req.email, error=str(e))
        raise HTTPException(status_code=422, detail="Invalid credentials or account details.")
    except Exception as e:
        log.error("Embed: provider connect failed", email=req.email, error=str(e))
        raise HTTPException(status_code=500, detail="Failed to connect provider. Please try again.")

    # 5. Return JWT. The token carries the account key; the body carries the
    # real address, which is what the widget displays.
    token = create_token(account_key)
    return {
        "token":       token,
        "user":        {"name": req.name, "email": email},
        "provider_id": partner["provider_id"],
        "sync":        "started",
    }


# ── Salesplay auto-init endpoints ─────────────────────────────────────────────

class PartnerConnectRequest(BaseModel):
    partner_key: str
    credentials: dict


@router.post("/partner/connect")
def partner_connect(request: Request, req: PartnerConnectRequest,
                    user: dict = Depends(current_user)):
    """Connect the brand's integration for the signed-in merchant.

    The widget used to send provider_id in the body, which put the
    integration's name in a request every whitelabel merchant can read. The
    provider comes from the partner row instead -- the widget never needs to
    know it.
    """
    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    _require_allowed_origin(partner, request)
    try:
        connect_provider(
            user_email  = user["email"],
            provider_id = partner["provider_id"],
            credentials = req.credentials,
        )
    except ValueError:
        raise HTTPException(status_code=422, detail=_msg(partner, ERR_TOKEN_INVALID))
    except Exception as e:
        log.error("Embed: partner connect failed", user=user["email"], error=str(e))
        raise HTTPException(status_code=500, detail="Failed to connect. Please try again.")
    return {"ok": True}


@router.get("/partner/sync-status")
def partner_sync_status(request: Request, partner_key: str,
                        user: dict = Depends(current_user)):
    """Sync progress for the signed-in merchant.

    The widget used to poll /providers/{connection_id}/status, where
    connection_id IS the provider id -- so a whitelabel merchant watched
    "salesplay" scroll past in their network tab on a loop. The provider is
    resolved server-side from the partner row instead.
    """
    partner = _get_partner(partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    _require_allowed_origin(partner, request)
    from integrations import get_connection_status
    try:
        return get_connection_status(user["email"], partner["provider_id"])
    except Exception as e:
        log.error("Embed: sync status failed", user=user["email"], error=str(e))
        raise HTTPException(status_code=500, detail="Failed to get sync status.")


@router.post("/partner/check-user")
def salesplay_check_user(request: Request, req: SalesplayCheckUserRequest):
    """
    Pre-flight check for the Salesplay auto-init flow.
    Returns whether a DataMind account and Salesplay credentials exist for the
    given email. The frontend uses this to decide whether to generate a new
    Salesplay API token before calling /salesplay/auto-init.
    """
    _check_rate(_client_ip(request))

    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail=_msg(partner, ERR_WRONG_PARTNER))

    email = req.email.strip().lower()

    # Scoped to this partner. Matching on the address alone would report another
    # brand's merchant as "existing" and send a real new user straight past
    # onboarding into an account that is not theirs.
    account_key = resolve_account_key(email, partner["partner_key"])

    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        exists = account_key is not None

        has_credentials = False
        credentials_healthy = False
        if exists:
            cursor.execute(
                "SELECT id, status FROM user_integrations "
                "WHERE user_email = %s AND provider_id = 'salesplay' LIMIT 1",
                (account_key,)
            )
            row = cursor.fetchone()
            if row:
                has_credentials = True
                credentials_healthy = row["status"] in ("active", "syncing")

        cursor.close()
    finally:
        conn.close()

    log.info("Salesplay check-user", email=email, exists=exists,
             has_credentials=has_credentials, credentials_healthy=credentials_healthy)
    return {"exists": exists, "has_credentials": has_credentials,
            "credentials_healthy": credentials_healthy}


@router.post("/partner/auto-init")
def salesplay_auto_init(request: Request, req: SalesplayAutoInitRequest):
    """
    One-shot auto-onboarding for Salesplay embed users.
    The widget calls this after fetching the user's Salesplay profile and
    (when needed) creating a Salesplay API token on the user's behalf.

    Flow:
      1. Validate partner key (Salesplay only)
      2. Create DataMind account — or issue JWT directly for existing accounts
      3. Start free trial (non-fatal if already active)
      4. If salesplay_api_token provided: connect provider + trigger sync
      5. Return JWT
    """
    _check_rate(_client_ip(request))

    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail=_msg(partner, ERR_WRONG_PARTNER))

    email = req.email.strip().lower()
    name  = req.name.strip()

    # Derive a deterministic password from the email (e.g. john2@gmail.com → john2@gmail)
    password = email.rsplit(".", 1)[0]

    is_new_user = False
    try:
        account_key = create_user(name, email, password, partner["partner_key"])["email"]
        is_new_user = True
        log.info("Salesplay auto-init: user created", email=email)
    except HTTPException as e:
        if "already registered" in str(e.detail):
            # Existing account — issue token directly; don't verify password because the
            # user may have a different password from a main-app registration.
            account_key = resolve_account_key(email, partner["partner_key"])
            log.info("Salesplay auto-init: existing user", email=email)
        else:
            raise

    # Start free trial (silently skip if already active)
    try:
        start_trial(account_key)
    except Exception as _te:
        log.warning("Salesplay auto-init: trial start skipped", email=email, error=str(_te))

    # Connect provider only when a fresh API token is supplied
    sync = "skipped"
    api_token = (req.salesplay_api_token or "").strip()
    if api_token:
        try:
            connect_provider(
                user_email  = account_key,
                provider_id = "salesplay",
                credentials = {"api_token": api_token},
            )
            sync = "started"
            log.info("Salesplay auto-init: provider connected", email=email)
        except ValueError as e:
            log.warning("Salesplay auto-init: bad API token", email=email, error=str(e))
            raise HTTPException(status_code=422, detail=_msg(partner, ERR_TOKEN_INVALID))
        except Exception as e:
            log.error("Salesplay auto-init: connect failed", email=email, error=str(e))
            raise HTTPException(status_code=500, detail="Failed to connect provider. Please try again.")

    token = create_token(account_key)
    return {
        "token":       token,
        "user":        {"name": name, "email": email},
        "provider_id": "salesplay",
        "is_new_user": is_new_user,
        "sync":        sync,
    }


# ── Salesplay API proxy endpoints ─────────────────────────────────────────────
# The Salesplay API enforces CORS and only allows requests from their own
# backoffice origin. Since our iframe runs at datamind.ai, direct browser
# calls are blocked. These thin server-side proxies forward the request
# using the user's app_access_token — no CORS applies to server-to-server calls.

# The base URLs are per-brand, not per-process: a whitelabel can run on its own
# instance of the provider (see partner_api). Every handler below already holds
# its partner row, so there is nothing to plumb.
_PROXY_TIMEOUT = int(os.getenv("SALESPLAY_EMBED_PROXY_TIMEOUT", "10"))


def _require_allowed_origin(partner: dict, request: "Request") -> None:
    """Refuse a partner key presented from a domain that is not that brand's.

    The key is visible in the iframe src, so without this one brand's key works
    from another brand's page. An empty allowed_origins means unrestricted,
    which keeps dev and every existing row behaving exactly as before.
    """
    raw = (partner.get("allowed_origins") or "").strip()
    if not raw:
        return
    allowed = {o.strip().rstrip("/").lower() for o in raw.split(",") if o.strip()}
    if not allowed:
        return
    origin = request.headers.get("origin") or ""
    if not origin:
        # Some browsers omit Origin on same-site navigations; fall back to the
        # Referer's scheme+host before refusing.
        referer = request.headers.get("referer") or ""
        parts = referer.split("/")
        if len(parts) >= 3:
            origin = parts[0] + "//" + parts[2]
    if not origin:
        return
    if origin.rstrip("/").lower() not in allowed:
        log.warning("Embed: partner key used from an unlisted origin",
                    partner=partner["partner_name"], origin=origin)
        raise HTTPException(
            status_code=403,
            detail="This page is not authorised to use this widget.",
        )


def _salesplay_guard(partner_key: str, request: "Request"):
    """Validate partner key is active Salesplay, apply rate limit. Returns partner row."""
    _check_rate(_client_ip(request))
    partner = _get_partner(partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    _require_allowed_origin(partner, request)
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail=_msg(partner, ERR_WRONG_PARTNER))
    return partner


class SalesplayProfileRequest(BaseModel):
    partner_key: str
    aat:         str   # Salesplay app_access_token


class SalesplayCreateTokenRequest(BaseModel):
    partner_key: str
    aat:         str


def _scrub_brand(text: str, partner) -> str:
    """Replace the integration's name in text we did not write.

    The passthrough below deliberately hands the merchant the provider's own
    wording so they can quote it to support. But a whitelabel merchant must not
    see the integration's brand, and a raw fault string never passes through
    the provider's own branding layer on the way here.
    """
    if not text or not partner:
        return text
    company = _brand(partner)["company_name"]
    return re.sub(r"sales\s*play(pos)?", company, text, flags=re.IGNORECASE)


def _salesplay_error(resp, fallback: str, partner=None) -> str:
    """Salesplay's own error text for a failed response, verbatim.

    The embed shows the merchant whatever Salesplay actually said (e.g.
    "Payment requires additional authentication...", or a raw PHP fault like
    "Undefined variable $systemCheckingErrorStatus") instead of a generic
    line of ours — a merchant reporting a problem then quotes something
    Salesplay's own support can act on. `fallback` is only used when the body
    carries nothing usable (empty body, HTML error page, network failure).
    """
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        # Guard against an HTML error page landing in the widget's error box.
        return _scrub_brand(text[:300], partner) if text and not text.lstrip().startswith("<") else fallback

    if isinstance(body, dict):
        err = body.get("error")
        candidates = [body.get("message")]
        if isinstance(err, dict):
            candidates += [err.get("message"), err.get("code")]
        elif isinstance(err, str):
            candidates.append(err)
        for c in candidates:
            if isinstance(c, str) and c.strip():
                return _scrub_brand(c.strip()[:300], partner)
    return fallback


def _extract_salesplay_locale(data: dict) -> dict:
    """Extract locale fields from a Salesplay /profile response dict."""
    raw    = data.get("user") or data.get("data") or data
    nf_raw = raw.get("number_format") or {}
    return {
        "currency":           (raw.get("currency") or "$").strip(),
        "country":            (raw.get("country") or "").strip(),
        "country_code":       ((data.get("access_info") or {}).get("country_code") or "").strip(),
        "timezone":           (raw.get("timezone") or "UTC").strip(),
        "ui_language":        (raw.get("ui_language") or "en_US").strip(),
        "number_format": {
            "decimals":           int(nf_raw.get("number_of_decimel") or 2),  # Salesplay typo
            "decimal_separator":  (nf_raw.get("decimal_separator") or "."),
            "thousand_separator": (nf_raw.get("thousond_separator") or ","),  # Salesplay typo
        },
    }


@router.post("/partner/profile")
def salesplay_proxy_profile(request: Request, req: SalesplayProfileRequest):
    """
    Proxy: fetch the authenticated user's Salesplay profile.
    Forwards the app_access_token server-to-server to avoid browser CORS restrictions.
    Returns { email, name }.
    """
    partner = _salesplay_guard(req.partner_key, request)

    try:
        resp = _http.get(
            f'{partner_api.for_partner(partner)["proxy_base"]}/profile',
            headers={"Authorization": f"Bearer {req.aat.strip()}"},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        if not resp.ok:
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, _msg(partner, ERR_UNREACHABLE), partner))
        data = resp.json()
        # Salesplay profile response: { "status": "success", "user": { "email": ..., "full_name": ... } }
        raw  = data.get("user") or data.get("data") or data
        email = (raw.get("email") or "").strip().lower()
        name  = (
            raw.get("full_name") or raw.get("name") or
            raw.get("business_name") or email.split("@")[0]
        ).strip()
        if not email:
            raise HTTPException(status_code=422, detail=_msg(partner, ERR_NO_EMAIL))
        locale = _extract_salesplay_locale(data)
        log.info("Salesplay proxy: profile fetched", email=email)
        return {"email": email, "name": name, "locale": locale}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: profile fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_UNREACHABLE))


@router.post("/partner/create-token")
def salesplay_proxy_create_token(request: Request, req: SalesplayCreateTokenRequest):
    """
    Proxy: create a DataMind integration access token in the user's Salesplay account.
    Forwards the app_access_token server-to-server to avoid browser CORS restrictions.
    Returns { token } — the Salesplay API token stored in user_integrations.
    """
    partner = _salesplay_guard(req.partner_key, request)

    try:
        resp = _http.post(
            f'{partner_api.for_partner(partner)["proxy_base"]}/integrations/access_tokens',
            headers={
                "Authorization":  f"Bearer {req.aat.strip()}",
                "Content-Type":   "application/json",
            },
            json={"name": "DataMind", "expire_enabled": False, "expires_at": ""},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        if not resp.ok:
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, _msg(partner, ERR_TOKEN_CREATE), partner))
        data  = resp.json()
        token = (data.get("data") or {}).get("token")
        if not token:
            raise HTTPException(status_code=502, detail=_msg(partner, ERR_NO_TOKEN))
        log.info("Salesplay proxy: access token created")
        return {"token": token}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: create-token failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_TOKEN_CREATE))


# ── Single onboarding endpoint (widget calls this once after consent) ─────────

class SalesplayOnboardRequest(BaseModel):
    partner_key: str
    aat:         str


@router.post("/partner/onboard")
def salesplay_onboard(request: Request, req: SalesplayOnboardRequest):
    """
    All-in-one Salesplay onboarding. The widget calls this once after the user
    accepts the consent screen. The backend handles everything:
      1. Fetch user profile from Salesplay (server-to-server, no CORS)
      2. Check if a DataMind account + credentials already exist
      3. If no credentials: create a Salesplay API token (server-to-server)
      4. Create DataMind account if new, or issue JWT for existing account
      5. Connect provider, trigger sync
      6. Return JWT + sync status

    Does NOT start a trial/subscription — that only happens when the user
    explicitly picks "Start free trial" on the plans screen (POST
    /embed/salesplay/start-trial), so reopening the widget never silently
    grants access nobody asked for.
    """
    _check_rate(_client_ip(request))

    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail=_msg(partner, ERR_WRONG_PARTNER))

    aat = req.aat.strip()

    # ── 1. Fetch Salesplay user profile ───────────────────────────────────────
    try:
        resp = _http.get(
            f'{partner_api.for_partner(partner)["proxy_base"]}/profile',
            headers={"Authorization": f"Bearer {aat}"},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        if not resp.ok:
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, _msg(partner, ERR_UNREACHABLE), partner))
        data  = resp.json()
        raw   = data.get("user") or data.get("data") or data
        email = (raw.get("email") or "").strip().lower()
        name  = (
            raw.get("full_name") or raw.get("name") or
            raw.get("business_name") or email.split("@")[0]
        ).strip()
        if not email:
            raise HTTPException(status_code=422, detail=_msg(partner, ERR_NO_EMAIL))
        locale = _extract_salesplay_locale(data)
        log.info("Salesplay onboard: profile fetched", email=email)
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay onboard: profile fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_UNREACHABLE))

    # ── 2. Check if DataMind account + credentials already exist ──────────────
    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        account_key = resolve_account_key(email, partner["partner_key"])
        user_exists = account_key is not None
        has_credentials = False
        credentials_healthy = False
        if user_exists:
            cursor.execute(
                "SELECT id, status FROM user_integrations "
                "WHERE user_email = %s AND provider_id = 'salesplay' LIMIT 1",
                (account_key,)
            )
            row = cursor.fetchone()
            if row:
                has_credentials = True
                credentials_healthy = row["status"] in ("active", "syncing")
        cursor.close()
    finally:
        conn.close()

    log.info("Salesplay onboard: credential check",
             email=email, has_credentials=has_credentials,
             credentials_healthy=credentials_healthy)

    # ── 3. Create Salesplay API token if needed ───────────────────────────────
    # Create a new token when:
    #   A) No credentials exist yet (first-time user)
    #   B) Credentials exist but the last sync failed (status='error') — the stored
    #      token may be invalid. The AAT is always fresh from the current Salesplay
    #      session, so it is safe to use it to get a new external token here.
    salesplay_api_token = None
    if not has_credentials or not credentials_healthy:
        try:
            resp = _http.post(
                f'{partner_api.for_partner(partner)["proxy_base"]}/integrations/access_tokens',
                headers={
                    "Authorization": f"Bearer {aat}",
                    "Content-Type":  "application/json",
                },
                json={"name": "DataMind", "expire_enabled": False, "expires_at": ""},
                timeout=_PROXY_TIMEOUT,
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
            if not resp.ok:
                raise HTTPException(status_code=502, detail=_salesplay_error(resp, _msg(partner, ERR_TOKEN_CREATE), partner))
            token_data        = resp.json()
            salesplay_api_token = (token_data.get("data") or {}).get("token")
            if not salesplay_api_token:
                raise HTTPException(status_code=502, detail=_msg(partner, ERR_NO_TOKEN))
            log.info("Salesplay onboard: API token created", email=email)
        except HTTPException:
            raise
        except Exception as e:
            log.error("Salesplay onboard: create-token failed", error=str(e))
            raise HTTPException(status_code=502, detail=_msg(partner, ERR_TOKEN_CREATE))

    # ── 4. Create DataMind account (or reuse existing) ────────────────────────
    password    = email.rsplit(".", 1)[0]
    is_new_user = False
    try:
        account_key = create_user(name, email, password, partner["partner_key"])["email"]
        is_new_user = True
        log.info("Salesplay onboard: user created", email=email)
    except HTTPException as e:
        if "already registered" in str(e.detail):
            account_key = resolve_account_key(email, partner["partner_key"])
            log.info("Salesplay onboard: existing user", email=email)
        else:
            raise

    # ── 5b. Persist locale from Salesplay profile (non-fatal) ─────────────────
    try:
        update_user_settings(account_key, {"locale": locale})
        log.info("Salesplay onboard: locale saved", email=email, currency=locale.get("currency"))
    except Exception as _le:
        log.warning("Salesplay onboard: locale save skipped", email=email, error=str(_le))

    # ── 6. Connect provider + trigger sync ───────────────────────────────────
    # Case A: fresh/broken credentials — store new token and kick off a full sync.
    # Case B: healthy existing credentials — just trigger a delta sync to pick up
    #         any new Salesplay data since the last sync (happens on every widget open).
    # skip_validation=True: token was created directly from Salesplay's own API,
    # so it is guaranteed valid without needing a separate /merchant round-trip.
    sync = "skipped"
    table_prefix = None
    if salesplay_api_token:
        try:
            result = connect_integration(
                user_email       = account_key,
                provider_id      = "salesplay",
                creds            = {"api_token": salesplay_api_token},
                skip_validation  = True,
            )
            table_prefix = (result or {}).get("table_prefix")
            sync = "started"
            log.info("Salesplay onboard: provider connected with new token", email=email)
        except Exception as e:
            log.error("Salesplay onboard: connect failed", email=email, error=str(e))
            raise HTTPException(status_code=500, detail="Failed to connect provider. Please try again.")
    elif has_credentials and credentials_healthy:
        # Returning user with a healthy integration — trigger a delta sync so they
        # see data from any new Salesplay transactions since their last sync.
        try:
            from integrations import trigger_sync, get_integration
            trigger_sync(account_key, "salesplay", full=False)
            sync = "delta_started"
            integ = get_integration(account_key, "salesplay")
            table_prefix = (integ or {}).get("table_prefix")
            log.info("Salesplay onboard: delta sync triggered for returning user", email=email)
        except Exception as e:
            log.warning("Salesplay onboard: delta sync trigger failed (non-fatal)",
                        email=email, error=str(e))
            sync = "skipped"

    # Report cache: refresh profile (shops/cashiers/currency) + stash the live
    # session token for chat-time report fetches. Feature-flagged, non-fatal.
    _sync_report_profile(table_prefix, account_key, aat)

    token = create_token(account_key)
    log.info("Salesplay onboard: complete", email=email, is_new_user=is_new_user, sync=sync)
    return {
        "token":       token,
        "user":        {"name": name, "email": email, "locale": locale},
        "provider_id": "salesplay",
        "is_new_user": is_new_user,
        "sync":        sync,
    }


# ── Salesplay AI POS subscription proxy ────────────────────────────────────────
# The "AI POS" addon is billed and tracked entirely inside Salesplay's own
# subscription system (trial window, quota, plans, payment) — not DataMind's
# internal billing. These are thin server-to-server proxies (same reasoning
# as the profile/create-token proxies above: Salesplay's API only allows
# requests from their own origin, so the browser can't call it directly).


def _reject_if_subscription_free(partner=None):
    """Refuse to take money while this brand is in free mode.

    Per-brand: a new whitelabel launching free must not be able to charge,
    while an established brand on the same deployment keeps selling normally.
    Falls back to the process-wide flag when a brand does not override it.

    The widget already hides every route to these endpoints in free mode, so
    reaching them means a stale iframe that was loaded before the flag went
    on, or a direct call. Either way the merchant's card must not be charged
    during a period we advertised as free -- a wrong charge is far more
    expensive to undo than a failed request.
    """
    if brand_subscription_free(partner):
        raise HTTPException(
            status_code=403,
            detail="Subscriptions are free right now -- there is nothing to pay for. "
                   "Please reload the page.",
        )



@router.get("/partner/subscription/info")
def salesplay_subscription_info(request: Request, partner_key: str, aat: str, user: Optional[dict] = Depends(optional_current_user)):
    """
    Proxy: GET {base}/subscriptions/get_ai_pos_info
    Returns Salesplay's raw state unchanged (frontend reads plans/card info
    from it), but access itself is decided from DataMind's OWN billing
    (trial days, tokens — see GET /billing/subscription) — Salesplay is the
    payment gateway, not the source of truth for what the user can do here.
    The one thing we do sync down from Salesplay: is_expired=1, or
    subscribe_status=0, means their subscription is no longer valid (failed
    renewal, refund, chargeback, cancelled) — cancel our internal one
    immediately regardless of our own period_end. activation_status is NOT
    checked (Salesplay-confirmed: was laggy after payment, no longer relied
    on for anything).

    user is optional: the consent screen's "Explore plans" toggle
    (EmbedSalesplayAutoInit.jsx) calls this with only partner_key+aat, before
    any DataMind account/dm_embed_token exists, purely to preview pricing —
    requiring current_user here made that call 401 unconditionally. The
    sync-down below only runs when a signed-in user is actually present.
    """
    partner = _salesplay_guard(partner_key, request)
    url = f'{partner_api.for_partner(partner)["subscription_base"]}/subscriptions/get_ai_pos_info'
    log.debug("Salesplay subscription API request", method="GET", url=url)
    try:
        resp = _http.get(
            url,
            headers={"Authorization": f"Bearer {aat.strip()}"},
            timeout=_PROXY_TIMEOUT,
        )
        log.debug("Salesplay subscription API response", url=url, status=resp.status_code, raw_body=resp.text)
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        if not resp.ok:
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, _msg(partner, ERR_UNREACHABLE), partner))
        data = resp.json()
        sub = (data.get("data") or {}).get("subscription") or []
        if user and sub and (sub[0].get("is_expired") == 1 or sub[0].get("subscribe_status") == 0):
            try:
                cancel_subscription(user["email"])
            except Exception as e:
                log.warning("Internal subscription sync-down failed (non-fatal)", email=user["email"], error=str(e))
        return data
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: subscription info fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_UNREACHABLE))


class SalesplaySubscriptionPaymentRequest(BaseModel):
    partner_key: str
    aat: str
    subscription_type: str
    subscription_product_code: str
    # Object, not a string — confirmed against predev2: Salesplay's endpoint
    # 500s on a JSON string ("must be an array") and on a missing/null value
    # (PHP sizeof() on null). Must always be sent, even empty.
    activation_value_data: dict = Field(default_factory=dict)
    product_type: Optional[str] = None
    subscription_activation_type: Optional[str] = None
    activation_renewal_auto_job_time: Optional[str] = None
    coupon_code_verified: Optional[bool] = None
    coupon_code: Optional[str] = None
    # Confirmed working against predev2 by the Salesplay team directly: "" not
    # "AUTH_NOT_REQUIRED" — the latter caused "No activations available to
    # subscribe" on every attempt.
    payment_action: str = ""
    auth_payment_intent_id: str = ""
    # How many days this purchase covers (30 monthly, 365 yearly). This one is
    # genuinely the frontend's to decide — it is the billing cycle the merchant
    # picked. We don't wait on Salesplay's activation_status to flip before
    # granting access — that lag is unbounded (confirmed: still 0 after 2.5+
    # minutes on predev2). The money already moved the instant Salesplay
    # returned "success", so we activate our own side immediately.
    internal_period_days: int
    # DEPRECATED and ignored. The browser used to name the plan to activate,
    # mapping tier position to a hardcoded subscription_plans.id — which is an
    # id it cannot know and that goes stale on any reseed. The plan is now
    # resolved server-side by name. Still accepted so an iframe cached from
    # before this change completes its charge instead of failing validation.
    internal_plan_id: Optional[int] = None


@router.post("/partner/subscription/payment")
def salesplay_subscription_payment(request: Request, req: SalesplaySubscriptionPaymentRequest, user: dict = Depends(current_user)):
    """
    Proxy: POST {base}/subscriptions/payment
    Forwards Salesplay's response unchanged — but on success ALSO activates
    the matching DataMind plan immediately (subscribe_to_plan), synchronously,
    in this same request. This is what actually grants chat access; Salesplay
    is the payment gateway, not the access gate. On error, Salesplay's
    response carries a redirect link the frontend must send the user to.
    """
    partner = _salesplay_guard(req.partner_key, request)
    _reject_if_subscription_free(partner)
    body = req.dict(exclude={"partner_key", "aat", "internal_plan_id", "internal_period_days"}, exclude_none=True)
    url = f'{partner_api.for_partner(partner)["subscription_base"]}/subscriptions/payment'
    log.debug("Salesplay subscription API request", method="POST", url=url, body=body)
    try:
        resp = _http.post(
            url,
            headers={
                "Authorization": f"Bearer {req.aat.strip()}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=_PROXY_TIMEOUT,
        )
        log.debug("Salesplay subscription API response", url=url, status=resp.status_code, raw_body=resp.text)
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        try:
            result = resp.json()
        except ValueError:
            # Non-JSON body — a raw PHP fatal or HTML error page from Salesplay.
            # Surface what they actually said; it's the only diagnostic anyone has.
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, "Payment could not be completed. Please try again.", partner))
        if result.get("status") == "success":
            try:
                subscribe_to_plan(user["email"], period_days=req.internal_period_days)
            except Exception as e:
                # The charge already succeeded on Salesplay's side — a failure here
                # is a real inconsistency (paid but not activated), not something to
                # silently swallow. Log loudly; still return the payment success so
                # the frontend doesn't tell the user their card was charged for nothing.
                log.error("Internal plan activation failed after successful Salesplay charge",
                          email=user["email"], period_days=req.internal_period_days, error=str(e))
        return result
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: subscription payment failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_UNREACHABLE))


class SalesplaySubscriptionPreviewRequest(BaseModel):
    partner_key: str
    aat: str
    subscription_type: int
    product_code: str
    activation_value_data: list = Field(default_factory=list)
    product_type: Optional[str] = None
    coupon_code_verified: int = 0
    coupon_code: str = ""


@router.post("/partner/subscription/preview")
def salesplay_subscription_preview(request: Request, req: SalesplaySubscriptionPreviewRequest, user: dict = Depends(current_user)):
    """
    Proxy: POST {base}/subscriptions/order/preview
    Returns Salesplay's real, already-formatted pricing (product_price ×
    qty, credits, amount due) for the receipt screen — forwarded verbatim.
    The frontend must never recompute or reformat these currency strings
    itself; this endpoint is the only source for what gets shown/charged.
    """
    partner = _salesplay_guard(req.partner_key, request)
    _reject_if_subscription_free(partner)
    body = req.dict(exclude={"partner_key", "aat"}, exclude_none=True)
    url = f'{partner_api.for_partner(partner)["subscription_base"]}/subscriptions/order/preview'
    log.debug("Salesplay subscription API request", method="POST", url=url, body=body)
    try:
        resp = _http.post(
            url,
            headers={
                "Authorization": f"Bearer {req.aat.strip()}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=_PROXY_TIMEOUT,
        )
        log.debug("Salesplay subscription API response", url=url, status=resp.status_code, raw_body=resp.text)
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail=_msg(partner, ERR_SESSION_EXPIRED))
        try:
            return resp.json()
        except ValueError:
            raise HTTPException(status_code=502, detail=_salesplay_error(resp, "Could not load order preview. Please try again.", partner))
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: subscription preview fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail=_msg(partner, ERR_UNREACHABLE))


@router.post("/partner/start-trial")
def salesplay_start_trial(user: dict = Depends(current_user)):
    """Explicitly start the free trial — only called from the plans screen's
    "Start free trial" button. Never implicit at onboarding, so reopening the
    widget never silently grants a trial nobody asked for."""
    start_trial(user["email"])
    return {"ok": True}
