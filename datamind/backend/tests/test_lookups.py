"""
tests/test_lookups.py
=======================
Unit tests for report_cache/lookups.py. No DB — list_shops/get_profile are
monkeypatched directly (both are simple, already-tested-elsewhere DB reads;
what's under test here is the resolution/authorization logic built on top).
Run: cd datamind/backend && pytest tests/test_lookups.py -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_cache.lookups as lookups

_SHOPS = [
    {"shop_id": "1072", "shop_name": "Colombo Fort"},
    {"shop_id": "1088", "shop_name": "Kandy Branch"},
    {"shop_id": "1099", "shop_name": "Colombo Nugegoda"},
]


def _patch_shops(monkeypatch, shops=_SHOPS):
    monkeypatch.setattr(lookups, "list_shops", lambda tenant_id: shops)


# ── resolve_shop ─────────────────────────────────────────────────────────────

def test_resolve_shop_exact_id(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.resolve_shop("t1", "1088") == "1088"


def test_resolve_shop_exact_name_case_insensitive(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.resolve_shop("t1", "kandy branch") == "1088"


def test_resolve_shop_unique_substring(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.resolve_shop("t1", "kandy") == "1088"


def test_resolve_shop_ambiguous_substring_returns_none(monkeypatch):
    _patch_shops(monkeypatch)
    # "colombo" matches both "Colombo Fort" and "Colombo Nugegoda"
    assert lookups.resolve_shop("t1", "colombo") is None


def test_resolve_shop_no_match_returns_none(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.resolve_shop("t1", "Galle") is None


def test_resolve_shop_empty_text_returns_none(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.resolve_shop("t1", "") is None


def test_resolve_shop_no_shops_returns_none(monkeypatch):
    _patch_shops(monkeypatch, shops=[])
    assert lookups.resolve_shop("t1", "Colombo") is None


# ── is_shop_allowed ──────────────────────────────────────────────────────────

def test_is_shop_allowed_all_is_always_true(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.is_shop_allowed("t1", "all") is True
    assert lookups.is_shop_allowed("t1", "All") is True
    assert lookups.is_shop_allowed("t1", "") is True


def test_is_shop_allowed_owned_shop_true(monkeypatch):
    _patch_shops(monkeypatch)
    assert lookups.is_shop_allowed("t1", "1072") is True


def test_is_shop_allowed_foreign_shop_false(monkeypatch):
    _patch_shops(monkeypatch)
    # a shop_id belonging to a DIFFERENT tenant must not be authorized here
    assert lookups.is_shop_allowed("t1", "9999") is False


# ── currency_symbol ──────────────────────────────────────────────────────────

def test_currency_symbol_from_profile(monkeypatch):
    monkeypatch.setattr(lookups, "get_profile", lambda tenant_id: {"currency_symbol": "Rs."})
    assert lookups.currency_symbol("t1") == "Rs."


def test_currency_symbol_defaults_when_no_profile(monkeypatch):
    monkeypatch.setattr(lookups, "get_profile", lambda tenant_id: None)
    assert lookups.currency_symbol("t1") == "$"


def test_currency_symbol_defaults_when_blank(monkeypatch):
    monkeypatch.setattr(lookups, "get_profile", lambda tenant_id: {"currency_symbol": ""})
    assert lookups.currency_symbol("t1") == "$"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
