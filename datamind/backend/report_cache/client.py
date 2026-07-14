"""
report_cache/client.py
=======================
ReportAPIClient — HTTP client for the SalesPlay POS report APIs
(salesplay-internal-api-v2, `Route::prefix('app')` group in routes/app.php,
GET /<report>). Retry/backoff/logging conventions mirror
providers/salesplay/sync.py:SalesPlayAPIClient (doc 08 §3.1) — but NOT its
auth header or base URL, which belong to a different SalesPlay API surface
entirely (the v1.0 data-sync API). This client hits the same v2.0
"public/app" surface embed.py's SalesPlay proxy already calls:

  - Base URL: reuses SALESPLAY_EMBED_PROXY_BASE (embed.py:_SALESPLAY_BASE) —
    a single source of truth for this API's host, not a second copy of it.
    That value already includes the "/app" prefix routes/app.php is
    mounted under (`Route::prefix('app')`, confirmed in
    RouteServiceProvider.php), so endpoint paths in registry.py are
    relative to it and must NOT repeat "/app/".
  - Auth header: "Authorization: Bearer <token>" — the report routes sit in
    the same `Route::middleware(['app.auth'])` group as ProfileController@profile
    (routes/app.php), guarded by the 'app_api' JWT guard (config/auth.php),
    which reads the standard Authorization header — same as embed.py's
    `_salesplay_guard`/`salesplay_proxy_profile` calls. This is a DIFFERENT
    header than SalesPlayAPIClient's "Token: Bearer", which authenticates
    against the unrelated v1.0 data-sync API.

Sync for now — the ingestion jobs that call this run in background workers
(PLAN 03/04), not on a web-request thread. An async variant is added in
PLAN 07 only if SSE needs one directly.

Library only in this phase — nothing in main.py calls this yet (PLAN 01 is
plumbing; wiring happens in PLAN 03+).
"""

import os
import time
from typing import Any, Dict, Optional

import requests
import urllib3

from logger import get_logger
from report_cache.registry import REPORTS

log = get_logger(__name__)

# Same env var + default as embed.py:_SALESPLAY_BASE — do not introduce a
# second base-URL setting for the same API host.
_DEFAULT_BASE_URL = "https://api.salesplaypos.com/v2.0/public/app"
REPORT_API_BASE_URL = os.getenv("SALESPLAY_EMBED_PROXY_BASE", _DEFAULT_BASE_URL)

_HTTP_TIMEOUT   = int(os.getenv("REPORT_API_HTTP_TIMEOUT", "90"))   # reports are heavy (set_time_limit(90) server-side) — deliberately separate from SALESPLAY_EMBED_PROXY_TIMEOUT (10s), which is tuned for light profile/token calls, not report fetches
_RETRY_ATTEMPTS = int(os.getenv("REPORT_API_RETRY_ATTEMPTS", "3"))
_VERIFY_SSL     = os.getenv("REPORT_API_VERIFY_SSL", "true").lower() not in ("false", "0", "no")
_MAX_PAGE_CAP   = int(os.getenv("REPORT_API_MAX_PAGE_CAP", "50"))   # hard ceiling regardless of caller's max_pages

if not _VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ReportAPIClient:
    """
    Auth: "Authorization: Bearer <token>" — same header the app_api JWT guard
    expects for every /app/* route, matching embed.py's SalesPlay proxy calls.
    Params sent as query string (these are GET routes — see routes/app.php).
    """

    def __init__(self, access_token: str, base_url: Optional[str] = None):
        if not access_token:
            raise ValueError("ReportAPIClient requires a non-empty access_token")
        self.base_url = (base_url or REPORT_API_BASE_URL).rstrip("/")
        if not self.base_url:
            raise ValueError("SALESPLAY_EMBED_PROXY_BASE is not configured")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        })
        self.session.verify = _VERIFY_SSL

    def _request(self, path: str, params: Dict[str, Any]) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"

        for attempt in range(_RETRY_ATTEMPTS):
            t0 = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=_HTTP_TIMEOUT, verify=_VERIFY_SSL)
            except requests.exceptions.ConnectionError as exc:
                log.warning("Report API connection error", url=url, attempt=attempt, error=str(exc))
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.warning("Report API timeout", url=url, attempt=attempt, timeout=_HTTP_TIMEOUT)
                time.sleep(3)
                continue

            elapsed_ms = int((time.monotonic() - t0) * 1000)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                log.warning("Report API rate limited", url=url, wait_seconds=wait)
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                log.error("Report API 401 Unauthorized", url=url)
                raise Exception("Report API token is invalid or expired.")

            if not resp.ok:
                preview = resp.text[:300] if resp.text else "(empty)"
                log.error("Report API error", url=url, status=resp.status_code, ms=elapsed_ms, body=preview)
                raise Exception(f"Report API HTTP {resp.status_code}: {preview}")

            try:
                data = resp.json()
            except ValueError:
                log.error("Report API non-JSON response", url=url, raw=resp.text[:500])
                raise Exception(f"Report API returned non-JSON: {resp.text[:200]}")

            log.info("Report API call", url=url, params=params, status=resp.status_code, ms=elapsed_ms)
            return data

        raise Exception(f"Report API: failed after {_RETRY_ATTEMPTS} retries.")

    def fetch_report(
        self, report_id: str, *,
        start_date: str, end_date: str,
        shop_id: str = "all", cashier_id: str = "all", customer_id: str = "all",
        from_time: str = "00:00", to_time: str = "23:59",
        page: int = 1, per_page: int = 1000,
        **extra_params: Any,
    ) -> dict:
        report = REPORTS.get(report_id)
        if report is None:
            raise ValueError(f"Unknown report_id: {report_id}")

        params = {
            "start_date": start_date,
            "end_date": end_date,
            "from_time": from_time,
            "to_time": to_time,
            "shop_id": shop_id,
            "cashier_id": cashier_id,
            "customer_id": customer_id,
            "page": page,
            "per_page": per_page,
        }
        # only pass through extra params the registry actually declares for this report
        # (e.g. category_id/subcategory_id on sales_by_products/sales_by_category)
        for key, value in extra_params.items():
            if key in report.params:
                params[key] = value

        return self._request(report.endpoint, params)

    def fetch_report_all_pages(self, report_id: str, *, max_pages: int = 20, **params: Any) -> dict:
        """Follow pagination.has_next_page, concatenating table_data, capped at
        min(max_pages, REPORT_API_MAX_PAGE_CAP) (doc 09 C1/§3.5)."""
        page_cap = min(max_pages, _MAX_PAGE_CAP)
        params.pop("page", None)

        all_rows = []
        summary = {}
        pagination_meta = {}
        page = 1

        while page <= page_cap:
            data = self.fetch_report(report_id, page=page, **params)
            body = data.get("data", {})
            table_data = body.get("table_data", [])
            if page == 1:
                summary = body.get("summary", {})
            all_rows.extend(table_data)

            pagination_meta = data.get("pagination", {})
            if not pagination_meta.get("has_next_page"):
                break
            page += 1
        else:
            log.warning("Report API pagination hit page cap", report=report_id, page_cap=page_cap)

        return {"summary": summary, "table_data": all_rows, "pagination_meta": pagination_meta}

    def fetch_profile(self) -> dict:
        return self._request("/profile", {})
