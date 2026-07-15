"""
tests/test_report_profile.py — report_cache client URL/auth assembly and
profile mapping (shapes verified against ProfileController@profile source).
"""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from report_cache import client as rc_client
from report_cache.profile import map_profile

PROFILE_RAW = {
    "status": "success",
    "user": {
        "email": "owner@shop.lk",
        "currency": "Rs.",
        "ui_language": "en",
        "timezone": "Asia/Colombo",
        "number_format": {"decimal_point": ".", "thousand_separator": ","},
        "date_format": "DD/MM/YYYY",
        "time_format": "hh:mm A",
    },
    "shop_list": [
        {"location_id": 101, "name": "Main Outlet", "is_enable": "1"},
        {"location_id": 102, "name": "Kandy Branch", "is_enable": "0"},
    ],
    "cashier_list": [
        {"user_name": "kasun@shop.lk", "name": "Kasun", "user_id": "77"},
        {"user_name": "amara@shop.lk", "name": "", "user_id": None},
    ],
}


# ── map_profile ──────────────────────────────────────────────────────────────

def test_map_profile_user_fields_come_from_user_block():
    m = map_profile(PROFILE_RAW)
    assert m["currency"] == "Rs."
    assert m["ui_language"] == "en"
    assert m["timezone"] == "Asia/Colombo"
    assert '"decimal_point"' in m["number_format"]


def test_map_profile_shops():
    m = map_profile(PROFILE_RAW)
    assert m["shops"] == [
        {"shop_id": "101", "shop_name": "Main Outlet", "is_enabled": 1},
        {"shop_id": "102", "shop_name": "Kandy Branch", "is_enabled": 0},
    ]


def test_map_profile_cashiers_name_falls_back_to_user_name():
    m = map_profile(PROFILE_RAW)
    assert m["cashiers"][0] == {
        "user_name": "kasun@shop.lk", "cashier_id": "77", "cashier_name": "Kasun"}
    # blank display name -> user_name; empty user_id -> None
    assert m["cashiers"][1]["cashier_name"] == "amara@shop.lk"
    assert m["cashiers"][1]["cashier_id"] is None


def test_map_profile_empty_response_is_safe():
    m = map_profile({})
    assert m["currency"] is None and m["shops"] == [] and m["cashiers"] == []


# ── ReportAPIClient ──────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers,
                           "timeout": timeout})
        return self.responses.pop(0)


def test_client_url_and_auth_header(monkeypatch):
    monkeypatch.setenv("SALESPLAY_EMBED_PROXY_BASE", "https://pos.example/v2.0/public/app/")
    sess = _FakeSession([_FakeResp(200, {"ok": True})])
    out = rc_client.ReportAPIClient("tok-123", session=sess).get(
        "/sales_summary", {"shop_id": "all"})
    assert out == {"ok": True}
    call = sess.calls[0]
    # no duplicate /app segment, no trailing-slash double-up
    assert call["url"] == "https://pos.example/v2.0/public/app/sales_summary"
    assert call["headers"]["Authorization"] == "Bearer tok-123"
    assert call["params"] == {"shop_id": "all"}


def test_client_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setenv("REPORT_API_RETRY_ATTEMPTS", "3")
    monkeypatch.setattr(rc_client.time, "sleep", lambda s: None)
    sess = _FakeSession([_FakeResp(500), _FakeResp(429), _FakeResp(200, {"n": 1})])
    assert rc_client.ReportAPIClient("t", session=sess).get("/taxes") == {"n": 1}
    assert len(sess.calls) == 3


def test_client_raises_after_exhausted_retries(monkeypatch):
    monkeypatch.setenv("REPORT_API_RETRY_ATTEMPTS", "2")
    monkeypatch.setattr(rc_client.time, "sleep", lambda s: None)
    sess = _FakeSession([_FakeResp(500), _FakeResp(500)])
    with pytest.raises(RuntimeError, match="500"):
        rc_client.ReportAPIClient("t", session=sess).get("/taxes")


def test_client_does_not_retry_4xx(monkeypatch):
    sess = _FakeSession([_FakeResp(401)])
    with pytest.raises(RuntimeError, match="401"):
        rc_client.ReportAPIClient("bad", session=sess).get("/profile")
    assert len(sess.calls) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
