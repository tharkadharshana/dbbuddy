"""Nothing on the wire may name the integration to a whitelabel merchant.

The widget calls our own backend, never the provider directly, so the provider's
API was never exposed. What did leak was our own naming around it: route paths,
error copy, and the provider's own words forwarded verbatim.
"""

import os
import sys

import pytest
from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import embed
from embed import (
    ERR_SESSION_EXPIRED,
    ERR_UNREACHABLE,
    _msg,
    _require_allowed_origin,
    _scrub_brand,
)

SELLMO = {
    "partner_name": "Sellmo",
    "provider_id": "salesplay",
    "allowed_origins": "https://app.sellmo.com,https://backoffice.sellmo.com",
    "branding": {"company_name": "Sellmo", "brand_slug": "sellmo"},
}


class FakeRequest:
    def __init__(self, **headers):
        self.headers = {k.lower(): v for k, v in headers.items()}


# ── merchant-facing copy ─────────────────────────────────────────────────────

def test_error_copy_names_the_merchants_own_brand():
    assert _msg(SELLMO, ERR_SESSION_EXPIRED) == "Sellmo session expired. Please refresh the page."
    assert "salesplay" not in _msg(SELLMO, ERR_UNREACHABLE).lower()


def test_copy_without_a_partner_stays_brand_neutral():
    """Better a generic word than another company's name."""
    for template in (ERR_SESSION_EXPIRED, ERR_UNREACHABLE):
        assert "salesplay" not in _msg(None, template).lower()


# ── forwarded provider text ──────────────────────────────────────────────────

def test_forwarded_fault_is_rebranded_not_discarded():
    """The merchant still sees the real fault -- attributed to their own brand."""
    out = _scrub_brand("SalesPlay API returned 500", SELLMO)
    assert "SalesPlay" not in out
    assert "Sellmo API returned 500" == out


@pytest.mark.parametrize("raw", [
    "salesplay is down",
    "SALESPLAYPOS timeout",
    "Sales Play gateway error",
])
def test_scrub_catches_the_spelling_variants(raw):
    assert "play" not in _scrub_brand(raw, SELLMO).lower()


def test_scrub_is_a_noop_without_a_partner():
    assert _scrub_brand("SalesPlay down", None) == "SalesPlay down"


# ── origin enforcement ───────────────────────────────────────────────────────

def test_key_from_an_unlisted_origin_is_refused():
    """The key is visible in the iframe src, so it must not work anywhere."""
    with pytest.raises(HTTPException) as e:
        _require_allowed_origin(SELLMO, FakeRequest(origin="https://evil.example"))
    assert e.value.status_code == 403


def test_key_from_its_own_origin_is_allowed():
    _require_allowed_origin(SELLMO, FakeRequest(origin="https://app.sellmo.com"))


def test_referer_is_used_when_origin_is_absent():
    _require_allowed_origin(SELLMO, FakeRequest(referer="https://app.sellmo.com/dashboard"))
    with pytest.raises(HTTPException):
        _require_allowed_origin(SELLMO, FakeRequest(referer="https://evil.example/x"))


def test_empty_allowed_origins_stays_unrestricted():
    """Existing rows and dev must keep working exactly as before."""
    open_partner = dict(SELLMO, allowed_origins="")
    _require_allowed_origin(open_partner, FakeRequest(origin="https://anywhere.example"))


def test_no_origin_and_no_referer_is_allowed():
    """Server-to-server callers send neither; refusing them would break them."""
    _require_allowed_origin(SELLMO, FakeRequest())


# ── route paths ──────────────────────────────────────────────────────────────

def test_no_route_path_names_the_integration():
    paths = [r.path for r in embed.router.routes]
    assert paths, "router exposed no routes"
    for path in paths:
        assert "salesplay" not in path.lower(), path
