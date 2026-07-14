"""
tests/test_client.py
======================
Structural checks on report_cache/client.py — no network calls. Guards
against the two bugs found in code review:
  1. A second, independent base-URL env var duplicating SALESPLAY_EMBED_PROXY_BASE
     (embed.py already owns this setting for the same SalesPlay API host).
  2. The wrong auth header ("Token: Bearer", copied from the unrelated v1.0
     data-sync SalesPlayAPIClient) instead of "Authorization: Bearer", which
     is what the app_api JWT guard on these routes actually expects (same
     header embed.py's SalesPlay proxy already uses).
Run: cd datamind/backend && pytest tests/test_client.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.client import ReportAPIClient
from report_cache.registry import REPORTS


def test_base_url_defaults_to_salesplay_embed_proxy_base(monkeypatch):
    monkeypatch.setenv("SALESPLAY_EMBED_PROXY_BASE", "https://spqa.nvision.lk/rest/v2.0/public/app")
    # module-level REPORT_API_BASE_URL is read at import time, so re-import
    import importlib
    import report_cache.client as client_module
    importlib.reload(client_module)

    client = client_module.ReportAPIClient("fake-token")
    assert client.base_url == "https://spqa.nvision.lk/rest/v2.0/public/app"


def test_explicit_base_url_overrides_env():
    client = ReportAPIClient("fake-token", base_url="https://example.test/rest/v2.0/public/app")
    assert client.base_url == "https://example.test/rest/v2.0/public/app"


def test_auth_header_is_authorization_bearer_not_token():
    client = ReportAPIClient("fake-token", base_url="https://example.test")
    assert client.session.headers.get("Authorization") == "Bearer fake-token"
    assert "Token" not in client.session.headers


def test_fetch_report_builds_url_without_duplicate_app_segment():
    client = ReportAPIClient("fake-token", base_url="https://example.test/rest/v2.0/public/app")
    for report in REPORTS.values():
        url = f"{client.base_url}/{report.endpoint.lstrip('/')}"
        assert "/app/app/" not in url, f"{report.id} builds a duplicate-/app/ URL: {url}"
        assert url == f"https://example.test/rest/v2.0/public/app{report.endpoint}"


def test_fetch_profile_url_has_no_duplicate_app_segment():
    client = ReportAPIClient("fake-token", base_url="https://example.test/rest/v2.0/public/app")
    url = f"{client.base_url}/profile"
    assert url == "https://example.test/rest/v2.0/public/app/profile"


def test_empty_base_url_raises():
    import importlib
    import report_cache.client as client_module
    original = client_module.REPORT_API_BASE_URL
    try:
        client_module.REPORT_API_BASE_URL = ""
        try:
            client_module.ReportAPIClient("fake-token")
            assert False, "expected ValueError for empty base_url"
        except ValueError:
            pass
    finally:
        client_module.REPORT_API_BASE_URL = original


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
