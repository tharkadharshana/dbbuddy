# Billing & Pricing Configuration — Admin Guide

This document explains which parts of the billing/pricing system are now
DB-driven, how the runtime cache works, and the exact SQL to view and edit
each setting.

All tables below are created automatically on server startup
(`bootstrap_billing_tables()` in `billing.py`) and seeded once with the
existing default values via `INSERT IGNORE` — so seeding never overwrites
values you've already changed, and your manual edits survive restarts.

## How the live-reload cache works

Pricing/feature values are read from these tables through
`_load_billing_config()` in `billing.py`, which caches the result in memory
for **60 seconds** (configurable via the `BILLING_CONFIG_CACHE_TTL` env var,
in seconds).

- Edit a row in the DB → the change takes effect for all users within at
  most `BILLING_CONFIG_CACHE_TTL` seconds — no restart needed.
- If the DB is unreachable when the cache expires, the system falls back to
  the hardcoded defaults in `billing.py` (fail-open — billing logic never
  hard-blocks the app).

---

## 1. `subscription_plans` — plan prices, AI credits, DB row limits

Controls the Starter / Growth / Pro plan definitions shown on the Billing page
and used for subscription checks.

```sql
-- View all plans
SELECT id, name, price_usd, price_cents, ai_credits, db_rows,
       trial_days, validity_days, is_active, sort_order, tokens_limit
FROM subscription_plans;

-- Example: change Growth plan price to $12.00 / 1200 cents
UPDATE subscription_plans
SET price_usd = 12.00, price_cents = 1200
WHERE name = 'Growth';

-- Example: change the monthly Token allowance for Pro
UPDATE subscription_plans
SET tokens_limit = 600
WHERE name = 'Pro';

-- Example: deactivate a plan so new users can't subscribe to it
UPDATE subscription_plans SET is_active = 0 WHERE name = 'Starter';
```

> Note: `tokens_limit` is the unified Token allowance per billing period
> (used by `check_ai_limit`). It's seeded to 50 / 125 / 500 for
> Starter / Growth / Pro.

---

## 2. `billing_config` — system-wide settings

Currently holds one key, `ai_credit_rate` (multiplier used in legacy credit
display).

```sql
-- View all config keys
SELECT config_key, config_value, updated_at FROM billing_config;

-- Example: change the AI credit rate
UPDATE billing_config SET config_value = '1.5' WHERE config_key = 'ai_credit_rate';

-- Add a new config key
INSERT INTO billing_config (config_key, config_value) VALUES ('my_new_key', 'value')
ON DUPLICATE KEY UPDATE config_value = VALUES(config_value);
```

---

## 3. `feature_costs` — per-operation Token cost

Controls the flat "feature compute" component of the unified Token formula:

```
Token = (llm_tokens / 1000) + (rows_returned / 1000) + feature_cost[operation_type]
```

Minimum charge per operation is always 0.1 Tokens, regardless of these values.

```sql
-- View all feature costs
SELECT operation_type, token_cost, description, updated_at FROM feature_costs;

-- Example: make forecast cheaper (2.0 -> 1.0 Tokens)
UPDATE feature_costs SET token_cost = 1.0 WHERE operation_type = 'forecast';

-- Example: add a cost for a brand-new operation type
-- (operation_type must match the string passed to calculate_tokens() in code)
INSERT INTO feature_costs (operation_type, token_cost, description)
VALUES ('new_feature_x', 1.5, 'My new feature')
ON DUPLICATE KEY UPDATE token_cost = VALUES(token_cost);
```

Seeded operation types and default costs:

| operation_type          | default cost |
|--------------------------|-------------|
| nl_query_rows             | 0.0 |
| prebuilt_template          | 1.0 |
| forecast                   | 2.0 |
| anomaly_detection          | 2.0 |
| rfm_analysis               | 1.5 |
| cohort_analysis            | 1.5 |
| basket_analysis            | 2.0 |
| growth_metrics             | 1.0 |
| employee_performance       | 1.0 |
| product_velocity           | 1.0 |
| payment_breakdown          | 0.5 |
| location_comparison        | 0.5 |
| llm                        | 0.0 |

> Operation types not present in the table fall back to a default cost of
> **0.5 Tokens** (hardcoded in `calculate_tokens()`).

---

## 4. `plan_feature_gates` — which plans can use which features

Controls feature gating (`check_plan_feature()`) — e.g. forecast and anomaly
detection require Growth or Pro.

```sql
-- View all gates
SELECT feature, plan_name FROM plan_feature_gates ORDER BY feature, plan_name;

-- Example: allow Starter users to use forecasting too
INSERT IGNORE INTO plan_feature_gates (feature, plan_name) VALUES ('forecast', 'Starter');

-- Example: remove Growth's access to anomaly_detection (Pro-only from now on)
DELETE FROM plan_feature_gates WHERE feature = 'anomaly_detection' AND plan_name = 'Growth';

-- Example: make a brand-new feature ungated for everyone
-- (simply do not add any rows for that feature — an empty/missing set means "no gate")
DELETE FROM plan_feature_gates WHERE feature = 'my_new_feature';
```

Seeded gates:

| feature           | allowed plans   |
|--------------------|-----------------|
| forecast            | Growth, Pro |
| anomaly_detection   | Growth, Pro |
| external_api        | Pro |
| partner_api         | Pro |
| web_widget          | Pro |

> A feature with **no rows** in this table is treated as available to all
> plans (no gate).

---

## 5. `plan_history_limits` — data lookback window per plan

Controls how far back NL queries / analytics can look (`get_plan_history_limit()`).

```sql
-- View all limits
SELECT plan_name, history_months, row_limit, updated_at FROM plan_history_limits;

-- Example: give Starter users 2 months of history instead of 1
UPDATE plan_history_limits SET history_months = 2 WHERE plan_name = 'Starter';

-- Example: raise the row fallback limit for Pro
UPDATE plan_history_limits SET row_limit = 20000 WHERE plan_name = 'Pro';
```

Seeded values:

| plan_name | history_months | row_limit |
|-----------|-----------------|-----------|
| Starter    | 1  | 1000  |
| Growth     | 3  | 3000  |
| Pro        | 12 | 12000 |

> If a user's plan name isn't found in this table, the system falls back to
> the Starter row.

---

## 6. `addon_packages` — add-on pack pricing

Controls the AI credit / DB row add-on packs purchasable from the Billing page
(`get_addon_pricing()`, `purchase_addon()`).

```sql
-- View all add-on packages
SELECT addon_type, units_per_pack, price_cents, label, updated_at FROM addon_packages;

-- Example: change the price of the AI credits pack to $1.50
UPDATE addon_packages SET price_cents = 150 WHERE addon_type = 'ai_credits';

-- Example: increase the DB rows pack size to 250K rows
UPDATE addon_packages SET units_per_pack = 250000, label = '250K DB Rows'
WHERE addon_type = 'db_rows';
```

Seeded values:

| addon_type  | units_per_pack | price_cents | label          |
|-------------|-----------------|-------------|----------------|
| ai_credits   | 25      | 100 | 25 AI Credits  |
| db_rows      | 100,000 | 100 | 100K DB Rows   |

> `addon_type` is also constrained by an `ENUM('ai_credits','db_rows')` on
> the `addon_purchases` table, so adding a brand-new addon_type here also
> requires widening that ENUM in `addon_purchases` (a code change).

---

## Summary: what's DB-driven vs hardcoded now

| Data | Source | Editable in DB? |
|------|--------|------------------|
| Plan prices, AI credits, DB row limits, Token allowance | `subscription_plans` | ✅ Yes |
| AI credit rate | `billing_config` | ✅ Yes |
| Per-operation Token cost | `feature_costs` | ✅ Yes (new) |
| Feature plan-gating | `plan_feature_gates` | ✅ Yes (new) |
| Data history window per plan | `plan_history_limits` | ✅ Yes (new) |
| Add-on pack pricing | `addon_packages` | ✅ Yes (new) |
| Minimum Token charge per operation (0.1) | hardcoded in `calculate_tokens()` | ❌ Code change |
| Default feature cost when not in `feature_costs` (0.5) | hardcoded in `calculate_tokens()` | ❌ Code change |
| `addon_type` values themselves (the ENUM) | `addon_purchases` table schema | ❌ Code/migration |
