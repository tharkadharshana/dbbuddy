"""
mcp_server/sql_guard.py
========================
AST-based SQL safety for the run_select_query FALLBACK path (doc 06 F4, PLAN 08
Step 2). Replaces the regex table-allowlist + tenant-isolation on the MCP tool
path with a real parse (sqlglot), which the regex versions can't match for:
  - UNION / set operations — every branch is scoped, not just the first WHERE
    (this is the doc 06 F1 cross-tenant bypass, fixed *correctly* rather than by
    rejecting UNION outright);
  - CTEs, derived tables, and subqueries — tenant predicates land in the right
    scope; a CTE name is recognised as a table, not flagged as unknown;
  - INTO OUTFILE / file smuggling — such statements don't even parse as a
    read-only SELECT and are rejected.

Two entry points, both raising plain ValueError on violation (so FastMCP surfaces
it to the model as a correctable tool error, matching mcp_server/safety.py):
  assert_safe_select(sql, allowed_tables)          — validate; no mutation, single
                                                     query, every real table allowlisted
  enforce_tenant_ast(sql, tenant_id, prefixes)     — return SQL with a tenant
                                                     predicate on every shared-table
                                                     ref in every scope

The regex guards in safety.py remain as a cheap first line (block_mutations) and
for the legacy one-shot path; this module is the authoritative check for
arbitrary model-authored SQL.
"""

from typing import Iterable

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope

_DIALECT = "mysql"
_SHARED_PREFIXES = ("sp_", "ly_")

# Expression types that make a statement more than a read-only query.
_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create,
    exp.Alter, exp.TruncateTable, exp.Command, exp.Into, exp.Set,
)


def _parse_single(sql: str):
    try:
        statements = [s for s in sqlglot.parse(sql, read=_DIALECT) if s is not None]
    except Exception as exc:
        # Unparseable (e.g. INTO OUTFILE, vendor file syntax) → treat as unsafe.
        raise ValueError(f"Could not parse SQL safely: {exc}")
    if len(statements) != 1:
        raise ValueError("Only a single SQL statement is allowed.")
    return statements[0]


def _is_shared(table_name: str, prefixes: Iterable[str]) -> bool:
    name = (table_name or "").lower()
    return any(name.startswith(p) for p in prefixes)


def assert_safe_select(sql: str, allowed_tables: Iterable[str]) -> None:
    """Raise ValueError unless `sql` is a single read-only query (SELECT / set
    operation) whose every real table is in `allowed_tables`. CTE names are
    recognised and exempt (their inner tables are still checked)."""
    stmt = _parse_single(sql)

    if not isinstance(stmt, exp.Query):
        raise ValueError("Only read-only SELECT queries are allowed.")
    for node in stmt.walk():
        node = node[0] if isinstance(node, tuple) else node
        if isinstance(node, _FORBIDDEN_NODES):
            raise ValueError("Only read-only SELECT queries are allowed.")

    allowed = {t.strip("`").lower() for t in allowed_tables}
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names:
            continue
        if name not in allowed:
            raise ValueError(
                f"Query references table '{name}' which is not available to this account."
            )


def _reject_foreign_tenant(stmt, tenant_id: str) -> None:
    """Prompt-injection guard (ports the regex version's SEC-15 check): a
    `tenant_id = '<other>'` literal anywhere is a cross-tenant request."""
    for eq in stmt.find_all(exp.EQ):
        col = eq.this
        val = eq.expression
        if isinstance(col, exp.Column) and col.name.lower() == "tenant_id" \
                and isinstance(val, exp.Literal) and val.is_string and val.this != tenant_id:
            raise ValueError(
                f"Query references tenant_id '{val.this}' which does not match your account. "
                "Cross-tenant queries are not allowed."
            )


def _already_scoped(select: exp.Select, alias: str, tenant_id: str) -> bool:
    where = select.args.get("where")
    if not where:
        return False
    for eq in where.find_all(exp.EQ):
        col, val = eq.this, eq.expression
        if not (isinstance(col, exp.Column) and col.name.lower() == "tenant_id"):
            continue
        if not (isinstance(val, exp.Literal) and val.this == tenant_id):
            continue
        table = (col.table or "").lower()
        if table == alias.lower() or table == "":   # alias-qualified or bare tenant_id
            return True
    return False


def enforce_tenant_ast(sql: str, tenant_id: str, prefixes: Iterable[str] = _SHARED_PREFIXES) -> str:
    """Return `sql` with `<alias>.tenant_id = '<tenant_id>'` ANDed into the WHERE
    of every scope that references a shared (sp_*/ly_*) table. Idempotent —
    scopes already carrying the correct tenant predicate are left alone. Raises
    if a different tenant_id literal is present (injection guard)."""
    if not tenant_id:
        return sql
    tree = _parse_single(sql)
    _reject_foreign_tenant(tree, tenant_id)

    for scope in build_scope(tree).traverse():
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        for _, source in scope.sources.items():
            if not (isinstance(source, exp.Table) and _is_shared(source.name, prefixes)):
                continue
            alias = source.alias_or_name
            if _already_scoped(select, alias, tenant_id):
                continue
            select.where(f"{alias}.tenant_id = '{tenant_id}'",
                         append=True, dialect=_DIALECT, copy=False)

    return tree.sql(dialect=_DIALECT)
