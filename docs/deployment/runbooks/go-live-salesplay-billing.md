# SalesPlay AI — Go Live with Subscriptions (Salesplay only)

Turns off free mode for **Salesplay**. Merchants whose trial has ended are
asked to pick a plan to keep using the assistant.

**Sellmo is not touched** — it stays in free BETA. Free mode is stored per
brand, so the two are independent.

- **Urgency:** High
- **DB:** SalesPlay production
- **Impact:** Changes one row. Creates none, deletes none. No downtime, no
  database restart, no application redeploy.

---

## 1. Pre-check — run this first

```sql
SELECT 'brand' AS check_name, partner_name AS item,
       CONCAT('free=', JSON_EXTRACT(branding,'$.subscription_free'),
              ' beta=', JSON_EXTRACT(branding,'$.show_beta_badge')) AS value
FROM embed_partners
UNION ALL
SELECT 'active_plan', name,
       CONCAT('$', price_usd, ' / ', tokens_limit, ' tokens / ',
              validity_days, ' days')
FROM subscription_plans WHERE is_active = 1
UNION ALL
SELECT 'already_expired', 'merchants locked out on flip', COUNT(*)
FROM user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
WHERE us.user_email LIKE 'sp_dev_test:%' AND us.period_end < CURDATE()
UNION ALL
SELECT 'first_billing_date', 'earliest trial end',
       CAST(MIN(us.period_end) AS CHAR)
FROM user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
WHERE us.user_email LIKE 'sp_dev_test:%'
UNION ALL
SELECT 'subscriptions', us.status, COUNT(*)
FROM user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
GROUP BY us.status;
```

**Expected:**

| check_name | item | value |
|---|---|---|
| `brand` | Salesplay | free=**true** beta=false |
| `brand` | Sellmo | free=true beta=true *(absent until the Sellmo runbook has been run)* |
| `brand` | Loyverse | (unset) |
| `active_plan` | Standard | $5.00 / 25000 tokens / 30 days |
| `already_expired` | merchants locked out on flip | **0** |
| `first_billing_date` | earliest trial end | a future date |
| `subscriptions` | trial | 278 |

**Stop if:**

- More than one row under `active_plan` — merchants would be offered a plan
  that was not meant to be sold.
- `already_expired` is above zero — that many merchants lose access the
  instant the change runs. Decide whether that is acceptable first.
- `first_billing_date` is today or in the past — merchants are billed with no
  notice.

Record the `subscriptions` counts. You compare against them afterwards.

---

## 1b. Blocker — merchants on deactivated plans

`get_user_subscription` joins on `us.plan_id` with no `is_active` filter, so
deactivating a plan does **not** move anyone off it. A merchant left on
Starter keeps Starter's **200-token** cap, not Standard's 25,000, and
`check_ai_limit` blocks on that cap without ever consulting free mode.

```sql
SELECT p.name plan, p.is_active, p.tokens_limit, COUNT(*) merchants
FROM user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
JOIN subscription_plans p ON p.id = us.plan_id
GROUP BY p.name, p.is_active, p.tokens_limit;
```

**Stop if any merchant sits on `is_active = 0`.** As last read on production,
252 of 278 were on Starter — a cap 125x tighter than intended, 8 of them
already over it.

Back up `user_subscriptions` (separate from the `embed_partners` backup
below), then migrate them onto the active plan:

```sql
UPDATE user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
JOIN subscription_plans p ON p.id = us.plan_id
SET us.plan_id = 4
WHERE p.is_active = 0;
```

Re-run the query above and confirm every merchant reads Standard **before**
flipping free mode. Reversed, they hit a 200-token wall the moment their
trial ends.

---

## 2. Back up

```sql
CREATE TABLE embed_partners_bak_billing_20260827
AS SELECT * FROM embed_partners;
```

---

## 3. The change

```sql
UPDATE embed_partners
SET branding = JSON_SET(branding, '$.subscription_free', FALSE)
WHERE partner_name = 'Salesplay';
```

Expect **1 row affected**. If it reports 0 or more than 1, stop and re-run
the pre-check.

> The `WHERE` clause names Salesplay explicitly. Do not widen it — a bare
> `UPDATE embed_partners` would also start charging Sellmo's BETA merchants.

Then restart the backend:

```bash
sudo systemctl restart datamind-backend
```

---

## 4. Post-check — run this after

```sql
SELECT 'brand' AS check_name, partner_name AS item,
       CONCAT('free=', JSON_EXTRACT(branding,'$.subscription_free'),
              ' beta=', JSON_EXTRACT(branding,'$.show_beta_badge')) AS value
FROM embed_partners
UNION ALL
SELECT 'subscriptions', us.status, COUNT(*)
FROM user_subscriptions us
JOIN (SELECT user_email, MAX(id) id FROM user_subscriptions GROUP BY user_email) l
     ON l.id = us.id
GROUP BY us.status;
```

**Expected:**

| check_name | item | value |
|---|---|---|
| `brand` | Salesplay | free=**false** beta=false |
| `brand` | Sellmo | free=**true** beta=true |
| `subscriptions` | trial | 278 — unchanged from step 1 |

> **If Sellmo reads `false`, roll back immediately.** Sellmo's BETA merchants
> must not be charged.

Subscription counts must match step 1 exactly. This change touches branding
only — any movement there means something else ran.

---

## 5. Validation checklist

- [ ] `curl -s "https://ai.salesplay.com/embed/context?pk=sp_dev_test" | grep -o '"subscription_free":[a-z]*'` returns `false`
- [ ] Same call with the Sellmo partner key still returns `true`
- [ ] Salesplay merchant with an **ended** trial sees the plans screen offering Standard — $5.00 *(do not complete a payment)*
- [ ] Salesplay merchant with an **active** trial works normally — no plans screen, no interruption
- [ ] Sellmo merchant works normally — no plans screen, BETA badge still shown
- [ ] `sudo journalctl -u datamind-backend -f` shows no 403 *"Subscriptions are free right now"* — that message means the restart did not take effect

---

## Rollback

```sql
UPDATE embed_partners
SET branding = JSON_SET(branding, '$.subscription_free', TRUE)
WHERE partner_name = 'Salesplay';
```

Restart the backend. Merchants regain access at once.

Rollback stops future charges. It does **not** refund a payment already
taken — refunds go through Salesplay's payment gateway.
