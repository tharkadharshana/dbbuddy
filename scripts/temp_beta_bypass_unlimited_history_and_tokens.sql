-- temp_beta_bypass_unlimited_history_and_tokens.sql
--
-- Temporary bypass for beta while the full billing restructure (STANDARD_PLAN /
-- TOKEN_HARD_CAP / UNLIMITED_HISTORY_MONTHS in billing.py) stays unshipped there.
-- Pure data change, no code deploy, no backend restart required.
--
-- 1. History window: widen every plan's history_months (and the row_limit
--    safety-valve fallback) so no beta user is capped by date. Takes effect
--    within _BILLING_CONFIG_CACHE_TTL seconds (default 60) — billing.py reads
--    this table live, cached briefly.
-- 2. AI tokens: gives every known user a 50,000 ai_credits addon balance
--    (1 addon unit = 1 unified token, see billing.py get_user_subscription).
--    This raises tokens_total_available above tokens_limit, so check_ai_limit
--    stops blocking. It does NOT touch or reset tokens_used, and it stacks
--    with the existing plan tokens_limit rather than replacing it.
--
-- Idempotent: re-running does not duplicate history_limits (UPDATE only) but
-- WILL add another 50,000-credit addon pack per run for step 2 — run once.

-- ── 1. Unlimited-ish history window for every plan ──────────────────────────
UPDATE plan_history_limits
SET history_months = 1200,
    row_limit       = 50000;

-- ── 2. 50,000 ai_credits addon for every known user ─────────────────────────
INSERT INTO addon_purchases (user_email, addon_type, units_remaining)
SELECT email, 'ai_credits', 50000
FROM users;
