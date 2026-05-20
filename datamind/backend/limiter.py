"""
limiter.py
==========
Shared slowapi rate limiter instance for main.py and v1.py.
All limits are configurable via environment variables — restart the server
after changing .env for new limits to take effect.

Defaults (conservative — tighten in production via .env):
  RATE_LIMIT_AUTH         5/minute   POST /auth/register
  RATE_LIMIT_AUTH_LOGIN  10/minute   POST /auth/login
  RATE_LIMIT_COMPUTE     10/minute   CPU/LLM-heavy endpoints (query, analytics, forecast, anomalies, report)
  RATE_LIMIT_READ        60/minute   All read-only GET endpoints
  RATE_LIMIT_WRITE       30/minute   Mutation endpoints (settings, providers, billing)
  RATE_LIMIT_V1          30/minute   Partner API /v1/ endpoints
"""

import os
from slowapi import Limiter


def _rate_limit_key(request) -> str:
    """Use real client IP, X-Forwarded-For aware for nginx/ALB deployments."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


limiter = Limiter(key_func=_rate_limit_key)

# Read from .env at import time (module loaded once at startup)
RL_AUTH         = os.getenv("RATE_LIMIT_AUTH",        "5/minute")
RL_AUTH_LOGIN   = os.getenv("RATE_LIMIT_AUTH_LOGIN",  "10/minute")
RL_COMPUTE      = os.getenv("RATE_LIMIT_COMPUTE",     "10/minute")
RL_READ         = os.getenv("RATE_LIMIT_READ",        "60/minute")
RL_WRITE        = os.getenv("RATE_LIMIT_WRITE",       "30/minute")
RL_V1           = os.getenv("RATE_LIMIT_V1",          "30/minute")
