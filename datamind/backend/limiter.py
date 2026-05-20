"""
limiter.py
==========
Two slowapi Limiter instances with different bucketing strategies:

  limiter         — keyed by client IP.  Used by all user-facing endpoints
                    in main.py.  Each user's IP gets its own independent counter.

  partner_limiter — keyed by X-API-Key.  Used by all /v1/ Partner API endpoints
                    in v1.py.  Each partner key gets its own independent counter,
                    so a partner's backend server calling on behalf of 500 users
                    is not capped by a single IP bucket.

All limits are configurable via .env — restart to apply changes.

  RATE_LIMIT_AUTH         5/minute   POST /auth/register       (per IP)
  RATE_LIMIT_AUTH_LOGIN  10/minute   POST /auth/login          (per IP)
  RATE_LIMIT_COMPUTE     10/minute   /query, /analytics/run,   (per IP — per user)
                                     /forecast, /anomalies, /report
  RATE_LIMIT_READ        60/minute   All GET endpoints         (per IP — per user)
  RATE_LIMIT_WRITE       30/minute   Settings, billing, sync   (per IP — per user)
  RATE_LIMIT_V1          30/minute   All /v1/ endpoints        (per API key — per partner)
"""

import os
from slowapi import Limiter


def _rate_limit_key(request) -> str:
    """Client IP — X-Forwarded-For aware for nginx/ALB deployments."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _partner_key(request) -> str:
    """Partner API key from X-API-Key header.
    Falls back to IP so unauthenticated probes are still rate-limited."""
    return request.headers.get("X-API-Key") or _rate_limit_key(request)


# User-facing limiter — buckets by IP (one counter per end-user)
limiter = Limiter(key_func=_rate_limit_key)

# Partner API limiter — buckets by API key (one counter per partner, not per IP)
partner_limiter = Limiter(key_func=_partner_key)

# Read from .env at import time (module loaded once at startup)
RL_AUTH         = os.getenv("RATE_LIMIT_AUTH",        "5/minute")
RL_AUTH_LOGIN   = os.getenv("RATE_LIMIT_AUTH_LOGIN",  "10/minute")
RL_COMPUTE      = os.getenv("RATE_LIMIT_COMPUTE",     "10/minute")
RL_READ         = os.getenv("RATE_LIMIT_READ",        "60/minute")
RL_WRITE        = os.getenv("RATE_LIMIT_WRITE",       "30/minute")
RL_V1           = os.getenv("RATE_LIMIT_V1",          "30/minute")
