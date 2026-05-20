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
from typing import Optional

import mysql.connector
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from logger import get_logger
from auth import create_user, authenticate_user, create_token
from billing import start_trial
from integrations import connect_provider

log = get_logger(__name__)

router = APIRouter(prefix="/embed", tags=["embed"])


# ── Internal DB connection ────────────────────────────────────────────────────

def _get_conn():
    return mysql.connector.connect(
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


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
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        "SELECT * FROM embed_partners WHERE partner_key=%s AND active=1",
        (partner_key,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/context")
def get_embed_context(pk: str):
    """
    Called by the iframe on load to validate the partner key.
    Returns partner name, provider_id, and optional branding config.
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

    log.info("Embed context requested", partner=partner["partner_name"])
    return {
        "partner_name": partner["partner_name"],
        "provider_id":  partner["provider_id"],
        "partner_key":  pk,
        "branding":     branding,
    }


class EmbedInitRequest(BaseModel):
    partner_key: str
    api_token:   str   # Salesplay API token
    name:        str   # Full name for DataMind account
    email:       str
    password:    str


@router.post("/init")
def embed_init(req: EmbedInitRequest):
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
