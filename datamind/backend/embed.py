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
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from logger import get_logger
from auth import create_user, authenticate_user, create_token
from billing import start_trial
from integrations import connect_provider
from pool import get_internal_conn as _get_conn

log = get_logger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])

# ── Simple in-memory rate limiter (no external deps) ─────────────────────────
# Tracks per-IP request timestamps for /embed/init (5 requests/minute max).
_rate_store: dict = collections.defaultdict(list)
_RATE_LIMIT   = 5    # max calls
_RATE_WINDOW  = 60   # seconds

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
        return {"ok": False, "error": str(e)}


class EmbedInitRequest(BaseModel):
    partner_key: str
    api_token:   str              # Salesplay API token
    name:        str              # Full name for DataMind account
    email:       str
    password:    str = Field(min_length=8)


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
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.error("Embed: provider connect failed", email=req.email, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to connect: {e}")

    # 5. Return JWT
    token = create_token(req.email.strip().lower())
    return {
        "token":       token,
        "user":        {"name": req.name, "email": req.email.strip().lower()},
        "provider_id": partner["provider_id"],
        "sync":        "started",
    }
