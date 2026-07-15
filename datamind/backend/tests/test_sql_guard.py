"""
tests/test_sql_guard.py — PLAN 08 Step 2 AST SQL safety (doc 06 F4).

The regressions that the regex guard could not handle: UNION scoped per-branch,
CTEs allowed, subquery tenant injection, INTO OUTFILE rejected.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import sql_guard

ALLOWED = {"sp_receipts", "sp_products", "sp_shops"}


# ── assert_safe_select ────────────────────────────────────────────────────────

def test_allows_plain_select():
    sql_guard.assert_safe_select("SELECT total FROM sp_receipts", ALLOWED)


def test_allows_cte_and_checks_inner_tables():
    sql_guard.assert_safe_select(
        "WITH x AS (SELECT total FROM sp_receipts) SELECT * FROM x", ALLOWED)


def test_allows_union_when_all_tables_allowed():
    sql_guard.assert_safe_select(
        "SELECT a FROM sp_receipts UNION SELECT b FROM sp_products", ALLOWED)


def test_rejects_unknown_table():
    with pytest.raises(ValueError):
        sql_guard.assert_safe_select("SELECT password FROM users", ALLOWED)


def test_rejects_union_smuggling_unknown_table():
    # doc 06 F1: the second branch must also be allowlisted
    with pytest.raises(ValueError):
        sql_guard.assert_safe_select(
            "SELECT a FROM sp_receipts UNION SELECT secret FROM auth_tokens", ALLOWED)


def test_rejects_into_outfile():
    with pytest.raises(ValueError):
        sql_guard.assert_safe_select(
            "SELECT a FROM sp_receipts INTO OUTFILE '/tmp/x'", ALLOWED)


def test_rejects_mutation():
    with pytest.raises(ValueError):
        sql_guard.assert_safe_select("DELETE FROM sp_receipts", ALLOWED)


def test_rejects_multi_statement():
    with pytest.raises(ValueError):
        sql_guard.assert_safe_select("SELECT 1 FROM sp_receipts; DROP TABLE sp_receipts", ALLOWED)


# ── enforce_tenant_ast ────────────────────────────────────────────────────────

def test_scopes_from_table():
    out = sql_guard.enforce_tenant_ast("SELECT total FROM sp_receipts", "t1")
    assert "sp_receipts.tenant_id = 't1'" in out


def test_scopes_both_join_tables():
    out = sql_guard.enforce_tenant_ast(
        "SELECT a FROM sp_receipts r JOIN sp_products p ON r.pid = p.id", "t1")
    assert "r.tenant_id = 't1'" in out and "p.tenant_id = 't1'" in out


def test_scopes_both_union_branches():
    out = sql_guard.enforce_tenant_ast(
        "SELECT a FROM sp_receipts UNION SELECT b FROM sp_products", "t1")
    assert out.count("tenant_id = 't1'") == 2


def test_scopes_subquery():
    out = sql_guard.enforce_tenant_ast("SELECT * FROM (SELECT a FROM sp_receipts) t", "t1")
    assert "sp_receipts.tenant_id = 't1'" in out


def test_idempotent():
    once = sql_guard.enforce_tenant_ast("SELECT a FROM sp_receipts", "t1")
    twice = sql_guard.enforce_tenant_ast(once, "t1")
    assert once == twice


def test_rejects_foreign_tenant_literal():
    with pytest.raises(ValueError):
        sql_guard.enforce_tenant_ast("SELECT a FROM sp_receipts WHERE tenant_id = 'other'", "t1")


def test_non_shared_table_left_alone():
    # a query over only allowlisted non-sp_ tables gets no tenant predicate
    out = sql_guard.enforce_tenant_ast("SELECT 1", "t1")
    assert "tenant_id" not in out
