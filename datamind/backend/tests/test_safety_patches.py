"""
tests/test_safety_patches.py — PLAN 08 Step 1 (doc 06 F1/F2) regressions on the
regex guards used by the legacy one-shot SQL path.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import safety


# ── F2: file/lock primitives blocked even inside a "SELECT" ───────────────────

@pytest.mark.parametrize("sql", [
    "SELECT * FROM sp_receipts INTO OUTFILE '/tmp/x'",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT a FROM sp_receipts INTO DUMPFILE '/tmp/x'",
    "HANDLER sp_receipts OPEN",
    "LOCK TABLES sp_receipts READ",
])
def test_block_mutations_rejects_file_and_lock(sql):
    with pytest.raises(ValueError):
        safety.block_mutations(sql)


def test_block_mutations_still_rejects_dml():
    with pytest.raises(ValueError):
        safety.block_mutations("UPDATE sp_receipts SET total = 0")


def test_block_mutations_allows_plain_select():
    safety.block_mutations("SELECT total FROM sp_receipts WHERE tenant_id = 't1'")


# ── F1: UNION / multi-statement blocked on the regex path ─────────────────────

def test_block_unsafe_rejects_union():
    with pytest.raises(ValueError):
        safety.block_unsafe_constructs(
            "SELECT a FROM sp_receipts UNION SELECT credentials_enc FROM user_integrations")


def test_block_unsafe_rejects_multi_statement():
    with pytest.raises(ValueError):
        safety.block_unsafe_constructs("SELECT 1 FROM sp_receipts; DROP TABLE sp_receipts")


def test_block_unsafe_allows_trailing_semicolon():
    safety.block_unsafe_constructs("SELECT total FROM sp_receipts;")


def test_block_unsafe_allows_plain_select():
    safety.block_unsafe_constructs("SELECT total FROM sp_receipts WHERE tenant_id = 't1'")
