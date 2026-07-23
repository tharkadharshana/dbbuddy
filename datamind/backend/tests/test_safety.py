"""
tests/test_safety.py — AST SQL safety guards (mcp_server/safety.py).

Covers the regressions the old regex guards could not handle: UNION scoped
per-branch, subquery/CTE tenant injection, INTO OUTFILE rejected, keyword
inside a string literal NOT flagged as a mutation.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import safety

TID = "dm_abc123_salesplay"


# ── block_mutations ──────────────────────────────────────────────────────────

def test_allows_plain_select():
    safety.block_mutations("SELECT total FROM sp_receipts WHERE total > 10")


def test_allows_union_and_cte():
    safety.block_mutations(
        "WITH x AS (SELECT total FROM sp_receipts) "
        "SELECT * FROM x UNION ALL SELECT total FROM sp_receipts")


@pytest.mark.parametrize("sql", [
    "UPDATE sp_receipts SET total = 0",
    "DELETE FROM sp_receipts",
    "INSERT INTO sp_receipts VALUES (1)",
    "DROP TABLE sp_receipts",
    "TRUNCATE TABLE sp_receipts",
    "ALTER TABLE sp_receipts ADD COLUMN x INT",
    "CREATE TABLE evil (id INT)",
    "GRANT ALL ON *.* TO 'x'@'%'",
])
def test_blocks_mutations(sql):
    with pytest.raises(ValueError):
        safety.block_mutations(sql)


def test_blocks_multi_statement():
    with pytest.raises(ValueError, match="single"):
        safety.block_mutations("SELECT 1; DROP TABLE sp_receipts")


def test_blocks_into_outfile():
    with pytest.raises(ValueError):
        safety.block_mutations("SELECT * FROM sp_receipts INTO OUTFILE '/tmp/x'")


def test_blocks_unparseable_garbage():
    with pytest.raises(ValueError):
        safety.block_mutations("HANDLER sp_receipts OPEN;; ???")


def test_mutation_keyword_in_string_literal_is_fine():
    # The old regex guard false-positived on this.
    safety.block_mutations("SELECT * FROM sp_products WHERE name = 'delete update drop'")


# ── references_shared_tables ─────────────────────────────────────────────────

def test_shared_table_detected():
    assert safety.references_shared_tables("SELECT * FROM sp_receipts")
    assert safety.references_shared_tables(
        "SELECT * FROM other JOIN ly_products p ON p.id = other.id")


def test_non_shared_not_detected():
    assert not safety.references_shared_tables("SELECT * FROM my_own_table")


def test_unparseable_fails_closed():
    assert safety.references_shared_tables("not really sql at all ;;")


# ── enforce_table_allowlist ──────────────────────────────────────────────────

ALLOWED = {"sp_receipts", "sp_products"}


def test_allowlist_passes_known_tables():
    safety.enforce_table_allowlist(
        "SELECT * FROM sp_receipts r JOIN sp_products p ON p.id = r.pid", ALLOWED)


def test_allowlist_rejects_unknown_table():
    with pytest.raises(ValueError, match="sp_customers"):
        safety.enforce_table_allowlist("SELECT * FROM sp_customers", ALLOWED)


def test_allowlist_rejects_unknown_table_in_subquery():
    with pytest.raises(ValueError, match="secret"):
        safety.enforce_table_allowlist(
            "SELECT * FROM sp_receipts WHERE id IN (SELECT id FROM secret)", ALLOWED)


def test_allowlist_cte_name_is_not_a_table():
    safety.enforce_table_allowlist(
        "WITH t AS (SELECT * FROM sp_receipts) SELECT * FROM t", ALLOWED)


# ── enforce_tenant_isolation ─────────────────────────────────────────────────

def _scoped(sql):
    return safety.enforce_tenant_isolation(sql, TID)


def test_tenant_added_to_bare_from():
    out = _scoped("SELECT SUM(total) FROM sp_receipts")
    assert f"sp_receipts.tenant_id = '{TID}'" in out


def test_tenant_respects_alias_and_existing_where():
    out = _scoped("SELECT SUM(r.total) FROM sp_receipts r WHERE r.total > 5")
    assert f"r.tenant_id = '{TID}'" in out
    assert "r.total > 5" in out


def test_tenant_added_to_join_on_clause():
    out = _scoped(
        "SELECT * FROM sp_receipts r LEFT JOIN sp_products p ON p.id = r.pid")
    # both tables scoped; the joined table's predicate must live in ON,
    # not WHERE, to preserve LEFT JOIN semantics
    assert f"r.tenant_id = '{TID}'" in out
    on_part = out[out.upper().index(" ON "):]
    assert f"p.tenant_id = '{TID}'" in on_part


def test_tenant_scopes_every_union_branch():
    out = _scoped(
        "SELECT total FROM sp_receipts UNION ALL SELECT total FROM ly_receipts")
    assert f"sp_receipts.tenant_id = '{TID}'" in out
    assert f"ly_receipts.tenant_id = '{TID}'" in out


def test_tenant_scopes_subquery():
    out = _scoped(
        "SELECT * FROM sp_products WHERE id IN (SELECT pid FROM sp_receipt_line_items)")
    assert f"sp_products.tenant_id = '{TID}'" in out
    assert f"sp_receipt_line_items.tenant_id = '{TID}'" in out


def test_tenant_scopes_cte_body_not_cte_name():
    out = _scoped("WITH t AS (SELECT total FROM sp_receipts) SELECT * FROM t")
    assert f"sp_receipts.tenant_id = '{TID}'" in out
    assert "t.tenant_id" not in out


def test_foreign_tenant_literal_rejected():
    with pytest.raises(ValueError, match="Cross-tenant"):
        _scoped("SELECT * FROM sp_receipts WHERE tenant_id = 'dm_someone_else'")


def test_own_tenant_literal_accepted():
    out = _scoped(f"SELECT * FROM sp_receipts WHERE tenant_id = '{TID}'")
    assert f"'{TID}'" in out


def test_non_shared_tables_untouched():
    sql = "SELECT * FROM my_own_table WHERE x = 1"
    assert "tenant_id" not in safety.enforce_tenant_isolation(sql, TID)


def test_no_tenant_id_returns_sql_unchanged():
    sql = "SELECT * FROM sp_receipts"
    assert safety.enforce_tenant_isolation(sql, "") == sql


# ── enforce_date_filter ──────────────────────────────────────────────────────

def test_date_filter_injected_on_transactional_table():
    out = safety.enforce_date_filter("SELECT SUM(total) FROM sp_receipts", 3)
    assert "INTERVAL '3' MONTH" in out  # sqlglot quotes the number — valid MySQL
    assert "sp_receipts.created_at >=" in out


def test_date_filter_uses_alias():
    out = safety.enforce_date_filter("SELECT SUM(r.total) FROM sp_receipts r", 12)
    assert "r.created_at >=" in out


def test_date_filter_applies_to_joined_transactional_table():
    out = safety.enforce_date_filter(
        "SELECT * FROM sp_products p JOIN sp_receipt_line_items li ON li.pid = p.id", 3)
    assert "li.created_at >=" in out
    assert "p.created_at" not in out  # reference tables are never date-filtered


def test_date_filter_trusts_existing_created_at():
    sql = "SELECT * FROM sp_receipts WHERE created_at >= '2026-01-01'"
    assert safety.enforce_date_filter(sql, 3) == sql


def test_date_filter_skips_reference_tables():
    sql = "SELECT * FROM sp_products"
    assert safety.enforce_date_filter(sql, 3) == sql


def test_date_filter_zero_months_noop():
    sql = "SELECT * FROM sp_receipts"
    assert safety.enforce_date_filter(sql, 0) == sql


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
