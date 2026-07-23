"""
test_mcp_tools.py
==================
Standalone smoke test for the Business-Data MCP tools (mcp_server/business_tools.py),
run directly against a real DB — no HTTP, no LLM call, no full FastAPI app boot.

This is the "test before wiring into real chat traffic" step from
docs/04_MCP_Architecture_And_Implementation_Guide.md Part 4.9, adapted to this
repo's script-based testing convention (see qa_e2e.py) instead of the MCP
Inspector — this MCP server has no HTTP transport at all (see
mcp_server/orchestrator.py's module docstring for why), so Inspector can't
point at it; this script drives the same in-memory Client the real
orchestrator uses.

Usage (from datamind/backend/, with a working DATAMIND_DB_*/.env):
  python scripts/test_mcp_tools.py --tenant-id sp_<your_test_prefix>
  python scripts/test_mcp_tools.py --own-db          # test against DB_* env config instead

Exercises all 4 tools, then 3 adversarial cases that MUST be rejected:
  - a mutation (DELETE) in run_select_query              -> is_error
  - a cross-tenant literal in run_select_query            -> is_error (tenant mode only)
  - a table not present in get_schema's output            -> is_error
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ on sys.path

from dotenv import load_dotenv
load_dotenv()

from fastmcp import Client

import db
from mcp_server.business_tools import ToolContext, build_business_mcp

# Mirrors main.py's _SALESPLAY_SHARED_TABLES — kept in sync manually since
# this script intentionally doesn't import main.py (avoids booting the full
# FastAPI app just to run a tool smoke test).
_SALESPLAY_SHARED_TABLES = [
    "sp_receipts", "sp_receipt_line_items", "sp_products",
    "sp_customers", "sp_categories", "sp_shops", "sp_payment_types",
]


def _noop_timeout(cursor):
    pass


def _ok(msg):
    print(f"[OK]   {msg}")


def _fail(msg):
    print(f"[FAIL] {msg}")


def _report_rejected(result, label):
    (_ok if result.is_error else _fail)(
        f"{label} correctly rejected" if result.is_error else f"{label} was NOT rejected!"
    )


async def main(tenant_id, own_db):
    if own_db:
        conn = db.get_connection()  # uses DB_* env vars
        tables = None
    else:
        from pool import get_internal_conn
        conn = get_internal_conn()
        tables = _SALESPLAY_SHARED_TABLES

    schemas = db.get_table_schemas(conn, tables)
    fkeys = db.get_foreign_keys(conn)
    if not any(schemas.values()):
        print("No tables/columns found — check --tenant-id or your DB connection settings.")
        return

    ctx = ToolContext(
        conn=conn, schemas=schemas, fkeys=fkeys, tenant_id=tenant_id,
        row_limit=500, history_months=12, set_query_timeout=_noop_timeout,
    )
    mcp = build_business_mcp(ctx)

    async with Client(mcp) as client:
        tools = await client.list_tools()
        print(f"Tools exposed: {[t.name for t in tools]}\n")

        r = await client.call_tool("get_schema", {}, raise_on_error=False)
        if not r.is_error and r.data.get("tables"):
            _ok(f"get_schema returned {len(r.data['tables'])} tables")
        else:
            _fail(f"get_schema: {r.content}")

        table = next(iter(schemas.keys()), None)
        if table:
            r = await client.call_tool("get_sample_rows", {"table": table, "limit": 3}, raise_on_error=False)
            if not r.is_error:
                _ok(f"get_sample_rows({table}) returned {len(r.data)} rows")
            else:
                _fail(f"get_sample_rows: {r.content}")

            r = await client.call_tool("run_select_query", {"sql": f"SELECT * FROM {table} LIMIT 3"}, raise_on_error=False)
            if not r.is_error:
                _ok(f"run_select_query returned {len(r.data)} rows (sql used: {ctx.last_result['sql'][:100]})")
            else:
                _fail(f"run_select_query: {r.content}")

        r = await client.call_tool("get_date_range", {}, raise_on_error=False)
        (_ok if not r.is_error else _fail)(f"get_date_range -> {r.data if not r.is_error else r.content}")

        print("\n-- Adversarial cases (all of these MUST be rejected) --")

        r = await client.call_tool("run_select_query", {"sql": "DELETE FROM sp_receipts"}, raise_on_error=False)
        _report_rejected(r, "mutation attempt")

        if tenant_id:
            r = await client.call_tool(
                "run_select_query",
                {"sql": "SELECT * FROM sp_receipts WHERE tenant_id = 'someone-elses-tenant'"},
                raise_on_error=False,
            )
            _report_rejected(r, "cross-tenant literal")

        r = await client.call_tool(
            "run_select_query", {"sql": "SELECT * FROM this_table_does_not_exist"}, raise_on_error=False
        )
        _report_rejected(r, "unlisted table reference")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", help="SalesPlay tenant_id/table_prefix to test against (e.g. sp_abc123)")
    parser.add_argument("--own-db", action="store_true", help="Test against the .env DB_* config instead of a SalesPlay tenant")
    args = parser.parse_args()
    if not args.tenant_id and not args.own_db:
        parser.error("Provide --tenant-id <prefix> or --own-db")
    asyncio.run(main(args.tenant_id, args.own_db))
