# AI DB user — least-privilege check (doc 06 F1/F2, PLAN 08 Step 1)

The account the AI uses to run model-authored `run_select_query` SQL must be
**read-only** and have **no `FILE` privilege** (so `INTO OUTFILE` / `LOAD_FILE`
can't exfiltrate even if a guard is bypassed) and **no access to auth/billing
tables**. The SQL guards (`mcp_server/safety.py`, `mcp_server/sql_guard.py`) are
the application-layer defense; this is the database-layer backstop — defense in
depth, so a single missed guard is not a breach.

## Verify the current grants

```sql
SHOW GRANTS FOR CURRENT_USER();           -- run as the AI's DB user
```

Expected: `SELECT` only on the analytics schema/tables (the `sp_*` shared tables
and the `report_cache*`/`tenant_*` tables), and **no** `FILE`, `INSERT`,
`UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `GRANT`, or `SUPER`.

Red flags to remove:
- `GRANT ALL PRIVILEGES ON *.* ...` — far too broad.
- `FILE` anywhere in the global grants — enables OUTFILE/LOAD_FILE.
- `SELECT` on `user_integrations`, `subscription_*`, `embed_partners`, auth
  tables — the AI query path must never reach credentials/billing.

## Create a dedicated read-only user (if the app currently runs as root)

```sql
CREATE USER 'datamind_ro'@'%' IDENTIFIED BY '<strong-password>';
-- analytics surface only:
GRANT SELECT ON datamind.sp_receipts            TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_receipt_line_items  TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_products            TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_customers           TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_categories          TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_shops               TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.sp_payment_types       TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.report_daily_fact      TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.report_dim_fact        TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.tenant_profile         TO 'datamind_ro'@'%';
GRANT SELECT ON datamind.tenant_shop            TO 'datamind_ro'@'%';
FLUSH PRIVILEGES;
```

Note: the report-cache **write** paths (ingestion jobs, `store.py`) and the app's
own tables need a separate, writable connection — this read-only user is only for
the model-authored `run_select_query` fallback surface. Wiring a second pooled
connection for that surface is a follow-up; this doc records the required end state
and the verification query so the check is not forgotten.
