"""
tests/test_salesplay_error.py
_salesplay_error() must hand the merchant Salesplay's OWN message — including
raw PHP faults — and only fall back when the body carries nothing usable.
"""

import json
import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embed import _salesplay_error

FALLBACK = "Could not reach Salesplay API. Please try again."


class FakeResp:
    """Minimal stand-in for a requests.Response."""
    def __init__(self, body, text=None):
        self._body = body
        self.text = text if text is not None else (
            json.dumps(body) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def test_top_level_message_used():
    resp = FakeResp({"status": "error",
                     "message": "Payment requires additional authentication."})
    assert _salesplay_error(resp, FALLBACK) == "Payment requires additional authentication."


def test_php_fatal_surfaced_verbatim():
    # The real predev2 failure when billing details exist but no card does.
    resp = FakeResp({"status": "error",
                     "message": "Undefined variable $systemCheckingErrorStatus"})
    assert _salesplay_error(resp, FALLBACK) == "Undefined variable $systemCheckingErrorStatus"


def test_nested_error_message_used_when_no_top_level():
    resp = FakeResp({"status": "error",
                     "error": {"code": "AUTHENTICATION_REQUIRED",
                               "message": "Complete the authentication process."}})
    assert _salesplay_error(resp, FALLBACK) == "Complete the authentication process."


def test_error_code_used_when_no_message_anywhere():
    resp = FakeResp({"status": "error", "error": {"code": "AUTHENTICATION_REQUIRED"}})
    assert _salesplay_error(resp, FALLBACK) == "AUTHENTICATION_REQUIRED"


def test_html_error_page_falls_back():
    # An HTML fault page must never land in the widget's error box.
    resp = FakeResp(None, text="<!DOCTYPE html><html><body>500</body></html>")
    assert _salesplay_error(resp, FALLBACK) == FALLBACK


def test_plain_text_body_surfaced():
    resp = FakeResp(None, text="Service temporarily unavailable")
    assert _salesplay_error(resp, FALLBACK) == "Service temporarily unavailable"


def test_empty_body_falls_back():
    assert _salesplay_error(FakeResp(None, text="   "), FALLBACK) == FALLBACK


def test_blank_message_falls_back():
    resp = FakeResp({"status": "error", "message": "   ", "error": None})
    assert _salesplay_error(resp, FALLBACK) == FALLBACK


def test_long_message_truncated():
    resp = FakeResp({"message": "x" * 500})
    assert len(_salesplay_error(resp, FALLBACK)) == 300


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("all passed")
