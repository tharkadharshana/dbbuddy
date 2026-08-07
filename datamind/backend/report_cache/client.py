"""
report_cache/client.py — HTTP client for the SalesPlay internal /app/* API
(profile + the 35 report endpoints).

Auth facts (verified against the Laravel source + live testing on the old
branch — do not re-learn these the hard way):
  - Base URL is SALESPLAY_EMBED_PROXY_BASE (same one embed.py's proxy uses,
    already ends in .../public/app) — endpoints are '/profile',
    '/sales_summary', ... with NO extra '/app' prefix.
  - The routes sit behind the `app_api` JWT guard and expect
    `Authorization: Bearer <token>` where the token is the short-lived
    app_access_token (`aat`) from a live widget session. The long-lived
    integration token stored for the v1.0 data-sync API does NOT work here
    (guard accepts it but resolves no user -> 404 "User not found").
"""

import os
import time

import requests

from logger import get_logger
from .filter_key import build_filter_key

log = get_logger(__name__)


def _base_url() -> str:
    return os.getenv(
        "SALESPLAY_EMBED_PROXY_BASE",
        "https://api.salesplaypos.com/v2.0/public/app",
    ).rstrip("/")


class ReportAPIClient:
    def __init__(self, access_token: str, timeout: int = None, session=None):
        self._token = (access_token or "").strip()
        # Report endpoints run up to set_time_limit(90) server-side — the 10s
        # proxy timeout used for profile calls elsewhere is not enough here.
        self._timeout = timeout or int(os.getenv("REPORT_API_HTTP_TIMEOUT", "90"))
        self._http = session or requests.Session()

    def get(self, endpoint: str, params: dict = None) -> dict:
        """GET {base}/{endpoint}, retrying 429/5xx with backoff. Raises
        requests.HTTPError on a final non-2xx response.

        Date-ranged calls (start_date/end_date present) get an X-Filter-Key
        header — SalesPlay's internal report API requires it to prove which
        day-range we're entitled to (docs/salesplay-encrypted-param.md).
        Regenerated fresh per call since the payload has a 60s freshness
        window and can't be reused across retries."""
        url = f"{_base_url()}/{endpoint.lstrip('/')}"
        attempts = max(1, int(os.getenv("REPORT_API_RETRY_ATTEMPTS", "3")))
        is_date_ranged = bool((params or {}).get("start_date") or (params or {}).get("end_date"))
        resp = None
        for attempt in range(1, attempts + 1):
            headers = {"Authorization": f"Bearer {self._token}"}
            if is_date_ranged:
                headers["X-Filter-Key"] = build_filter_key()
            resp = self._http.get(
                url,
                params=params or {},
                headers=headers,
                timeout=self._timeout,
            )
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < attempts:
                wait = min(2 ** attempt, 10)
                log.warning("Report API retry", url=url, status=resp.status_code,
                            attempt=attempt, wait=wait)
                time.sleep(wait)
                continue
            break
        resp.raise_for_status()
        return resp.json()

    def fetch_profile(self) -> dict:
        return self.get("/profile")
