# Pricing & Plan Configuration

## Quick Answer

**No, you do not need to touch the database manually.**

`billing.py` runs an automatic UPSERT on the `subscription_plans` table every time the server starts. Change the numbers in `billing.py`, restart the server, and the database is updated automatically.

**Yes, there are currently 3 places to change.** This document explains why, what each controls, and how to do it cleanly every time.

---

## Current Prices (as of 2026-07-02)

| Plan    | Price/mo | Tokens (raw / displayed) | Data history | DB Rows     |
|---------|----------|---------------------------|--------------|-------------|
| Starter | $5       | 200 / 2M                 | 3 months     | 2,000,000   |
| Growth  | $10      | 500 / 5M                 | 12 months    | 5,000,000   |
| Pro     | $25      | 2,000 / 20M               | All historical (since 2010) | 20,000,000  |

Displayed tokens = raw `tokens_limit` × `TDM` (10,000) — see `TDM` constant in `BillingPage.jsx` / `EmbedSalesplayAutoInit.jsx`.

"All historical" for Pro is implemented as `history_months = 200` (~16.7 years) — there's no explicit
"unlimited" sentinel in `get_plan_history_limit()`, it always computes a concrete `cutoff_date`. 200 months
comfortably covers any data since 2010 from today's date.

### Changelog
- **2026-07-02** — Prices restructured to $5 (3mo history) / $10 (12mo history) / $25 (all historical since
  2010). Token allowances doubled: Starter 100→200, Growth 250→500, Pro 1,000→2,000 (raw `tokens_limit`;
  displayed as 2M/5M/20M). `_PLAN_HISTORY` months changed 1/3/12 → 3/12/200. Pro's history `row_limit`
  fallback raised 12,000→50,000 to stay proportionate. `ai_credits` seed values also doubled to match
  (100→200/250→500/1,000→2,000) — note this field is legacy/display-only, it does **not** feed the live
  `tokens_limit` enforcement (see "Two separate credit fields" below).
- **2026-05-22** — Prices $100 / $250 / $1,000 (superseded).

---

## All Locations Where Pricing Lives

### 1. `datamind/backend/billing.py` — Source of Truth
**`_bootstrap_db()`, ~line 385. This is the only place with real business logic.**

```python
plans = [
    ("Starter", "5.00",   500,   200,  2_000_000,    1),
    ("Growth",  "10.00", 1000,   500,  5_000_000,    2),
    ("Pro",     "25.00", 2500,  2000, 20_000_000,    3),
]
# Format: (name, price_usd, price_cents, ai_credits, db_rows, sort_order)
```

**What it does:** On every server startup, `_bootstrap_db()` runs an `INSERT ... ON DUPLICATE KEY UPDATE` — it creates the plans if they don't exist, or updates them if they do. The database always reflects whatever is in this file after a restart.

**Controls:**
- The database `subscription_plans` table (authoritative record)
- All billing enforcement (`check_ai_limit`, `charge_tokens`, `get_plan_history_limit`)
- The `/v1/billing/plans` API endpoint — what the frontend fetches at runtime
- Token limit enforcement: `_TOKEN_LIMITS` dict (also in `billing.py`, ~line 665) must match

**Note on `ai_credits` vs `tokens_limit`:** `ai_credits` is a legacy/display-only column (feeds `ai_base_limit` /
`ai_total_available` in the subscription response) — it does **not** gate AI usage. The real enforcement value
is `tokens_limit`, seeded separately via `_TOKEN_LIMITS_SEED` right after the plans loop, and mirrored in the
in-memory dict below. Keep both doubled/changed together even though they're not read from the same place:
```python
_TOKEN_LIMITS = {"Starter": 200.0, "Growth": 500.0, "Pro": 2000.0}
```
This is the live enforcement gate used by `check_ai_limit()`. If you change `tokens_limit` seeding but forget
to update `_TOKEN_LIMITS`, billing enforcement will use the old numbers.

**Displayed tokens ≠ raw `tokens_limit`.** The frontend multiplies the raw DB value by `TDM = 10_000` before
showing it to users (e.g. raw `200` → displayed `"2M Tokens"`). See `BillingPage.jsx` and
`EmbedSalesplayAutoInit.jsx`.

**Note on `_PLAN_HISTORY` / `plan_history_limits`:** Data-history window (how far back date-filtered queries
look) is a *separate* dict, ~line 677:
```python
_PLAN_HISTORY = {
    "Starter": {"months": 3,   "row_limit": 3000},
    "Growth":  {"months": 12,  "row_limit": 12000},
    "Pro":     {"months": 200, "row_limit": 50000},   # ~16.7yr — stand-in for "all data since 2010"
}
```
**This one seeds via `INSERT IGNORE` into `plan_history_limits`, so changing the dict and restarting does
NOT update an already-provisioned database** — unlike `subscription_plans`, which UPSERTs every restart.
You must run a manual `UPDATE` (see the SQL section below) after changing these values, or delete the rows
and let `_bootstrap_db()` reseed them.

---

### 2. `datamind/frontend/src/pages/OnboardingWizard.jsx` — Onboarding UI Labels
**`PLAN_HIGHLIGHTS` (~line 44) and the `dataMonths` ternary (~line 391). Hardcoded display strings only.**

```js
const PLAN_HIGHLIGHTS = {
  Starter: { tokens: '200 Tokens / mo',   price: '$5' },
  Growth:  { tokens: '500 Tokens / mo',   price: '$10' },
  Pro:     { tokens: '2,000 Tokens / mo', price: '$25' },
}
```

```js
const dataMonths = plan.name === 'Starter' ? '3 months' : plan.name === 'Growth' ? '12 months' : 'all historical'
```

**What it does:** Shows price/token labels on the plan selection card inside the onboarding wizard, and a
"— N months of data" label after a provider connection test succeeds.

**Why it's hardcoded:** The onboarding wizard runs before the user is fully authenticated (they haven't
connected a provider yet). Fetching plans from the API at that moment was not implemented — a static map was
used for simplicity. The `dataMonths` ternary keys off `plan.name` (a string match against the DB row), not
an API-provided history value — `/v1/billing/plans` doesn't return `history_months` today.

**Controls:** Visual display only. Has no effect on billing enforcement.

**Note:** The rest of the onboarding wizard reads `plan.price_usd` from the API (`/v1/billing/plans`) for the
actual plan confirmation step. Only the summary labels and the `dataMonths` string use hardcoded text.

---

### 3. `datamind/frontend/src/embed/EmbedOnboarding.jsx` — Embed Onboarding Fallback
**~line 331. Shown only while the API call is loading.**

```js
{ id: 1, name: 'Starter', tokens_limit: 200,  price_cents: 500  },
{ id: 2, name: 'Growth',  tokens_limit: 500,  price_cents: 1000 },
{ id: 3, name: 'Pro',     tokens_limit: 2000, price_cents: 2500 },
```

**What it does:** When the embed onboarding widget loads, it fetches plans from `/v1/billing/plans`. While waiting for that response, it renders this hardcoded list as a loading placeholder. Once the API responds, the real data replaces it.

**Controls:** Visual fallback only. If the API is fast (it always is), users will never see these numbers.

---

### 4. `datamind/frontend/src/pages/BillingPage.jsx` and `datamind/frontend/src/embed/EmbedSalesplayAutoInit.jsx` — Feature/History Bullet Lists
**`PLAN_FEATURES` in both files (~line 20 and ~line 46 respectively). Hardcoded display strings only.**

Each plan's feature list mixes real DB-backed gates (Forecasting, Priority Support, Web widget — mirrors
`_PLAN_FEATURE_GATE` / `plan_feature_gates` table, also never exposed via API) with a plain-text restatement
of the token count and data-history window, e.g. `'2M Tokens / month'`, `'3 Months data history'`,
`'All historical data'`. None of this is fetched from the API — it's typed by hand in both files and must be
kept in sync manually whenever prices, tokens, or history windows change.

**Known gap:** `/v1/billing/plans` could return `history_months` and feature gates directly (both already
exist as DB tables), which would let `PLAN_FEATURES`, `PLAN_HIGHLIGHTS`, and `dataMonths` all be removed in
favor of API-driven rendering. Not done as of 2026-07-02 — flagged as follow-up, not implemented.

---

### 5. `datamind/frontend/src/pages/BillingPage.jsx` — Prices: No Change Needed
This file reads `plan.price_cents` directly from the API response and formats it dynamically:
```js
${(plan.price_cents / 100).toFixed(0)}/month
```
No hardcoded prices. Updates automatically when the backend changes.

---

## Why There Are Multiple Places (Not 1)

| Location | Why it exists separately |
|---|---|
| `billing.py` | Backend enforcement needs prices/tokens/history at runtime without a DB query on every request. Seeds the DB on startup. |
| `OnboardingWizard.jsx` | Frontend was built before the plan API was wired to the onboarding step. Static labels were faster to ship. |
| `EmbedOnboarding.jsx` | Embed must show something while the API loads; a blank screen looks broken. |
| `BillingPage.jsx` / `EmbedSalesplayAutoInit.jsx` `PLAN_FEATURES` | Feature-gate and history-window bullet text was never wired to an API field, even though the underlying data (`plan_feature_gates`, `plan_history_limits`) is DB-backed. |

**Long-term fix:** Wire `OnboardingWizard.jsx` to fetch from `/v1/billing/plans` on mount, same as
`BillingPage.jsx`. Extend `/v1/billing/plans` to also return `history_months` and feature gates per plan, and
rewrite `PLAN_FEATURES` / `PLAN_HIGHLIGHTS` / `dataMonths` to render from that response. Then pricing, tokens,
and history windows all become a single-source change in `billing.py` only. The embed fallback can remain as
a UX placeholder — it's not a business logic concern. **Not implemented as of 2026-07-02** — scoped out as
follow-up work, not done during the 2026-07-02 pricing change.

---

## How to Change Pricing / Tokens / History Window (Step-by-Step)

### Step 1 — Update `billing.py`
Edit the `plans` list in `_bootstrap_db()`:
```python
plans = [
    ("Starter", "NEW_USD", NEW_CENTS, NEW_AI_CREDITS, db_rows, 1),
    ("Growth",  "NEW_USD", NEW_CENTS, NEW_AI_CREDITS, db_rows, 2),
    ("Pro",     "NEW_USD", NEW_CENTS, NEW_AI_CREDITS, db_rows, 3),
]
```
`price_cents` = `price_usd × 100` (e.g. $25 = `2500`).

If you changed token allowances, update **all three** of these together (they must stay in sync):
```python
_TOKEN_LIMITS_SEED = {"Starter": NEW_TOKENS, "Growth": NEW_TOKENS, "Pro": NEW_TOKENS}  # in _bootstrap_db()
_TOKEN_LIMITS = {"Starter": NEW_TOKENS, "Growth": NEW_TOKENS, "Pro": NEW_TOKENS}        # module-level, ~line 665
```
`ai_credits` in the `plans` list is legacy/display-only and doesn't gate usage, but keep it proportionate to
`_TOKEN_LIMITS` to avoid confusing `ai_total_available` numbers in the API response.

If you changed the data-history window, update `_PLAN_HISTORY` (~line 677):
```python
_PLAN_HISTORY = {
    "Starter": {"months": NEW_MONTHS, "row_limit": NEW_ROW_LIMIT},
    "Growth":  {"months": NEW_MONTHS, "row_limit": NEW_ROW_LIMIT},
    "Pro":     {"months": NEW_MONTHS, "row_limit": NEW_ROW_LIMIT},
}
```
**Important:** unlike `subscription_plans`, the `plan_history_limits` table seeds via `INSERT IGNORE` — changing
this dict and restarting will **not** update an already-provisioned database. See Step 5.

### Step 2 — Update `OnboardingWizard.jsx`
Update `PLAN_HIGHLIGHTS` (price/token labels) and the `dataMonths` ternary (history-window label):
```js
const PLAN_HIGHLIGHTS = {
  Starter: { tokens: 'NEW Tokens / mo', price: '$NEW' },
  ...
}
const dataMonths = plan.name === 'Starter' ? 'NEW' : plan.name === 'Growth' ? 'NEW' : 'NEW'
```

### Step 3 — Update `EmbedOnboarding.jsx`
Update the fallback plan list `tokens_limit` and `price_cents` values.

### Step 4 — Update `BillingPage.jsx` and `EmbedSalesplayAutoInit.jsx`
Update the `PLAN_FEATURES` token-count and data-history bullet strings in both files (they're typed by hand,
not derived from `tokens_limit` or `_PLAN_HISTORY`).

### Step 5 — Restart the server, then run manual SQL for history windows
```bash
python start.py
```
`_bootstrap_db()` runs on startup and UPSERTs the new prices/tokens into `subscription_plans`. **No manual SQL
needed for prices or tokens.**

**Data-history windows are the exception** — `plan_history_limits` uses `INSERT IGNORE`, so existing rows
survive restarts unchanged. Run this after restarting:
```sql
UPDATE plan_history_limits SET history_months = NEW_MONTHS, row_limit = NEW_ROW_LIMIT WHERE plan_name = 'Starter';
UPDATE plan_history_limits SET history_months = NEW_MONTHS, row_limit = NEW_ROW_LIMIT WHERE plan_name = 'Growth';
UPDATE plan_history_limits SET history_months = NEW_MONTHS, row_limit = NEW_ROW_LIMIT WHERE plan_name = 'Pro';
```
This table is also cached in-process for `BILLING_CONFIG_CACHE_TTL` seconds (default 60) — changes apply
within that window without needing a restart at all.

### Step 6 — Verify in the DB (optional sanity check)
```sql
SELECT name, price_usd, price_cents, ai_credits, tokens_limit FROM subscription_plans;
SELECT plan_name, history_months, row_limit FROM plan_history_limits;
```
Should reflect the new values immediately after restart (plans) / after the manual UPDATE (history limits).

---

## Database — What Happens Automatically (and What Doesn't)

The `subscription_plans` table is managed entirely by `_bootstrap_db()`. The UPSERT logic is:

```sql
INSERT INTO subscription_plans (name, price_usd, price_cents, ai_credits, db_rows, tokens_limit, sort_order)
VALUES (...)
ON DUPLICATE KEY UPDATE
  price_usd = VALUES(price_usd),
  price_cents = VALUES(price_cents),
  ai_credits = VALUES(ai_credits),
  db_rows = VALUES(db_rows),
  sort_order = VALUES(sort_order)
-- tokens_limit is updated in a separate UPDATE right after, from _TOKEN_LIMITS_SEED
```

**You never need to run manual SQL to change plan prices or token allowances.** The server is the source of
truth for the database schema and seed data — restart and it's live.

**`plan_history_limits` is the one table where this doesn't hold** — it uses `INSERT IGNORE`, which only
inserts rows that don't already exist. Changing `_PLAN_HISTORY` in code and restarting has no effect on a
database that's already been provisioned once. You must run the manual `UPDATE` shown in Step 5, or
`DELETE FROM plan_history_limits;` and restart to let it reseed from scratch (loses any DB-side manual edits).

**Existing subscriptions are not retroactively changed.** A user currently on Starter will still be on
Starter after a price change — but the plan record in `subscription_plans` will now show the new price.
Their next billing cycle will be at the new rate. If you need to grandfather users, that requires a separate
migration.

---

## Admin: Manually Adding Credits to a User

Use this when a user runs out of credits and needs more for testing or as a manual top-up
(bypasses the payment flow — no charge is made).

### Add AI credit addon packs

Each pack = 25 AI credits. Adjust the `units_remaining` value to the number of credits you want to add.

```sql
-- Add 100 AI credits (= 4 packs of 25) to a user
INSERT INTO addon_purchases (user_email, addon_type, units_remaining)
VALUES ('user@example.com', 'ai_credits', 100);
```

To verify the balance afterwards:

```sql
SELECT SUM(units_remaining) AS addon_balance
FROM addon_purchases
WHERE user_email = 'user@example.com'
  AND addon_type = 'ai_credits'
  AND units_remaining > 0;
```

### Reset usage for the current billing period

Use this when a test account is exhausted and you want to restore the full plan allowance
without adding addon packs.

```sql
UPDATE subscription_usage
SET ai_credits_used = 0, tokens_used = 0, db_rows_used = 0
WHERE user_email = 'user@example.com';
```

### Check a user's full billing state

```sql
SELECT
    us.plan_id, us.status, us.period_start, us.period_end,
    su.tokens_used, su.ai_credits_used,
    (SELECT SUM(units_remaining) FROM addon_purchases ap
     WHERE ap.user_email = us.user_email AND ap.addon_type = 'ai_credits' AND units_remaining > 0
    ) AS addon_credits_remaining
FROM user_subscriptions us
JOIN subscription_usage su ON su.user_email = us.user_email
WHERE us.user_email = 'user@example.com';
```

> **Note:** The preferred method for top-ups is `addon_purchases` (not resetting usage or changing the
> plan), because it preserves the user's actual usage history and rolls over unused balance.

---

## Related Files (Read-Only Reference)

| File | Role |
|---|---|
| `billing.py:check_ai_limit()` | Enforces token caps using `_TOKEN_LIMITS` |
| `billing.py:get_plan_history_limit()` | Returns months of history and row limits per plan |
| `billing.py:subscribe_to_plan()` | Records a user's plan choice in `user_subscriptions` |
| `main.py:/v1/billing/plans` | API endpoint — returns the `subscription_plans` table rows |
| `BillingPage.jsx` | Reads plans from API; no hardcoded prices |
