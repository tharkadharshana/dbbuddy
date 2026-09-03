"""
mcp_server/business_tools.py
=============================
The Business-Data MCP server: 4 tools an LLM can call to look at a merchant's
own data before answering, instead of writing one blind SQL query and hoping
it's right (see docs/04_MCP_Architecture_And_Implementation_Guide.md and
docs/05_MCP_Worked_Example_And_Comparison.md for the full rationale).

SECURITY — read before changing anything here:

A fresh FastMCP instance is built PER REQUEST by `build_business_mcp(ctx)`,
with all 4 tools closing over that request's already-authenticated
`ToolContext`. `tenant_id` is never a tool parameter the model can see or
set — it's baked into the closure server-side, exactly like the SEC-15 guard
already requires for the legacy one-shot query path. This is deliberate: the
architecture doc's own literal example takes tenant_id as a plain tool
argument, which the doc's own "Part 6" section flags as a risk. Building a
new instance per request (cheap — it's just function closures) instead of a
long-lived shared instance means there is no code path where one request's
tools could ever see another request's tenant_id.

This module is never reached over a network socket: main.py's orchestrator
talks to it via FastMCP's in-memory Client transport only. There is no
HTTP mount for this server (see mcp_server/orchestrator.py's module docstring
for why).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from fastmcp import FastMCP

import db as _db
from llm import _SENSITIVE_COL_RE, _SP_INTERNAL_COLS, _filter_sensitive_schema
from . import safety

# Hard ceilings independent of whatever the model asks for — keep tool
# responses small (cost/latency) regardless of model behavior.
_MAX_SAMPLE_ROWS = 10
_DEFAULT_SAMPLE_ROWS = 5


@dataclass
class ToolContext:
    """Everything the 4 tools need for one request. Built fresh per request
    in orchestrator.answer_business_question — never reused across requests.

    `schemas` should be the raw, unfiltered introspected schema (table ->
    column list) for the tables this user/tenant may see — get_schema()
    below filters out sensitive/internal columns itself (defense in depth:
    it never trusts a caller to have done that already, e.g. a standalone
    script building a ToolContext directly for testing)."""
    conn: Any
    schemas: Dict[str, List[Dict[str, Any]]]
    fkeys: List[Dict[str, str]]
    tenant_id: Optional[str]                    # None for non-integration (own-DB) users
    row_limit: int
    history_months: int
    set_query_timeout: Callable[[Any], None]    # main.py's _set_query_timeout, reused as-is
    # Populated by run_select_query on every successful call (overwritten each
    # time) so the orchestrator can recover the final columns/data/sql after
    # the tool-calling loop ends, without needing the model to repeat them in prose.
    last_result: Optional[Dict[str, Any]] = field(default=None)
    # Set by the export_data tool when the merchant asks for a file. Carries
    # the rows already in last_result out to the response so the browser can
    # build the file — nothing is written to disk and nothing is stored.
    export_request: Optional[Dict[str, Any]] = field(default=None)


def _strip_hidden_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Remove security-sensitive and internal-routing columns from tool output —
    the same filter llm.py already applies to schema text before it reaches the
    prompt (see llm._filter_sensitive_schema), applied here to actual row data
    since get_sample_rows/run_select_query return real values, not just column names."""
    if not rows:
        return rows
    hidden = set(_SP_INTERNAL_COLS)
    out = []
    for row in rows:
        out.append({
            k: v for k, v in row.items()
            if k not in hidden and not _SENSITIVE_COL_RE.search(k)
        })
    return out


def build_business_mcp(ctx: ToolContext) -> FastMCP:
    mcp = FastMCP("datamind-business-data")

    @mcp.tool()
    def get_schema() -> dict:
        """Return the table and column structure available for this merchant's
        data, with sensitive columns (passwords, tokens, card numbers) and
        internal routing columns already filtered out. Call this first if you
        don't already know what tables/columns exist — never guess a table or
        column name without having seen it here or in a previous tool result."""
        tables = {}
        for table, cols in _filter_sensitive_schema(ctx.schemas).items():
            tables[table] = {
                "description": _db._TABLE_DESCRIPTIONS.get(table),
                "columns": [{"name": c["name"], "type": c["type"]} for c in cols],
            }
        relationships = [
            f"{fk['table']}.{fk['column']} -> {fk['ref_table']}.{fk['ref_column']}"
            for fk in ctx.fkeys
            if fk["table"] in ctx.schemas and fk["ref_table"] in ctx.schemas
        ]
        return {"tables": tables, "relationships": relationships}

    @mcp.tool()
    def get_sample_rows(table: str, limit: int = _DEFAULT_SAMPLE_ROWS) -> list:
        """Return a few example rows from one table, scoped to this merchant
        only. Use this BEFORE writing a filter condition, to see the real
        format/casing of values (e.g. how a status column is actually spelled
        — 'COMPLETED' vs 'completed'), or before a JOIN to confirm the column
        names actually exist. `table` must be one of the tables from get_schema."""
        if table not in ctx.schemas:
            raise ValueError(f"Unknown table '{table}'. Call get_schema first to see available tables.")
        capped = max(1, min(int(limit), _MAX_SAMPLE_ROWS))
        sql = f"SELECT * FROM `{table}` LIMIT {capped}"
        # Fail-closed: a shared sp_*/ly_* table must never be read without a
        # tenant_id to scope it, no matter why tenant_id ended up missing —
        # this refuses loudly instead of silently returning cross-tenant rows.
        if safety.references_shared_tables(sql) and not ctx.tenant_id:
            raise ValueError(
                "Refusing to read this table without a tenant scope — this would expose other accounts' data."
            )
        if ctx.tenant_id:
            sql = safety.enforce_tenant_isolation(sql, ctx.tenant_id)
            if ctx.tenant_id not in sql:
                raise ValueError("Could not safely scope this table to your account.")
        try:
            result = _db.run_select_and_format(ctx.conn, sql, set_timeout=ctx.set_query_timeout)
        except Exception as db_err:
            raise ValueError(f"Could not read sample rows: {db_err}") from db_err
        return _strip_hidden_columns(result["data"])

    @mcp.tool()
    def run_select_query(sql: str) -> list:
        """Run one read-only SQL SELECT query against this merchant's data and
        return the results as rows. Automatically scoped to this merchant's
        data and to their plan's allowed history window — do not add your own
        tenant filter, it is applied for you. Only reference tables you saw in
        get_schema. If the result is empty or looks wrong for a question that
        should have data, you may call this again with a corrected query
        before giving your final answer — for example, check get_sample_rows
        first if a filter value might not match the real stored format."""
        safety.block_mutations(sql)
        safety.enforce_table_allowlist(sql, ctx.schemas.keys())
        # Fail-closed: a query touching shared sp_*/ly_* tables must never
        # run without a tenant_id to scope it, no matter why tenant_id
        # ended up missing (e.g. an upstream caller bug) — refuse loudly
        # instead of silently returning every tenant's data.
        if safety.references_shared_tables(sql) and not ctx.tenant_id:
            raise ValueError(
                "Refusing to run this query without a tenant scope — this would expose other accounts' data."
            )
        if ctx.tenant_id:
            sql = safety.enforce_tenant_isolation(sql, ctx.tenant_id)
            if ctx.tenant_id not in sql:
                raise ValueError("Could not safely scope this query to your account.")
            sql = safety.enforce_date_filter(sql, ctx.history_months)
        try:
            result = _db.run_select_and_format(ctx.conn, sql, set_timeout=ctx.set_query_timeout)
        except ValueError:
            raise
        except Exception as db_err:
            # Surfaced as a tool error the model can see and react to (e.g. a
            # syntax error or unknown column) — this is the actual "self
            # correct" capability the tool-calling loop exists for.
            raise ValueError(f"Query failed: {db_err}") from db_err

        data = _strip_hidden_columns(result["data"])
        if len(data) > ctx.row_limit:
            data = data[:ctx.row_limit]
        ctx.last_result = {"sql": sql, "columns": result["columns"], "data": data}
        return data

    @mcp.tool()
    def get_date_range() -> dict:
        """Return the history window (in months) this merchant's plan allows
        querying. Use this before writing a query that covers 'all time' or a
        long date range, to know the actual boundary — queries are
        automatically clipped to this window regardless, but knowing it up
        front avoids a wasted attempt."""
        return {"months": ctx.history_months}

    return mcp
