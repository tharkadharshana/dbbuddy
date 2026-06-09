"""
providers/salesplay/canonical_metrics.py
=========================================
Canonical metric definitions for SalesPlay POS data.

This module is the single source of truth for how DataMind computes each
SalesPlay metric. The definitions here mirror how SalesPlay's own dashboard
computes the same numbers. Any code that generates SQL for SalesPlay data
(LLM prompts, analytics templates, reconciliation checks) must derive its
logic from the constants below — never independently guess the formula.

WHY THIS EXISTS:
  SalesPlay excludes VOID receipts from all revenue/transaction figures, stores
  timestamps in UTC while reporting in the shop's local timezone, and uses
  receipt-level totals (not line-item sums) for store revenue. An LLM that
  doesn't know these rules will produce plausible-looking but wrong SQL.
"""

# ── Metric calculation rules injected into the LLM system prompt ─────────────
#
# Appended verbatim to query_to_sql()'s system prompt when tenant_id is set.
# Written as explicit directives so the LLM cannot guess differently.
#
# {timezone} is a format placeholder filled in by main.py at request time
# using the value from sp_shops.timezone for this tenant.

SALESPLAY_LLM_RULES = """
SALESPLAY REPORT RULES — apply these exactly to match SalesPlay's official dashboard:

1. VOID receipts must ALWAYS be excluded from revenue and transaction counts.
   Every query on sp_receipts that involves money or counts must include:
   WHERE status = 'COMPLETED'
   Never use AVG(total_money) — use SUM(total_money)/COUNT(*) instead so VOIDs
   cannot skew the average.

2. "Total Sales" or "Revenue" = SUM(total_money) FROM sp_receipts WHERE status = 'COMPLETED'.
   Do NOT sum sp_receipt_line_items.total_money for store-level revenue — use the
   receipt total. Line-item totals are only for product/category breakdowns.

3. total_discount is already deducted from total_money in sp_receipts.
   Do NOT subtract it again. total_money is the final amount paid.

4. Date filtering: sp_receipts.created_at is stored in UTC.
   SalesPlay reports show data in the shop's local timezone ({timezone}).
   Always wrap date comparisons with CONVERT_TZ:
     DATE(CONVERT_TZ(created_at, '+00:00', '{timezone}'))
   For "today": DATE(CONVERT_TZ(created_at, '+00:00', '{timezone}')) = CURDATE()
   For relative ranges: use CONVERT_TZ consistently on both sides.

5. sp_customers has pre-aggregated columns — use them directly:
   total_spent (lifetime revenue), total_visits (order count),
   last_purchase_date (most recent completed purchase).
   Do NOT recompute these via JOIN to sp_receipts.

6. For product-level revenue breakdowns, use sp_receipt_line_items.total_money
   filtered by status = 'COMPLETED' via JOIN to sp_receipts on receipt_id.
""".strip()


# ── Internal consistency checks run after every sync ─────────────────────────
#
# These queries run against OUR own sp_* tables (not the SalesPlay API).
# They catch transformation bugs in our sync pipeline, not Salesplay API drift.
# Each entry: (check_name, sql_template, failure_threshold, description)
#
# {tenant_id} is substituted at runtime.

CONSISTENCY_CHECKS = [
    (
        "orphaned_line_items",
        # Line items that reference a receipt_id that doesn't exist in sp_receipts.
        # Indicates a partial/interrupted sync where receipts were missed.
        """
        SELECT COUNT(*) AS cnt
        FROM sp_receipt_line_items li
        WHERE li.tenant_id = '{tenant_id}'
          AND NOT EXISTS (
              SELECT 1 FROM sp_receipts r
              WHERE r.tenant_id = '{tenant_id}'
                AND r.id = li.receipt_id
          )
        """,
        0,  # zero orphans expected
        "Line items with no matching receipt (incomplete sync)",
    ),
    (
        "unexpected_receipt_status",
        # Receipts with a status value other than COMPLETED or VOID.
        # SalesPlay only uses these two — any other value signals a parsing bug.
        """
        SELECT COUNT(*) AS cnt
        FROM sp_receipts
        WHERE tenant_id = '{tenant_id}'
          AND status NOT IN ('COMPLETED', 'VOID')
          AND status IS NOT NULL
        """,
        0,  # zero unexpected statuses expected
        "Receipts with unrecognised status (parsing error)",
    ),
    (
        "revenue_line_vs_receipt_divergence",
        # Large divergence between SUM of line-item totals and SUM of receipt
        # totals for COMPLETED receipts signals a transformation bug.
        # Returns the absolute difference as a percentage of receipt total.
        # A small gap (<2%) is acceptable due to rounding; >5% is a bug.
        """
        SELECT
          ROUND(ABS(
            COALESCE((
              SELECT SUM(li.total_money)
              FROM sp_receipt_line_items li
              JOIN sp_receipts r ON r.id = li.receipt_id
                AND r.tenant_id = '{tenant_id}'
              WHERE r.status = 'COMPLETED'
                AND li.tenant_id = '{tenant_id}'
            ), 0)
            -
            COALESCE((
              SELECT SUM(total_money)
              FROM sp_receipts
              WHERE tenant_id = '{tenant_id}'
                AND status = 'COMPLETED'
            ), 0)
          ) / NULLIF((
            SELECT SUM(total_money)
            FROM sp_receipts
            WHERE tenant_id = '{tenant_id}'
              AND status = 'COMPLETED'
          ), 0) * 100, 2) AS divergence_pct
        """,
        5.0,  # flag if line-item vs receipt total diverges by more than 5%
        "Line-item total vs receipt total divergence > 5% (transformation bug)",
    ),
]
