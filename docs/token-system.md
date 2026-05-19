# DataMind Token System — Technical Reference

Internal reference for how Token charging works, where calculations happen in the code,
and what gaps exist. Keep this up to date when the billing model changes.

---

## Core Formula

Every chargeable operation produces one row in `usage_log` computed as:

```
Tokens = T_llm + T_db + T_feature

T_llm     = llm_tokens_used / 1000
T_db      = rows_counted / 1000
T_feature = FEATURE_COST[operation_type]

Minimum per operation: 0.1 Tokens
```

**Source:** `billing.py` → `calculate_tokens(operation_type, llm_tokens, rows_returned)`

---

## Feature Cost Table

Defined in `billing.py` as `FEATURE_COST`. The flat compute cost added on top of
data volume and LLM cost for each operation type.

| `operation_type`      | `T_feature` | Triggered by |
|-----------------------|-------------|--------------|
| `nl_query_rows`       | 0.0         | NL query result rows (LLM billed separately via `llm`) |
| `prebuilt_template`   | 1.0         | SQL template — cache, fallback, or integration |
| `rfm_analysis`        | 1.5         | Python RFM customer segmentation |
| `cohort_analysis`     | 1.5         | Python cohort retention |
| `basket_analysis`     | 2.0         | Python market basket (cross-join heavy) |
| `growth_metrics`      | 1.0         | Python growth metrics |
| `employee_performance`| 1.0         | Python cashier/employee performance |
| `product_velocity`    | 1.0         | Python product velocity |
| `payment_breakdown`   | 0.5         | Python payment method analysis |
| `location_comparison` | 0.5         | Python location comparison |
| `forecast`            | 2.0         | Prophet ML fit + predict |
| `anomaly_detection`   | 2.0         | IsolationForest over full dataset |
| `llm`                 | 0.0         | Raw LLM calls — cost is entirely T_llm |

> **Default fallback:** any unknown `operation_type` charges 0.5 via `FEATURE_COST.get(op, 0.5)`.

---

## Worked Examples

### Example 1 — NL Query (small table)

User asks: *"Show top 10 products by revenue this month"*

- LLM generates SQL: **800 LLM tokens** used
- Query returns **10 rows**

```
T_llm     = 800 / 1000  = 0.8
T_db      = 10 / 1000   = 0.01
T_feature = 0.0         (nl_query_rows)

Total = max(0.81, 0.1)  = 0.81 Tokens
```

Two `usage_log` rows are written:
- `llm` — 0.8 Tokens (from `charge_ai_usage` → `charge_tokens`)
- `nl_query_rows` — 0.1 Tokens (minimum, since 0.01 < 0.1)

---

### Example 2 — NL Query (large table)

Same question on a table with 200,000 rows. LLM still uses 800 tokens but query returns 5,000 rows.

```
T_llm     = 800 / 1000    = 0.8
T_db      = 5000 / 1000   = 5.0
T_feature = 0.0

Total = 5.8 Tokens
```

Same question, 7× more expensive due to data volume.

---

### Example 3 — Prebuilt Template (own-DB, no LLM)

User runs "Revenue Trend" from Analytics Hub. Result has 24 rows (monthly aggregates).

```
T_llm     = 0
T_db      = 24 / 1000   = 0.024
T_feature = 1.0         (prebuilt_template)

Total = max(1.024, 0.1) = 1.024 Tokens
```

---

### Example 4 — Prebuilt Template (large integration dataset)

Same "Revenue Trend" but against a SalesPlay integration with 180,000 synced rows.
The SQL aggregates them but still returns 24 rows.

```
T_llm     = 0
T_db      = 24 / 1000   = 0.024    ← result rows, not scanned rows
T_feature = 1.0

Total = 1.024 Tokens
```

> **Note:** We charge result rows, not scanned rows. A heavy aggregation query that
> scans 180,000 rows but returns 24 looks the same as scanning 24. See **Gap #3**.

---

### Example 5 — Forecasting

User runs auto-forecast. Prophet fetches 365 daily data points.

```
T_llm     = 0
T_db      = 365 / 1000  = 0.365   ← input rows fed to ML model
T_feature = 2.0

Total = 2.365 Tokens
```

Forecast charges **input rows** (the historical data fed to the model), not output rows
(the forecast points returned to the user).

---

### Example 6 — Report Generation

User generates a 3-section report. The LLM processes 4,200 tokens for the narrative.

```
T_llm     = 4200 / 1000 = 4.2
T_db      = 0            ← section data rows not charged
T_feature = 0.0         (llm)

Total = 4.2 Tokens
```

> **Note:** Report section data rows are not charged. See **Gap #2**.

---

## LLM Token Flow

```
call_llm()
  └── returns (response_text, llm_tokens_used)
        └── charge_ai_usage(user_email, llm_tokens, model, endpoint)
              ├── INSERT INTO llm_usage_log  (LLM audit trail)
              └── charge_tokens(user_email, credits_charged, "llm", llm_tokens=tokens)
                    ├── INSERT INTO usage_log
                    └── UPDATE subscription_usage SET tokens_used += N
```

**Conversion:**
```python
credits_charged = round(llm_tokens / 1000 * ai_credit_rate, 4)
# ai_credit_rate: stored in billing_config, default 1.0
# Raising ai_credit_rate makes LLM ops more expensive without changing the user-facing formula
```

`ai_credit_rate` is the internal margin lever for LLM operations only.
It has no effect on T_db or T_feature.

---

## Where `_charge_op` Is Called (main.py)

Every endpoint that produces data rows must call `_charge_op` before returning:

```python
def _charge_op(email: str, op_type: str, rows: int):
    tokens = calculate_tokens(op_type, rows_returned=rows)
    charge_tokens(email, tokens, op_type, rows_returned=rows)
```

| Endpoint | `op_type` | `rows` value |
|---|---|---|
| `POST /query` | `nl_query_rows` | `len(data)` — result rows |
| `POST /analytics/run` (cache) | from `_ANALYTICS_OP` | `result["row_count"]` |
| `POST /analytics/run` (python) | from `_ANALYTICS_OP` | `result["row_count"]` |
| `POST /analytics/run` (fallback) | from `_ANALYTICS_OP` | `result["row_count"]` |
| `POST /analytics/run` (integration) | from `_ANALYTICS_OP` | `result["row_count"]` |
| `POST /integrations/{id}/analytics/run` | from `_ANALYTICS_OP` | `result["row_count"]` |
| `POST /forecast` | `forecast` | `len(rows)` — input rows |
| `GET /forecast/auto` (provider) | `forecast` | `len(rows)` |
| `GET /forecast/auto` (own-DB) | `forecast` | `len(rows)` |
| `POST /integrations/{id}/forecast` | `forecast` | `len(rows)` |
| `POST /anomalies` | `anomaly_detection` | `len(rows)` |
| `GET /anomalies/auto` (provider) | `anomaly_detection` | `len(rows)` |
| `GET /anomalies/auto` (own-DB) | `anomaly_detection` | `len(rows)` |

`_ANALYTICS_OP` maps template IDs to operation types:
```python
_ANALYTICS_OP = {
    "customer_rfm":        "rfm_analysis",
    "customer_cohort":     "cohort_analysis",
    "basket_analysis":     "basket_analysis",
    "growth_metrics":      "growth_metrics",
    "cashier_performance": "employee_performance",
    "product_velocity":    "product_velocity",
    "payment_methods":     "payment_breakdown",
    "location_comparison": "location_comparison",
    # anything not listed → default "prebuilt_template"
}
```

---

## Data History Limits

Applied as a `WHERE date_col >= cutoff_date` clause before the query runs.
If no date column is available, a `LIMIT` is applied on the result instead.

| Plan | Window | Row fallback |
|---|---|---|
| Starter | 1 month (30 days) | LIMIT 1,000 |
| Growth | 3 months (90 days) | LIMIT 3,000 |
| Pro | 12 months (365 days) | LIMIT 12,000 |

**Source:** `billing.py` → `get_plan_history_limit(user_email)`

Applied at: `/forecast`, `/forecast/auto`, `/anomalies`, `/anomalies/auto`,
`/integrations/{id}/forecast`, NL query system prompt, `/analytics/run` result truncation.

---

## Plan Token Limits

Stored in `subscription_plans.tokens_limit`. Seeded by `bootstrap_billing_tables`.

| Plan | `tokens_limit` | Price |
|---|---|---|
| Starter | 500 | $5/mo |
| Growth | 1,500 | $10/mo |
| Pro | 10,000 | $25/mo |

Add-on packs: 50 Tokens for $1. Add-on balance = `ai_addon_balance + (db_addon_balance / 1000)`.
Add-on Tokens are consumed after plan Tokens are exhausted and never expire.

---

## Database Schema

### `usage_log` — unified per-operation audit trail

```sql
id             INT AUTO_INCREMENT PRIMARY KEY
user_email     VARCHAR(255) NOT NULL
tokens         DECIMAL(12,4) NOT NULL DEFAULT 0
operation_type VARCHAR(50)  NOT NULL
llm_tokens     INT          NOT NULL DEFAULT 0   -- raw LLM tokens (0 for non-LLM ops)
rows_charged   INT          NOT NULL DEFAULT 0   -- rows counted in T_db
created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
```

### `subscription_usage` — running total per billing period

```sql
id           INT AUTO_INCREMENT PRIMARY KEY
user_email   VARCHAR(255)  NOT NULL
period_start DATE          NOT NULL
tokens_used  DECIMAL(12,4) NOT NULL DEFAULT 0
UNIQUE KEY (user_email, period_start)
```

Incremented via:
```sql
INSERT INTO subscription_usage (user_email, period_start, tokens_used)
VALUES (?, ?, ?)
ON DUPLICATE KEY UPDATE tokens_used = tokens_used + ?
```

### `llm_usage_log` — LLM-specific audit trail (kept for detail)

```sql
id              INT AUTO_INCREMENT PRIMARY KEY
user_email      VARCHAR(255)  NOT NULL
tokens          INT           NOT NULL DEFAULT 0   -- raw LLM tokens
model           VARCHAR(50)
endpoint        VARCHAR(255)
credits_charged DECIMAL(10,4) NOT NULL DEFAULT 0
created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
```

---

## Limit Enforcement Flow

```
check_ai_limit(user_email)
  └── get_user_subscription(user_email)
        ├── _process_subscription()  -- auto-expire if period_end < today
        ├── SELECT tokens_used FROM subscription_usage
        └── SELECT tokens_limit FROM subscription_plans
  └── if tokens_used >= tokens_limit → return (False, "You've used all your tokens")
  └── fails open on any DB error (never hard-blocks due to billing DB issues)
```

Feature gates run before the token limit check:

```
check_plan_feature(user_email, "forecast")
  └── SELECT plan_name FROM user_subscriptions JOIN subscription_plans
  └── if plan_name not in {"Growth", "Pro"} → return (False, "Upgrade to Growth...")

check_plan_feature(user_email, "anomaly_detection")  → same, Growth+ only
check_plan_feature(user_email, "external_api")        → Pro only
```

---

## Gaps

### Gap 1 — Integration sync is not in the Token system

`POST /providers/{id}/sync` goes through `check_db_limit()` which checks
`db_base_used < db_total_available`. This is a separate parallel system:

- `db_base_used` = live COUNT of rows in the user's synced tables via `get_user_total_rows()`
- `db_total_available` = `subscription_plans.db_rows + db_addon_balance`

Syncing 500,000 rows costs **zero Tokens**. The sync row limit and the Token balance
are completely independent. A user can exhaust their sync quota while having 100%
Tokens remaining, and vice versa.

**To fix:** Call `charge_tokens(email, rows_synced/1000, "integration_sync", rows_returned=rows_synced)`
at the end of `_run_sync()` in `integrations.py`.

---

### Gap 2 — Report section rows are not charged

`POST /report` fetches data for each section via `_run_sql()` but only charges T_llm
for the narrative. A 10-section report pulling 5,000 rows per section is billed the same
as a 1-sentence summary with no data.

**To fix:** Sum `row_count` across all sections and call `_charge_op(email, "report_section", total_rows)`
before the final return in the `/report` endpoint.

---

### Gap 3 — Result rows charged, not scanned rows

For all analytics templates, `T_db` is based on the rows *returned* to the user,
not the rows the DB engine had to scan. A `GROUP BY` aggregation scanning 500,000 rows
that returns 12 rows costs the same as a query that natively has 12 rows.

This intentionally keeps billing simple and predictable. It does understate the actual
DB infrastructure cost for heavy aggregation queries on large datasets.

**To fix (if needed):** Use `cursor.rowcount` or `EXPLAIN` output to capture scanned rows
instead of `len(data)`. More complex to implement and harder to explain to users.

---

### Gap 4 — `ai_credit_rate` only multiplies T_llm

The `billing_config.ai_credit_rate` multiplier (default 1.0) only applies to the LLM token
conversion. There is no equivalent rate lever for T_db or T_feature. Adjusting pricing for
data-heavy operations requires changing `FEATURE_COST` values directly in `billing.py`
and restarting the server.

**To fix (if needed):** Add `db_credit_rate` and `feature_credit_rate` multipliers to
`billing_config` and apply them in `calculate_tokens()`.

---

### Gap 5 — Cache build LLM cost is unverifiable

The one-time schema cache build charges LLM tokens via `charge_ai_usage` in a background thread.
If the build fails mid-way, partial tokens are consumed but the cache may be incomplete.
There is no way to reconcile "tokens charged for this cache build" vs "templates actually generated".

---

## Key Files

| File | Role |
|---|---|
| `datamind/backend/billing.py` | `FEATURE_COST`, `calculate_tokens`, `charge_tokens`, `charge_ai_usage`, `check_ai_limit`, `check_plan_feature`, `get_plan_history_limit`, `get_user_subscription` |
| `datamind/backend/main.py` | `_charge_op`, `_apply_row_limit`, `_ANALYTICS_OP`, all endpoint charge calls |
| `datamind/backend/llm.py` | `call_llm` → `charge_ai_usage` integration |
| `datamind/backend/integrations.py` | Sync history cutoff (`since` date), `check_db_limit` (parallel system) |
