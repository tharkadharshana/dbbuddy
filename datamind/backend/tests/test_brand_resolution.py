"""Brand resolution for the multi-brand embed.

A brand is one embed_partners row. Several brands share one provider_id --
Salesplay, Sellmo and any future whitelabel all run provider_id='salesplay'.
These tests pin the two rules that keep them apart: no default may ever name a
brand, and every brand-visible value comes from that brand's own row.
"""

import json
import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from embed import _BRAND_DEFAULTS, _brand, brand_subscription_free

SALESPLAY = {
    "partner_name": "Salesplay",
    "provider_id": "salesplay",
    "branding": {"product_name": "SalesPlay AI", "company_name": "Salesplay",
                 "brand_slug": "salesplay"},
}
SELLMO = {
    "partner_name": "Sellmo",
    "provider_id": "salesplay",
    "branding": json.dumps({"product_name": "Sellmo AI", "company_name": "Sellmo",
                            "brand_slug": "sellmo", "subscription_free": True}),
}


def test_no_default_names_a_brand():
    """A default that named one brand would leak it into another brand's widget."""
    blob = json.dumps(_BRAND_DEFAULTS).lower()
    for word in ("salesplay", "sellmo", "datamind", "loyverse"):
        assert word not in blob


def test_branding_json_is_parsed_from_a_string_column():
    assert _brand(SELLMO)["product_name"] == "Sellmo AI"


def test_two_brands_on_one_provider_stay_distinct():
    assert _brand(SALESPLAY)["product_name"] == "SalesPlay AI"
    assert _brand(SELLMO)["product_name"] == "Sellmo AI"
    assert _brand(SALESPLAY)["company_name"] != _brand(SELLMO)["company_name"]


def test_partner_name_backstops_the_names():
    """partner_name is the only value guaranteed present, so it fills both."""
    bare = {"partner_name": "Acme", "provider_id": "salesplay", "branding": None}
    brand = _brand(bare)
    assert brand["product_name"] == "Acme"
    assert brand["company_name"] == "Acme"


def test_unparseable_branding_falls_back_to_defaults_not_an_error():
    bare = {"partner_name": "Acme", "provider_id": "salesplay", "branding": "{not json"}
    assert _brand(bare)["product_name"] == "Acme"


def test_null_values_do_not_override_defaults():
    bare = {"partner_name": "Acme", "provider_id": "salesplay",
            "branding": {"primary_color": None}}
    assert _brand(bare)["primary_color"] == _BRAND_DEFAULTS["primary_color"]


# ── per-brand billing ────────────────────────────────────────────────────────

def test_brand_can_be_free_while_another_charges():
    """A new whitelabel launches free while an established brand already charges."""
    assert brand_subscription_free(SELLMO) is True
    assert brand_subscription_free(SALESPLAY) is False


def test_brand_without_an_override_inherits_the_process_default(monkeypatch):
    import embed
    monkeypatch.setattr(embed, "SUBSCRIPTION_FREE", True)
    assert brand_subscription_free(SALESPLAY) is True
    monkeypatch.setattr(embed, "SUBSCRIPTION_FREE", False)
    assert brand_subscription_free(SALESPLAY) is False


def test_explicit_false_beats_a_true_process_default(monkeypatch):
    """A brand that has started charging must not be reset to free by the env."""
    import embed
    monkeypatch.setattr(embed, "SUBSCRIPTION_FREE", True)
    paid = {"partner_name": "Paid", "provider_id": "salesplay",
            "branding": {"brand_slug": "paid", "subscription_free": False}}
    assert brand_subscription_free(paid) is False


def test_no_partner_falls_back_to_the_process_default(monkeypatch):
    import embed
    monkeypatch.setattr(embed, "SUBSCRIPTION_FREE", True)
    assert brand_subscription_free(None) is True
