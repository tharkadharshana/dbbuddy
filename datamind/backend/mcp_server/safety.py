"""
mcp_server/safety.py
====================
The SQL safety passes shared by both query paths:

  1. The legacy one-shot NL->SQL path in main.py (`_guard_sql`,
     `_enforce_tenant_isolation`, `_enforce_date_filter` there are thin
     wrappers that delegate to the functions below).
  2. The MCP business-data tools (business_tools.py), which run these same
     checks on every `run_select_query` tool call.

Keeping exactly one implementation means the two paths can never quietly
drift apart on what counts as "safe" SQL.

All checks are AST-based (sqlglot), not regex. That closes the classic regex
holes: a UNION's second branch gets tenant-scoped too, subqueries and CTEs are
scoped in the right place, `INTO OUTFILE` doesn't parse as a read-only SELECT,
a table name is a real table reference (not a keyword mis-captured as an
alias), and a string literal containing the word "update" is not a mutation.
SQL that sqlglot cannot parse is rejected outright — fail closed: if we can't
prove it's a single read-only SELECT, it doesn't run (it would fail in MySQL
anyway or, worse, be hiding vendor syntax like OUTFILE/HANDLER).

Every function here raises plain `ValueError` on a safety violation — no
FastAPI dependency, so these can be called from MCP tool code that isn't
running inside a request (and FastMCP surfaces a raised exception as a tool
error the model can see and react to, which is exactly what we want for the
"self-correct" tool-calling loop).
"""

import sqlglot
from sqlglot import exp

_DIALECT = "mysql"
_SHARED_PREFIXES = ("sp_", "ly_")

# Statement/expression types that make a query more than a read-only SELECT.
# exp.Command is sqlglot's fallback for statements it recognises but doesn't
# fully parse (GRANT, CALL, HANDLER, LOCK TABLES, ...) — all disallowed.
# exp.Into catches SELECT ... INTO (OUTFILE/DUMPFILE/variable).
_FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create,
    exp.Alter, exp.TruncateTable, exp.Command, exp.Into, exp.Set,
)

_TRANSACTIONAL_TABLES = frozenset({
    "sp_receipts", "sp_receipt_line_items", "ly_receipts", "ly_receipt_line_items",
})


def _parse_single(sql: str):
    """Parse `sql` as exactly one MySQL statement, or raise ValueError."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=_DIALECT) if s is not None]
    except Exception as exc:
        raise ValueError(f"Query could not be parsed safely: {exc}")
    if len(statements) != 1:
        raise ValueError("Only a single SQL statement is allowed.")
    return statements[0]


def _walk(node):
    """sqlglot walk() yields nodes (or (node, parent, key) tuples on old versions)."""
    for item in node.walk():
        yield item[0] if isinstance(item, tuple) else item


def _cte_names(stmt) -> set:
    return {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}


def _real_tables(stmt):
    """Every physical table referenced anywhere in the statement (CTE names excluded)."""
    ctes = _cte_names(stmt)
    for table in stmt.find_all(exp.Table):
        if table.name and table.name.lower() not in ctes:
            yield table


def _is_shared(name: str) -> bool:
    return (name or "").lower().startswith(_SHARED_PREFIXES)


def block_mutations(sql: str) -> None:
    """SEC-04: raise ValueError unless `sql` is a single read-only query
    (SELECT, including UNION/CTE forms). Anything else — mutations, DDL,
    multi-statement, SELECT INTO OUTFILE, unparseable vendor syntax — raises."""
    stmt = _parse_single(sql)
    if not isinstance(stmt, exp.Query):
        raise ValueError("Only read-only SELECT queries are allowed.")
    for node in _walk(stmt):
        if isinstance(node, _FORBIDDEN_NODES):
            raise ValueError(
                f"Query contains a disallowed statement: {node.key.upper()}"
            )


def references_shared_tables(sql: str) -> bool:
    """True if the SQL touches any sp_*/ly_* shared multi-tenant table.

    Used as a fail-closed guard: a caller with no tenant_id must never be
    allowed to run a query against these tables, no matter why tenant_id
    ended up missing. Unparseable SQL returns True (fail closed)."""
    try:
        stmt = _parse_single(sql)
    except ValueError:
        return True
    return any(_is_shared(t.name) for t in _real_tables(stmt))


def enforce_table_allowlist(sql: str, allowed_tables) -> None:
    """
    Defense-in-depth for the MCP tool-calling path: raise ValueError if the SQL
    references any physical table that isn't one the model was actually shown
    via get_schema. CTE names are exempt (their inner tables are still checked).
    """
    stmt = _parse_single(sql)
    allowed = {t.strip("`").lower() for t in allowed_tables}
    for table in _real_tables(stmt):
        if table.name.lower() not in allowed:
            raise ValueError(
                f"Query references table '{table.name}' which is not available to this account."
            )


def _tenant_predicate(alias: str, tenant_id: str) -> exp.EQ:
    return exp.EQ(
        this=exp.column("tenant_id", table=alias),
        expression=exp.Literal.string(tenant_id),
    )


def enforce_tenant_isolation(sql: str, tenant_id: str) -> str:
    """
    SEC-15: Server-side tenant isolation enforcement for shared sp_*/ly_* tables.

    The LLM is instructed to add WHERE tenant_id = '...' but sometimes omits it,
    adds it only for the primary table, or (prompt injection) asks for a
    different tenant's data. This rewrites the parsed AST so that EVERY shared
    table reference in EVERY scope — FROM, JOINs, subqueries, CTE bodies, each
    UNION branch — carries `alias.tenant_id = '<tenant>'`:

      - FROM tables get the predicate AND-ed into that SELECT's WHERE;
      - JOINed tables get it AND-ed into their ON clause (so LEFT JOIN
        semantics are preserved).

    Any tenant_id compared to a literal other than the caller's raises
    ValueError (cross-tenant request), and the unconditional injection makes a
    smuggled foreign filter harmless anyway (it just ANDs to an empty result).
    Returns the rewritten SQL.
    """
    if not tenant_id:
        return sql
    stmt = _parse_single(sql)

    # Reject any tenant_id = '<other tenant>' literal anywhere in the query.
    for node in stmt.find_all(exp.EQ):
        col, lit = node.this, node.expression
        if isinstance(lit, exp.Column) and isinstance(col, exp.Literal):
            col, lit = lit, col
        if (isinstance(col, exp.Column) and col.name.lower() == "tenant_id"
                and isinstance(lit, exp.Literal) and lit.is_string
                and lit.this != tenant_id):
            raise ValueError(
                f"Query references tenant_id '{lit.this}' which does not match "
                f"your account. Cross-tenant queries are not allowed."
            )

    ctes = _cte_names(stmt)

    for select in stmt.find_all(exp.Select):
        from_clause = select.args.get("from")
        if from_clause is not None:
            table = from_clause.this
            if (isinstance(table, exp.Table) and _is_shared(table.name)
                    and table.name.lower() not in ctes):
                select.where(_tenant_predicate(table.alias_or_name, tenant_id), copy=False)
        for join in select.args.get("joins") or []:
            table = join.this
            if (isinstance(table, exp.Table) and _is_shared(table.name)
                    and table.name.lower() not in ctes):
                pred = _tenant_predicate(table.alias_or_name, tenant_id)
                existing_on = join.args.get("on")
                join.set("on", exp.and_(existing_on, pred) if existing_on is not None else pred)

    return stmt.sql(dialect=_DIALECT)


def enforce_date_filter(sql: str, history_months: int) -> str:
    """
    Enforce the plan's data-history window on every NL query for integration users.

    Only applies when the query touches a transactional table (sp_receipts /
    sp_receipt_line_items / ly_receipts / ly_receipt_line_items). Reference
    tables (products, categories, shops, customers, payment_types) are never
    date-filtered — their created_at reflects record creation in the POS, not
    a transaction date, and for products it's often NULL entirely.

    The plan bound is ALWAYS AND-ed in, on top of whatever the model wrote:
    every transactional table reference gets
    `alias.created_at >= DATE_SUB(CURDATE(), INTERVAL n MONTH)` added to its
    SELECT's WHERE clause. Returns the rewritten SQL.

    This used to return early — "if the query already filters on created_at
    anywhere, it is trusted as-is" — which meant a model that wrote its own
    `created_at >= '2019-01-01'` silently escaped the plan window entirely.
    That is a billing-integrity hole, not just an answer-quality one: a
    model-supplied bound is never a substitute for the plan bound. ANDing both
    is always correct — the narrower of the two wins.
    """
    if not history_months:
        return sql
    stmt = _parse_single(sql)

    if not any(t.name.lower() in _TRANSACTIONAL_TABLES for t in _real_tables(stmt)):
        return sql

    months = int(history_months)
    for select in stmt.find_all(exp.Select):
        sources = []
        from_clause = select.args.get("from")
        if from_clause is not None:
            sources.append(from_clause.this)
        sources.extend(j.this for j in select.args.get("joins") or [])
        for table in sources:
            if isinstance(table, exp.Table) and table.name.lower() in _TRANSACTIONAL_TABLES:
                cond = sqlglot.parse_one(
                    f"{table.alias_or_name}.created_at >= "
                    f"DATE_SUB(CURDATE(), INTERVAL {months} MONTH)",
                    read=_DIALECT,
                )
                select.where(cond, copy=False)

    return stmt.sql(dialect=_DIALECT)
