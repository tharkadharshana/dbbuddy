"""Brand resolution must never leak one brand's identity into another's app.

Runs without a database: resolve_partner_by_host is the only piece that needs
one, and its host->row matching is what we stub.
"""
import json
from embed import _brand


def _row(name, branding):
    return {"partner_name": name, "branding": json.dumps(branding)}


def test_brand_carries_its_own_logo():
    b = _brand(_row("Sellmo", {
        "product_name": "Sellmo AI",
        "logo_url": "/brand/sellmo-logo.png",
        "logo_mark_url": "/brand/sellmo-mark.png",
    }))
    assert b["logo_url"] == "/brand/sellmo-logo.png"
    assert b["product_name"] == "Sellmo AI"


def test_missing_logo_does_not_fall_back_to_another_brand():
    # The bug this whole change exists to prevent: a default naming one brand
    # surfaces in every other brand's app.
    b = _brand(_row("Newco", {"product_name": "Newco AI"}))
    assert b["logo_url"] is None
    assert b["logo_mark_url"] is None
    assert "salesplay" not in json.dumps(b).lower()


def test_product_name_backstops_to_partner_name():
    b = _brand(_row("Loyverse", {}))
    assert b["product_name"] == "Loyverse"
    assert b["logo_url"] is None


if __name__ == "__main__":
    test_brand_carries_its_own_logo()
    test_missing_logo_does_not_fall_back_to_another_brand()
    test_product_name_backstops_to_partner_name()
    print("all brand resolution checks passed")
