"""
tests/test_profile.py
=======================
Unit tests for report_cache/profile.py:map_profile — pure function, no DB/network.
Run: cd datamind/backend && pytest tests/test_profile.py -q
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache.profile import map_profile

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _load_fixture():
    with open(os.path.join(FIXTURES_DIR, "profile_sample.json"), encoding="utf-8") as f:
        return json.load(f)


def test_map_profile_shops():
    raw = _load_fixture()
    _, shop_rows, _ = map_profile(raw)

    assert len(shop_rows) == 3
    assert {"shop_id": "1072", "shop_name": "Colombo Fort"} in shop_rows
    assert {"shop_id": "1088", "shop_name": "Kandy Branch"} in shop_rows
    assert {"shop_id": "1099", "shop_name": "Colombo Nugegoda"} in shop_rows


def test_map_profile_cashiers():
    raw = _load_fixture()
    _, _, cashier_rows = map_profile(raw)

    assert len(cashier_rows) == 2
    assert {"cashier_id": "5", "cashier_name": "Nimal Perera"} in cashier_rows
    assert {"cashier_id": "6", "cashier_name": "Kamal Silva"} in cashier_rows


def test_map_profile_currency_and_number_format():
    raw = _load_fixture()
    profile_row, _, _ = map_profile(raw)

    assert profile_row["currency"] == "Rs."
    assert profile_row["currency_symbol"] == "Rs."
    assert profile_row["number_format"] == {
        "profile_currency": "Rs.",
        "number_of_decimel": 2,
        "decimal_separator": ".",
        "thousond_separator": ",",
    }


def test_map_profile_timezone_ui_language_master_username():
    raw = _load_fixture()
    profile_row, _, _ = map_profile(raw)

    assert profile_row["timezone"] == "Asia/Colombo"
    assert profile_row["ui_language"] == "en_US"
    assert profile_row["master_username"] == "merchant@example.com"


def test_map_profile_stores_full_raw_payload():
    raw = _load_fixture()
    profile_row, _, _ = map_profile(raw)

    assert profile_row["profile_json"] == raw


def test_map_profile_does_not_read_tier_from_payload():
    """PLAN_02's core warning: the POS profile carries the merchant's POS
    back-office subscription, which must never be mistaken for the AI tier.
    map_profile must not surface anything tier-shaped from access_info."""
    raw = _load_fixture()
    profile_row, _, _ = map_profile(raw)

    assert "subscription_tier" not in profile_row
    assert "history_months" not in profile_row
    assert "access_info" not in profile_row


def test_map_profile_defaults_on_missing_fields():
    raw = {"user": {}, "shop_list": [], "cashier_list": []}
    profile_row, shop_rows, cashier_rows = map_profile(raw)

    assert profile_row["currency"] == ""
    assert profile_row["timezone"] == "UTC"
    assert profile_row["ui_language"] == "en_US"
    assert profile_row["master_username"] == ""
    assert shop_rows == []
    assert cashier_rows == []


def test_map_profile_skips_shops_and_cashiers_missing_ids():
    raw = {
        "user": {},
        "shop_list": [{"name": "No ID Shop"}, {"location_id": 1, "name": "Valid"}],
        "cashier_list": [{"name": "No ID Cashier"}, {"user_id": "9", "name": "Valid"}],
    }
    _, shop_rows, cashier_rows = map_profile(raw)

    assert shop_rows == [{"shop_id": "1", "shop_name": "Valid"}]
    assert cashier_rows == [{"cashier_id": "9", "cashier_name": "Valid"}]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
