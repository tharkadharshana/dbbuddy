"""Per-brand provider API bases.

A whitelabel does not have to share its parent's provider backend: Salesplay
runs on predev2 and its whitelabel Sellmo on predev1, and in production each
partner has its own backoffice host. One deployment has to serve both at once,
so these URLs belong to the brand row, not to process env.

They are also the hosts the integration asked us not to expose, which is why
they live in `api_config` and never in `branding`.
"""

import json
import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import partner_api

SALESPLAY = {
    "partner_name": "Salesplay",
    "api_config": {
        "sync_base": "https://predev2api.nvision.lk/v1.0",
        "proxy_base": "https://predev2backoffice.nvision.lk/rest/v2.0/public/app",
        "subscription_base": "https://predev2backoffice.nvision.lk/rest/v2.0/public/app",
    },
}

# Stored as a JSON string, which is what some connector configurations return.
SELLMO = {
    "partner_name": "Sellmo",
    "api_config": json.dumps({
        "sync_base": "https://predev1api.nvision.lk/v1.0",
        "proxy_base": "https://predev1backoffice.nvision.lk/rest/v2.0/public/app",
        "subscription_base": "https://predev1backoffice.nvision.lk/rest/v2.0/public/app",
    }),
}


def test_two_brands_on_one_provider_reach_different_instances():
    """The whole point: predev2 and predev1 served by one process."""
    sp = partner_api.for_partner(SALESPLAY)
    sl = partner_api.for_partner(SELLMO)
    for key in ("sync_base", "proxy_base", "subscription_base"):
        assert sp[key] != sl[key]
    assert "predev2" in sp["proxy_base"]
    assert "predev1" in sl["proxy_base"]


def test_json_string_column_is_parsed():
    assert partner_api.for_partner(SELLMO)["sync_base"] == "https://predev1api.nvision.lk/v1.0"


def test_missing_config_falls_back_to_env(monkeypatch):
    """A single-brand deployment that never sets api_config must not change."""
    monkeypatch.setenv("SALESPLAY_BASE_URL", "https://env.example/v1.0")
    monkeypatch.setenv("SALESPLAY_EMBED_PROXY_BASE", "https://env.example/app")
    monkeypatch.delenv("SALESPLAY_SUBSCRIPTION_BASE_URL", raising=False)
    out = partner_api.for_partner({"partner_name": "Legacy"})
    assert out["sync_base"] == "https://env.example/v1.0"
    assert out["proxy_base"] == "https://env.example/app"
    # Historically the subscription base defaulted to the proxy base.
    assert out["subscription_base"] == "https://env.example/app"


def test_partial_config_only_overrides_what_it_names(monkeypatch):
    monkeypatch.setenv("SALESPLAY_BASE_URL", "https://env.example/v1.0")
    out = partner_api.for_partner({"api_config": {"proxy_base": "https://brand.example/app/"}})
    assert out["proxy_base"] == "https://brand.example/app"   # trailing slash stripped
    assert out["sync_base"] == "https://env.example/v1.0"


def test_bad_json_does_not_take_the_provider_down(monkeypatch):
    monkeypatch.setenv("SALESPLAY_BASE_URL", "https://env.example/v1.0")
    out = partner_api.for_partner({"partner_name": "Broken", "api_config": "{not json"})
    assert out["sync_base"] == "https://env.example/v1.0"


def test_api_config_is_never_sent_to_the_browser():
    """These are the provider hosts we promised not to expose. /embed/context
    builds an explicit field list -- this pins that api_config is not in it."""
    import embed
    partner = dict(SALESPLAY, partner_key="pk_test", provider_id="salesplay",
                   allowed_origins="", branding={"brand_slug": "salesplay"})
    orig = embed._get_partner
    embed._get_partner = lambda pk: partner
    try:
        body = embed.get_embed_context("pk_test")
    finally:
        embed._get_partner = orig
    blob = json.dumps(body)
    assert "api_config" not in blob
    assert "predev2" not in blob
    assert "nvision" not in blob
