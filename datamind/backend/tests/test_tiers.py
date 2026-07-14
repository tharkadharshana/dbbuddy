"""
tests/test_tiers.py
=====================
Unit tests for report_cache/tiers.py. No DB/network — get_tenant_user_email
and billing.get_plan_history_limit are monkeypatched.
Run: cd datamind/backend && pytest tests/test_tiers.py -q
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import report_cache.tiers as tiers


def _patch_email(monkeypatch, email):
    monkeypatch.setattr(tiers, "get_tenant_user_email", lambda tenant_id: email)


def _patch_plan(monkeypatch, plan_name, months, cutoff):
    monkeypatch.setattr(
        tiers, "_plan_history_limit",
        lambda user_email: {"plan_name": plan_name, "months": months, "cutoff_date": cutoff, "row_limit": 0},
    )


def test_get_ai_tier_starter_maps_to_basic(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")
    _patch_plan(monkeypatch, "Starter", 3, date(2026, 4, 1))
    assert tiers.get_ai_tier("sp_tenant1") == "basic"


def test_get_ai_tier_growth_maps_to_standard(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")
    _patch_plan(monkeypatch, "Growth", 12, date(2025, 7, 1))
    assert tiers.get_ai_tier("sp_tenant1") == "standard"


def test_get_ai_tier_pro_maps_to_unlimited(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")
    _patch_plan(monkeypatch, "Pro", 200, date(2010, 1, 1))
    assert tiers.get_ai_tier("sp_tenant1") == "unlimited"


def test_get_ai_tier_no_tenant_email_defaults_basic(monkeypatch):
    _patch_email(monkeypatch, None)
    assert tiers.get_ai_tier("sp_unknown_tenant") == "basic"


def test_get_ai_tier_billing_error_defaults_basic(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")

    def _raise(user_email):
        raise RuntimeError("billing DB unavailable")
    monkeypatch.setattr(tiers, "_plan_history_limit", _raise)

    assert tiers.get_ai_tier("sp_tenant1") == "basic"


def test_history_months_for_matches_billing(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")
    _patch_plan(monkeypatch, "Growth", 12, date(2025, 7, 1))
    assert tiers.history_months_for("sp_tenant1") == 12


def test_history_months_for_no_email_defaults_3(monkeypatch):
    _patch_email(monkeypatch, None)
    assert tiers.history_months_for("sp_unknown_tenant") == 3


def test_window_start_delegates_to_billing_cutoff(monkeypatch):
    _patch_email(monkeypatch, "user@example.com")
    cutoff = date(2026, 1, 15)
    _patch_plan(monkeypatch, "Pro", 200, cutoff)
    assert tiers.window_start("sp_tenant1") == cutoff


def test_window_start_no_email_falls_back_to_3mo(monkeypatch):
    _patch_email(monkeypatch, None)
    today = date(2026, 7, 14)
    result = tiers.window_start("sp_unknown_tenant", today=today)
    # 3 months * 30 days fallback (see tiers._DEFAULT_MONTHS)
    assert result == today - __import__("datetime").timedelta(days=90)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
