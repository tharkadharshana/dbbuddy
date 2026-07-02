-- Pricing/token/history-window update — 2026-07-02
-- Run this AFTER restarting the backend (so `subscription_plans` is already
-- UPSERTed by _bootstrap_db() with the new prices/tokens from billing.py).
--
-- Why this file exists at all: subscription_plans is auto-UPSERTed by the
-- server on every restart, so its price/tokens_limit columns need no manual
-- SQL. plan_history_limits seeds via INSERT IGNORE, which only inserts rows
-- that don't already exist — so on a DB that's already been provisioned,
-- changing _PLAN_HISTORY in billing.py and restarting has NO effect. This
-- script is the manual step that actually makes the new history windows live.

-- ── 1. Data-history window per plan ────────────────────────────────────────
-- Starter: 1 month -> 3 months (row_limit 1000 -> 3000)
-- Growth:  3 months -> 12 months (row_limit 3000 -> 12000)
-- Pro:     12 months -> ~200 months / "all historical data since 2010" (row_limit 12000 -> 50000)
UPDATE plan_history_limits SET history_months = 3,   row_limit = 3000  WHERE plan_name = 'Starter';
UPDATE plan_history_limits SET history_months = 12,  row_limit = 12000 WHERE plan_name = 'Growth';
UPDATE plan_history_limits SET history_months = 200, row_limit = 50000 WHERE plan_name = 'Pro';

-- ── 2. Sanity check — confirm the update landed ────────────────────────────
SELECT plan_name, history_months, row_limit, updated_at FROM plan_history_limits ORDER BY plan_name;

-- ── 3. Optional — confirm subscription_plans picked up the new tokens/prices
--    from the backend restart (should already be correct with no action needed;
--    included here only as a verification step, not something to edit by hand).
SELECT name, price_usd, price_cents, ai_credits, tokens_limit, db_rows
FROM subscription_plans
ORDER BY sort_order;

-- ── 4. NOT included, and NOT recommended ───────────────────────────────────
-- Existing subscribers keep their current plan_id and are NOT retroactively
-- changed. Their next billing cycle will reflect the new subscription_plans
-- row automatically (same plan, new price/tokens/history). Do not run manual
-- UPDATEs against user_subscriptions or subscription_usage for this change —
-- there's nothing there that needs migrating.
