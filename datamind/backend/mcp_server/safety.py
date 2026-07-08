"""
mcp_server/safety.py
====================
The SQL safety passes shared by both query paths:

  1. The legacy one-shot NL->SQL path in main.py (`_guard_sql`,
     `_enforce_tenant_isolation`, `_enforce_date_filter` there are now thin
     wrappers that delegate to the functions below).
  2. The MCP business-data tools (business_tools.py), which run these same
     checks on every `run_select_query` tool call.

Keeping exactly one implementation means the two paths can never quietly
drift apart on what counts as "safe" SQL.

Every function here raises plain `ValueError` on a safety violation — no
FastAPI dependency, so these can be called from MCP tool code that isn't
running inside a request (and FastMCP surfaces a raised exception as a tool
error the model can see and react to, which is exactly what we want for the
"self-correct" tool-calling loop).

`block_mutations`, `enforce_tenant_isolation`, and `enforce_date_filter` are
ported unchanged (same regexes, same injection strategy) from main.py's
SEC-04 / SEC-15 guards — see main.py's docstrings for the full rationale.
`enforce_table_allowlist` is new: defense-in-depth for the MCP tool-calling
surface, which lets the model type arbitrary table names across several
calls (more autonomy than the legacy path's single blind SQL guess).
"""

import re


# SQL keywords that can never be a table alias. The LLM often omits aliases,
# so the FROM/JOIN regex captures the next keyword (e.g. WHERE, JOIN, ON) as
# the alias, which then produces invalid SQL like WHERE.tenant_id = '...'.
# Both enforcers below guard against this by falling back to the table name
# when the captured alias is one of these.
_SQL_KEYWORDS = frozenset({
    'WHERE', 'GROUP', 'ORDER', 'LIMIT', 'HAVING', 'ON', 'SET',
    'INNER', 'LEFT', 'RIGHT', 'CROSS', 'FULL', 'JOIN', 'UNION',
    'SELECT', 'FROM', 'AND', 'OR', 'NOT', 'IN', 'IS', 'NULL',
    'AS', 'BY', 'ASC', 'DESC', 'WITH', 'USING',
})

_SQL_MUTATION_RE = re.compile(
    r'\b(DROP|DELETE|INSERT|UPDATE|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|REVOKE|CALL|EXEC)\b',
    re.IGNORECASE,
)

# Matches any FROM/JOIN reference to a table, capturing its (optional) alias.
_TABLE_REF_RE = re.compile(
    r'\b(FROM|(?:INNER|LEFT|RIGHT|CROSS|FULL)?\s*JOIN)\s+'
    r'(`?\w+`?)'
    r'(?:\s+(?:AS\s+)?(`?\w+`?))?',
    re.IGNORECASE,
)

# Same as above but restricted to sp_*/ly_* shared tables — used by the
# tenant-isolation enforcer, which only ever needs to scope those.
_SHARED_TABLE_REF_RE = re.compile(
    r'\b(FROM|(?:INNER|LEFT|RIGHT|CROSS|FULL)?\s*JOIN)\s+'
    r'(`?(?:sp|ly)_\w+`?)'
    r'(?:\s+(?:AS\s+)?(`?\w+`?))?',
    re.IGNORECASE,
)


def block_mutations(sql: str) -> None:
    """SEC-04: raise ValueError if the SQL contains anything other than a read-only SELECT."""
    m = _SQL_MUTATION_RE.search(sql)
    if m:
        raise ValueError(f"Query contains a disallowed statement: {m.group(0).upper()}")


def enforce_table_allowlist(sql: str, allowed_tables) -> None:
    """
    Defense-in-depth for the MCP tool-calling path: raise ValueError if the SQL
    references any FROM/JOIN table that isn't one the model was actually shown
    via get_schema. The legacy one-shot path only ever sees a schema already
    filtered to the user's allowed tables in one blind guess; the tool-calling
    loop gives the model several independent chances to type a table name, so
    this closes the gap explicitly rather than relying on the model only ever
    reusing names it previously saw.
    """
    allowed = {t.strip('`').lower() for t in allowed_tables}
    for m in _TABLE_REF_RE.finditer(sql):
        table = m.group(2).strip('`').lower()
        if table not in allowed:
            raise ValueError(
                f"Query references table '{table}' which is not available to this account."
            )


def enforce_tenant_isolation(sql: str, tenant_id: str) -> str:
    """
    SEC-15: Server-side tenant isolation enforcement for shared sp_*/ly_* tables.

    The LLM is instructed to add WHERE tenant_id = '...' but sometimes omits it
    or adds it only for the primary table while leaving JOINed tables unscoped.
    This function post-processes the generated SQL to guarantee every shared-table
    reference is filtered to the correct tenant BEFORE execution.

    Strategy:
      1. Find all sp_*/ly_* table references with their aliases (FROM and JOIN).
      2. For each alias not already scoped with alias.tenant_id = '...':
         - If it's the FROM table: inject into the WHERE clause (or create one).
         - If it's a JOINed table: inject AND alias.tenant_id = '...' into the ON clause.
      3. Raise ValueError if ANY other tenant_id literal appears in the SQL
         (prevents prompt-injection attacks requesting another tenant's data).
    """
    if not tenant_id:
        return sql

    safe_tid = tenant_id.replace("'", "\\'")
    expected_literal = f"'{safe_tid}'"

    # Security: reject if a *different* tenant_id literal appears in the SQL.
    tid_val_re = re.compile(r"tenant_id\s*=\s*'([^']*)'", re.IGNORECASE)
    for m in tid_val_re.finditer(sql):
        found_tid = m.group(1)
        if found_tid != tenant_id:
            raise ValueError(
                f"Query references tenant_id '{found_tid}' which does not match "
                f"your account. Cross-tenant queries are not allowed."
            )

    refs = []
    for m in _SHARED_TABLE_REF_RE.finditer(sql):
        clause = m.group(1).strip().upper()
        clause_type = "FROM" if clause == "FROM" else "JOIN"
        table_raw = m.group(2).strip('`')
        captured_alias = (m.group(3) or "").strip('`')
        alias_raw = table_raw if (not captured_alias or captured_alias.upper() in _SQL_KEYWORDS) else captured_alias
        refs.append((alias_raw, clause_type, m.end()))

    if not refs:
        return sql  # No shared tables referenced — nothing to enforce.

    already_scoped = set()
    for alias, _, _ in refs:
        pattern = re.compile(
            rf'\b{re.escape(alias)}\.tenant_id\s*=\s*{re.escape(expected_literal)}',
            re.IGNORECASE,
        )
        if pattern.search(sql):
            already_scoped.add(alias)

    bare_scoped = bool(re.search(
        rf'\btenant_id\s*=\s*{re.escape(expected_literal)}', sql, re.IGNORECASE
    ))
    from_alias = next((a for a, ct, _ in refs if ct == "FROM"), None)
    if bare_scoped and from_alias:
        already_scoped.add(from_alias)

    unscoped = [(a, ct, pos) for (a, ct, pos) in refs if a not in already_scoped]
    if not unscoped:
        return sql

    # Step 1: Inject AND alias.tenant_id = '...' into each JOIN ON clause.
    # Process in reverse order so injections don't shift positions.
    for alias, clause_type, match_end in sorted(unscoped, key=lambda x: x[2], reverse=True):
        if clause_type != "JOIN":
            continue
        on_re = re.compile(r'\bON\b', re.IGNORECASE)
        on_m = on_re.search(sql, match_end)
        if on_m:
            end_re = re.compile(
                r'\b(?:INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|CROSS\s+JOIN|JOIN|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b',
                re.IGNORECASE,
            )
            end_m = end_re.search(sql, on_m.end())
            insert_at = end_m.start() if end_m else len(sql)
            inject = f" AND {alias}.tenant_id = {expected_literal}"
            sql = sql[:insert_at].rstrip() + inject + " " + sql[insert_at:].lstrip()

    # Step 2: Inject tenant_id into WHERE clause for FROM table (if unscoped).
    from_unscoped = [a for a, ct, _ in unscoped if ct == "FROM"]
    if from_unscoped:
        alias = from_unscoped[0]
        cond = f"{alias}.tenant_id = {expected_literal}"
        where_re = re.compile(r'\bWHERE\b', re.IGNORECASE)
        where_m = where_re.search(sql)
        if where_m:
            insert_at = where_m.end()
            sql = sql[:insert_at] + f" {cond} AND " + sql[insert_at:].lstrip()
        else:
            end_re = re.compile(r'\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b', re.IGNORECASE)
            end_m = end_re.search(sql)
            if end_m:
                sql = sql[:end_m.start()] + f"WHERE {cond} " + sql[end_m.start():]
            else:
                sql = sql.rstrip(';').rstrip() + f" WHERE {cond}"

    return sql


def enforce_date_filter(sql: str, history_months: int) -> str:
    """
    Enforce the plan's data-history window on every NL query for integration users.

    Only applies when the query touches a transactional table (sp_receipts /
    sp_receipt_line_items / ly_receipts / ly_receipt_line_items). Reference
    tables (products, categories, shops, customers, payment_types) are never
    date-filtered — their created_at reflects record creation in the POS, not
    a transaction date, and for products it's often NULL entirely.
    """
    if not history_months:
        return sql

    _TRANSACTIONAL = re.compile(
        r'\b(sp_receipts|sp_receipt_line_items|ly_receipts|ly_receipt_line_items)\b',
        re.IGNORECASE,
    )
    if not _TRANSACTIONAL.search(sql):
        return sql

    if re.search(r'\bcreated_at\s*[><=]', sql, re.IGNORECASE):
        return sql  # LLM already applied a date filter on created_at — trust it.

    table_re = re.compile(
        r'\bFROM\s+(`?(?:sp|ly)_\w+`?)(?:\s+(?:AS\s+)?(`?\w+`?))?',
        re.IGNORECASE,
    )
    m = table_re.search(sql)
    if not m:
        return sql  # no shared table found — nothing to inject

    alias_candidate = m.group(2)
    if alias_candidate and alias_candidate.strip('`').upper() in _SQL_KEYWORDS:
        alias_candidate = None
    alias = (alias_candidate or m.group(1)).strip('`')
    date_cond = f"{alias}.created_at >= DATE_SUB(CURDATE(), INTERVAL {history_months} MONTH)"

    where_re = re.compile(r'\bWHERE\b', re.IGNORECASE)
    where_m = where_re.search(sql)
    if where_m:
        insert_at = where_m.end()
        return sql[:insert_at] + f" {date_cond} AND " + sql[insert_at:].lstrip()

    end_re = re.compile(r'\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT)\b', re.IGNORECASE)
    end_m = end_re.search(sql)
    if end_m:
        return sql[:end_m.start()] + f"WHERE {date_cond} " + sql[end_m.start():]

    return sql.rstrip(';').rstrip() + f" WHERE {date_cond}"
