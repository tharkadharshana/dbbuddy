"""Brand derivation for the identity migration.

The migration stamps every existing user with a partner_key. Getting that wrong
is not recoverable from data -- a mislabelled user lands under the wrong brand
and, once account_key is generated, points at the wrong rows. So the rule here
refuses far more often than it guesses.
"""
import importlib.util
import os

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "migrate_multi_brand_identity",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "migrate_multi_brand_identity.py"),
)
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)


class FakeCursor:
    """Answers only the two SELECTs resolve_brands issues, by shape."""

    def __init__(self, partners, users):
        self._partners = partners      # [(partner_key, provider_id)]
        self._users = users            # [(email, provider_id or None)]
        self._rows = []

    def execute(self, sql, params=None):
        if "FROM embed_partners" in sql:
            self._rows = list(self._partners)
        elif "FROM users u" in sql:
            self._rows = list(self._users)
        else:
            raise AssertionError("unexpected query: " + sql)

    def fetchall(self):
        return self._rows


def test_single_brand_per_provider_maps_cleanly():
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay")],
        users=[("owner@shop.com", "salesplay"), ("two@shop.com", "salesplay")],
    )
    mapping, orphans = mig.resolve_brands(cur, None)
    assert mapping == {"owner@shop.com": "sp_live_a", "two@shop.com": "sp_live_a"}
    assert orphans == []


def test_two_brands_on_one_provider_is_refused():
    """The window for this migration closes the moment a second brand exists."""
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay"), ("sl_live_b", "salesplay")],
        users=[("owner@shop.com", "salesplay")],
    )
    with pytest.raises(SystemExit) as e:
        mig.resolve_brands(cur, None)
    assert "no longer a function" in str(e.value)


def test_user_with_no_integration_is_refused_without_an_explicit_key():
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay")],
        users=[("direct@shop.com", None)],
    )
    with pytest.raises(SystemExit) as e:
        mig.resolve_brands(cur, None)
    assert "no derivable brand" in str(e.value)


def test_orphans_take_the_explicit_key():
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay")],
        users=[("direct@shop.com", None), ("owner@shop.com", "salesplay")],
    )
    mapping, orphans = mig.resolve_brands(cur, "sp_live_a")
    assert mapping["direct@shop.com"] == "sp_live_a"
    assert orphans == ["direct@shop.com"]


def test_unknown_orphan_key_is_refused():
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay")],
        users=[("direct@shop.com", None)],
    )
    with pytest.raises(SystemExit) as e:
        mig.resolve_brands(cur, "sl_live_typo")
    assert "not a known partner_key" in str(e.value)


def test_provider_without_a_partner_row_is_refused():
    """A user synced from a provider nobody registered cannot be placed."""
    cur = FakeCursor(
        partners=[("sp_live_a", "salesplay")],
        users=[("owner@shop.com", "loyverse")],
    )
    with pytest.raises(SystemExit) as e:
        mig.resolve_brands(cur, None)
    assert "no partner row exists" in str(e.value)


def test_pos_data_tables_are_never_migrated():
    """sp_customers.email and sp_shops.email are merchant data, not identity."""
    assert "sp_customers" not in mig.CHILD_TABLES
    assert "sp_shops" not in mig.CHILD_TABLES
    assert "users" not in mig.CHILD_TABLES


def test_account_key_width_covers_the_longest_possible_value():
    # partner_key(64) + ':' + RFC-max address(254)
    assert mig.ACCOUNT_KEY_LEN >= 64 + 1 + 254
