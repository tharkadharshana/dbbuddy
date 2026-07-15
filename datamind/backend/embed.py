"""
embed.py
========
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
import json
import time
import collections
from typing import Optional

import mysql.connector
import requests as _http
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from logger import get_logger
from auth import create_user, authenticate_user, create_token, update_user_settings
from billing import start_trial
from integrations import connect_provider, connect_integration
from pool import get_internal_conn as _get_conn

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
        months = get_plan_history_limit(email).get("months") or 3
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
            active          TINYINT(1)   DEFAULT 1,
            created_at      DATETIME     DEFAULT NOW()
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    conn.commit()
    cursor.close()
    conn.close()
    log.info("Embed tables bootstrapped")


# ── Partner lookup ────────────────────────────────────────────────────────────

def _get_partner(partner_key: str) -> Optional[dict]:
    """Return partner row dict or None if not found / inactive."""
    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM embed_partners WHERE partner_key=%s AND active=1",
            (partner_key,)
        )
        row = cursor.fetchone()
        cursor.close()
        return row
    finally:
        conn.close()


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

    branding = partner.get("branding") or {}
    if isinstance(branding, str):
        try:
            branding = json.loads(branding)
        except Exception:
            branding = {}

    # Parse comma-separated origins for use in postMessage security checks
    raw_origins = partner.get("allowed_origins", "")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    log.info("Embed context requested", partner=partner["partner_name"])
    return {
        "partner_name":    partner["partner_name"],
        "provider_id":     partner["provider_id"],
        "partner_key":     pk,
        "app_name":        os.getenv("APP_NAME", "SalesPlay AI"),
        "branding":        branding,
        "allowed_origins": allowed_origins,
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
    try:
        create_user(req.name, req.email.strip().lower(), req.password)
        log.info("Embed: user created", email=req.email, partner=partner["partner_name"])
    except HTTPException as e:
        if "already registered" in str(e.detail):
            # Email exists — authenticate with provided password instead.
            # Handles the case where the user started onboarding but stopped.
            try:
                authenticate_user(req.email.strip().lower(), req.password)
                log.info("Embed: existing user re-authenticated", email=req.email)
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
        start_trial(req.email.strip().lower())
    except Exception as _te:
        log.warning("Embed: trial start skipped", email=req.email, error=str(_te))

    # 4. Connect the provider (validates API token, creates tables, starts sync)
    # connect_provider() raises ValueError on bad credentials, Exception on other failures
    try:
        connect_provider(
            user_email  = req.email.strip().lower(),
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

    # 5. Return JWT
    token = create_token(req.email.strip().lower())
    return {
        "token":       token,
        "user":        {"name": req.name, "email": req.email.strip().lower()},
        "provider_id": partner["provider_id"],
        "sync":        "started",
    }


# ── Salesplay auto-init endpoints ─────────────────────────────────────────────

@router.post("/salesplay/check-user")
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
        raise HTTPException(status_code=403, detail="This endpoint is only available for Salesplay partners.")

    email = req.email.strip().lower()

    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT email FROM users WHERE email = %s LIMIT 1", (email,))
        user_row = cursor.fetchone()
        exists = user_row is not None

        has_credentials = False
        credentials_healthy = False
        if exists:
            cursor.execute(
                "SELECT id, status FROM user_integrations "
                "WHERE user_email = %s AND provider_id = 'salesplay' LIMIT 1",
                (email,)
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


@router.post("/salesplay/auto-init")
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
        raise HTTPException(status_code=403, detail="This endpoint is only available for Salesplay partners.")

    email = req.email.strip().lower()
    name  = req.name.strip()

    # Derive a deterministic password from the email (e.g. john2@gmail.com → john2@gmail)
    password = email.rsplit(".", 1)[0]

    is_new_user = False
    try:
        create_user(name, email, password)
        is_new_user = True
        log.info("Salesplay auto-init: user created", email=email)
    except HTTPException as e:
        if "already registered" in str(e.detail):
            # Existing account — issue token directly; don't verify password because the
            # user may have a different password from a main-app registration.
            log.info("Salesplay auto-init: existing user", email=email)
        else:
            raise

    # Start free trial (silently skip if already active)
    try:
        start_trial(email)
    except Exception as _te:
        log.warning("Salesplay auto-init: trial start skipped", email=email, error=str(_te))

    # Connect provider only when a fresh API token is supplied
    sync = "skipped"
    api_token = (req.salesplay_api_token or "").strip()
    if api_token:
        try:
            connect_provider(
                user_email  = email,
                provider_id = "salesplay",
                credentials = {"api_token": api_token},
            )
            sync = "started"
            log.info("Salesplay auto-init: provider connected", email=email)
        except ValueError as e:
            log.warning("Salesplay auto-init: bad API token", email=email, error=str(e))
            raise HTTPException(status_code=422, detail="Invalid Salesplay API token.")
        except Exception as e:
            log.error("Salesplay auto-init: connect failed", email=email, error=str(e))
            raise HTTPException(status_code=500, detail="Failed to connect provider. Please try again.")

    token = create_token(email)
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

_SALESPLAY_BASE = os.getenv("SALESPLAY_EMBED_PROXY_BASE", "https://api.salesplaypos.com/v2.0/public/app")
_PROXY_TIMEOUT  = int(os.getenv("SALESPLAY_EMBED_PROXY_TIMEOUT", "10"))


def _salesplay_guard(partner_key: str, request: "Request"):
    """Validate partner key is active Salesplay, apply rate limit. Returns partner row."""
    _check_rate(_client_ip(request))
    partner = _get_partner(partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail="This endpoint is only available for Salesplay partners.")
    return partner


class SalesplayProfileRequest(BaseModel):
    partner_key: str
    aat:         str   # Salesplay app_access_token


class SalesplayCreateTokenRequest(BaseModel):
    partner_key: str
    aat:         str


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


@router.post("/salesplay/profile")
def salesplay_proxy_profile(request: Request, req: SalesplayProfileRequest):
    """
    Proxy: fetch the authenticated user's Salesplay profile.
    Forwards the app_access_token server-to-server to avoid browser CORS restrictions.
    Returns { email, name }.
    """
    _salesplay_guard(req.partner_key, request)

    try:
        resp = _http.get(
            f"{_SALESPLAY_BASE}/profile",
            headers={"Authorization": f"Bearer {req.aat.strip()}"},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Salesplay session expired. Please refresh the page.")
        resp.raise_for_status()
        data = resp.json()
        # Salesplay profile response: { "status": "success", "user": { "email": ..., "full_name": ... } }
        raw  = data.get("user") or data.get("data") or data
        email = (raw.get("email") or "").strip().lower()
        name  = (
            raw.get("full_name") or raw.get("name") or
            raw.get("business_name") or email.split("@")[0]
        ).strip()
        if not email:
            raise HTTPException(status_code=422, detail="Could not retrieve email from Salesplay profile.")
        locale = _extract_salesplay_locale(data)
        log.info("Salesplay proxy: profile fetched", email=email)
        return {"email": email, "name": name, "locale": locale}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: profile fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail="Could not reach Salesplay API. Please try again.")


@router.post("/salesplay/create-token")
def salesplay_proxy_create_token(request: Request, req: SalesplayCreateTokenRequest):
    """
    Proxy: create a DataMind integration access token in the user's Salesplay account.
    Forwards the app_access_token server-to-server to avoid browser CORS restrictions.
    Returns { token } — the Salesplay API token stored in user_integrations.
    """
    _salesplay_guard(req.partner_key, request)

    try:
        resp = _http.post(
            f"{_SALESPLAY_BASE}/integrations/access_tokens",
            headers={
                "Authorization":  f"Bearer {req.aat.strip()}",
                "Content-Type":   "application/json",
            },
            json={"name": "DataMind", "expire_enabled": False, "expires_at": ""},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Salesplay session expired. Please refresh the page.")
        resp.raise_for_status()
        data  = resp.json()
        token = (data.get("data") or {}).get("token")
        if not token:
            raise HTTPException(status_code=502, detail="Salesplay did not return an API token.")
        log.info("Salesplay proxy: access token created")
        return {"token": token}
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay proxy: create-token failed", error=str(e))
        raise HTTPException(status_code=502, detail="Could not create Salesplay API token. Please try again.")


# ── Single onboarding endpoint (widget calls this once after consent) ─────────

class SalesplayOnboardRequest(BaseModel):
    partner_key: str
    aat:         str


@router.post("/salesplay/onboard")
def salesplay_onboard(request: Request, req: SalesplayOnboardRequest):
    """
    All-in-one Salesplay onboarding. The widget calls this once after the user
    accepts the consent screen. The backend handles everything:
      1. Fetch user profile from Salesplay (server-to-server, no CORS)
      2. Check if a DataMind account + credentials already exist
      3. If no credentials: create a Salesplay API token (server-to-server)
      4. Create DataMind account if new, or issue JWT for existing account
      5. Start free trial, connect provider, trigger sync
      6. Return JWT + sync status
    """
    _check_rate(_client_ip(request))

    partner = _get_partner(req.partner_key)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    if partner["provider_id"] != "salesplay":
        raise HTTPException(status_code=403, detail="This endpoint is only available for Salesplay partners.")

    aat = req.aat.strip()

    # ── 1. Fetch Salesplay user profile ───────────────────────────────────────
    try:
        resp = _http.get(
            f"{_SALESPLAY_BASE}/profile",
            headers={"Authorization": f"Bearer {aat}"},
            timeout=_PROXY_TIMEOUT,
        )
        if resp.status_code == 401:
            raise HTTPException(status_code=401, detail="Salesplay session expired. Please refresh the page.")
        resp.raise_for_status()
        data  = resp.json()
        raw   = data.get("user") or data.get("data") or data
        email = (raw.get("email") or "").strip().lower()
        name  = (
            raw.get("full_name") or raw.get("name") or
            raw.get("business_name") or email.split("@")[0]
        ).strip()
        if not email:
            raise HTTPException(status_code=422, detail="Could not retrieve email from Salesplay profile.")
        locale = _extract_salesplay_locale(data)
        log.info("Salesplay onboard: profile fetched", email=email)
    except HTTPException:
        raise
    except Exception as e:
        log.error("Salesplay onboard: profile fetch failed", error=str(e))
        raise HTTPException(status_code=502, detail="Could not reach Salesplay API. Please try again.")

    # ── 2. Check if DataMind account + credentials already exist ──────────────
    conn = _get_conn()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT email FROM users WHERE email = %s LIMIT 1", (email,))
        user_exists = cursor.fetchone() is not None
        has_credentials = False
        credentials_healthy = False
        if user_exists:
            cursor.execute(
                "SELECT id, status FROM user_integrations "
                "WHERE user_email = %s AND provider_id = 'salesplay' LIMIT 1",
                (email,)
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
                f"{_SALESPLAY_BASE}/integrations/access_tokens",
                headers={
                    "Authorization": f"Bearer {aat}",
                    "Content-Type":  "application/json",
                },
                json={"name": "DataMind", "expire_enabled": False, "expires_at": ""},
                timeout=_PROXY_TIMEOUT,
            )
            if resp.status_code == 401:
                raise HTTPException(status_code=401, detail="Salesplay session expired. Please refresh the page.")
            resp.raise_for_status()
            token_data        = resp.json()
            salesplay_api_token = (token_data.get("data") or {}).get("token")
            if not salesplay_api_token:
                raise HTTPException(status_code=502, detail="Salesplay did not return an API token.")
            log.info("Salesplay onboard: API token created", email=email)
        except HTTPException:
            raise
        except Exception as e:
            log.error("Salesplay onboard: create-token failed", error=str(e))
            raise HTTPException(status_code=502, detail="Could not create Salesplay API token. Please try again.")

    # ── 4. Create DataMind account (or reuse existing) ────────────────────────
    password    = email.rsplit(".", 1)[0]
    is_new_user = False
    try:
        create_user(name, email, password)
        is_new_user = True
        log.info("Salesplay onboard: user created", email=email)
    except HTTPException as e:
        if "already registered" in str(e.detail):
            log.info("Salesplay onboard: existing user", email=email)
        else:
            raise

    # ── 5. Start trial (non-fatal) ────────────────────────────────────────────
    try:
        start_trial(email)
    except Exception as _te:
        log.warning("Salesplay onboard: trial start skipped", email=email, error=str(_te))

    # ── 5b. Persist locale from Salesplay profile (non-fatal) ─────────────────
    try:
        update_user_settings(email, {"locale": locale})
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
                user_email       = email,
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
            trigger_sync(email, "salesplay", full=False)
            sync = "delta_started"
            integ = get_integration(email, "salesplay")
            table_prefix = (integ or {}).get("table_prefix")
            log.info("Salesplay onboard: delta sync triggered for returning user", email=email)
        except Exception as e:
            log.warning("Salesplay onboard: delta sync trigger failed (non-fatal)",
                        email=email, error=str(e))
            sync = "skipped"

    # Report cache: refresh profile (shops/cashiers/currency) + stash the live
    # session token for chat-time report fetches. Feature-flagged, non-fatal.
    _sync_report_profile(table_prefix, email, aat)

    token = create_token(email)
    log.info("Salesplay onboard: complete", email=email, is_new_user=is_new_user, sync=sync)
    return {
        "token":       token,
        "user":        {"name": name, "email": email, "locale": locale},
        "provider_id": "salesplay",
        "is_new_user": is_new_user,
        "sync":        sync,
    }
