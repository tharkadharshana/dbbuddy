# Pricing & Plan Configuration

## Quick Answer

**No, you do not need to touch the database manually.**

`billing.py` runs an automatic UPSERT on the `subscription_plans` table every time the server starts. Change the numbers in `billing.py`, restart the server, and the database is updated automatically.

**Yes, there are currently 3 places to change.** This document explains why, what each controls, and how to do it cleanly every time.

---

## Current Prices (as of 2026-05-22)

| Plan    | Price/mo | AI Tokens | DB Rows     |
|---------|----------|-----------|-------------|
| Starter | $100     | 500       | 2,000,000   |
| Growth  | $250     | 1,500     | 5,000,000   |
| Pro     | $1,000   | 10,000    | 20,000,000  |

---

## All Locations Where Pricing Lives

### 1. `datamind/backend/billing.py` — Source of Truth
**Line ~279. This is the only place with real business logic.**

```python
plans = [
    ("Starter", "100.00", 10000,   500,  2_000_000,    1),
    ("Growth",  "250.00", 25000,  1500,  5_000_000,    2),
    ("Pro",     "1000.00", 100000, 10000, 20_000_000,    3),
]
# Format: (name, price_usd, price_cents, ai_credits, db_rows, sort_order)
```

**What it does:** On every server startup, `_bootstrap_db()` runs an `INSERT ... ON DUPLICATE KEY UPDATE` — it creates the plans if they don't exist, or updates them if they do. The database always reflects whatever is in this file after a restart.

**Controls:**
- The database `subscription_plans` table (authoritative record)
- All billing enforcement (`check_ai_limit`, `charge_tokens`, `get_plan_history_limit`)
- The `/v1/billing/plans` API endpoint — what the frontend fetches at runtime
- Token limit enforcement: `_TOKEN_LIMITS` dict (also in `billing.py`, line ~303) must match

**Note on `_TOKEN_LIMITS`:** There is a second in-memory dict that must stay in sync:
```python
_TOKEN_LIMITS = {"Starter": 500.0, "Growth": 1500.0, "Pro": 10000.0}
```
This is the live enforcement gate used by `check_ai_limit()`. If you change `ai_credits` in the plans list but forget to update `_TOKEN_LIMITS`, billing enforcement will use the old numbers.

---

### 2. `datamind/frontend/src/pages/OnboardingWizard.jsx` — Onboarding UI Labels
**Line ~42. Hardcoded display strings only.**

```js
const PLAN_HIGHLIGHTS = {
  Starter: { tokens: '500 Tokens / mo',    price: '$100' },
  Growth:  { tokens: '1,500 Tokens / mo',  price: '$250' },
  Pro:     { tokens: '10,000 Tokens / mo', price: '$1,000' },
}
```

**What it does:** Shows price labels on the plan selection card inside the onboarding wizard (Step 1 of the main app onboarding flow).

**Why it's hardcoded:** The onboarding wizard runs before the user is fully authenticated (they haven't connected a provider yet). Fetching plans from the API at that moment was not implemented — a static map was used for simplicity.

**Controls:** Visual display only. Has no effect on billing enforcement.

**Note:** The rest of the onboarding wizard reads `plan.price_usd` from the API (`/v1/billing/plans`) for the actual plan confirmation step (line ~414). Only the summary labels use `PLAN_HIGHLIGHTS`.

---

### 3. `datamind/frontend/src/embed/EmbedOnboarding.jsx` — Embed Onboarding Fallback
**Line ~330. Shown only while the API call is loading.**

```js
{ id: 1, name: 'Starter', tokens_limit: 500,   price_cents: 10000  },
{ id: 2, name: 'Growth',  tokens_limit: 1500,  price_cents: 25000  },
{ id: 3, name: 'Pro',     tokens_limit: 10000, price_cents: 100000 },
```

**What it does:** When the embed onboarding widget loads, it fetches plans from `/v1/billing/plans`. While waiting for that response, it renders this hardcoded list as a loading placeholder. Once the API responds, the real data replaces it.

**Controls:** Visual fallback only. If the API is fast (it always is), users will never see these numbers.

---

### 4. `datamind/frontend/src/pages/BillingPage.jsx` — No Change Needed
This file reads `plan.price_cents` directly from the API response and formats it dynamically:
```js
${(plan.price_cents / 100).toFixed(0)}/month
```
No hardcoded prices. Updates automatically when the backend changes.

---

## Why There Are 3 Places (Not 1)

| Location | Why it exists separately |
|---|---|
| `billing.py` | Backend enforcement needs prices at runtime without a DB query on every request. Seeds the DB on startup. |
| `OnboardingWizard.jsx` | Frontend was built before the plan API was wired to the onboarding step. Static labels were faster to ship. |
| `EmbedOnboarding.jsx` | Embed must show something while the API loads; a blank screen looks broken. |

**Long-term fix:** Wire `OnboardingWizard.jsx` to fetch from `/v1/billing/plans` on mount, same as `BillingPage.jsx`. Then pricing is a single-source change in `billing.py` only. The embed fallback can remain as a UX placeholder — it's not a business logic concern.

---

## How to Change Pricing (Step-by-Step)

### Step 1 — Update `billing.py`
Edit the `plans` list in `_bootstrap_db()`:
```python
plans = [
    ("Starter", "NEW_USD", NEW_CENTS, ai_credits, db_rows, 1),
    ("Growth",  "NEW_USD", NEW_CENTS, ai_credits, db_rows, 2),
    ("Pro",     "NEW_USD", NEW_CENTS, ai_credits, db_rows, 3),
]
```
`price_cents` = `price_usd × 100` (e.g. $100 = `10000`).

If you changed `ai_credits`, also update `_TOKEN_LIMITS`:
```python
_TOKEN_LIMITS = {"Starter": NEW_CREDITS, "Growth": NEW_CREDITS, "Pro": NEW_CREDITS}
```

### Step 2 — Update `OnboardingWizard.jsx`
Update the price string and token label:
```js
const PLAN_HIGHLIGHTS = {
  Starter: { tokens: 'NEW Tokens / mo', price: '$NEW' },
  ...
}
```

### Step 3 — Update `EmbedOnboarding.jsx`
Update the fallback plan list `price_cents` values.

### Step 4 — Restart the server
```bash
python start.py
```
`_bootstrap_db()` runs on startup and UPSERTs the new prices into `subscription_plans`. **No manual SQL needed.**

### Step 5 — Verify in the DB (optional sanity check)
```sql
SELECT name, price_usd, price_cents, ai_credits FROM subscription_plans;
```
Should reflect the new values immediately after restart.

---

## Database — What Happens Automatically

The `subscription_plans` table is managed entirely by `_bootstrap_db()`. The UPSERT logic is:

```sql
INSERT INTO subscription_plans (name, price_usd, price_cents, ai_credits, db_rows, sort_order)
VALUES (...)
ON DUPLICATE KEY UPDATE
  price_usd = VALUES(price_usd),
  price_cents = VALUES(price_cents),
  ai_credits = VALUES(ai_credits),
  db_rows = VALUES(db_rows),
  sort_order = VALUES(sort_order)
```

**You never need to run manual SQL to change plan prices.** The server is the source of truth for the database schema and seed data.

**Existing subscriptions are not retroactively changed.** A user currently on Starter at $5/mo will still be on Starter — but the plan record in `subscription_plans` will now show $100. Their next billing cycle will be at the new rate. If you need to grandfather users, that requires a separate migration.

---

## Related Files (Read-Only Reference)

| File | Role |
|---|---|
| `billing.py:check_ai_limit()` | Enforces token caps using `_TOKEN_LIMITS` |
| `billing.py:get_plan_history_limit()` | Returns months of history and row limits per plan |
| `billing.py:subscribe_to_plan()` | Records a user's plan choice in `user_subscriptions` |
| `main.py:/v1/billing/plans` | API endpoint — returns the `subscription_plans` table rows |
| `BillingPage.jsx` | Reads plans from API; no hardcoded prices |
