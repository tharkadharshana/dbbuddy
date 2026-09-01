# SalesPlay AI — Enable Sellmo Brand (Beta)

Registers Sellmo as a second brand on the existing production deployment.
Same server, same database, same application — one new row in `embed_partners`.

Sellmo launches in **BETA**: the widget carries a BETA badge and all
subscription checks are bypassed (free mode). Salesplay is unaffected.

**Prerequisite:** patch v1.200.0 already deployed. Confirmed live.

- **Urgency:** Medium
- **DB:** SalesPlay production
- **Impact:** No downtime. No restart. No application redeploy. Adds one row; changes no existing row.

---

## Part 1 — Create the brand row

### 1. Confirm Sellmo does not already exist

```sql
SELECT partner_key, partner_name FROM embed_partners;
```

Expect two rows only: Salesplay and Loyverse. If a Sellmo row is already
present, **stop** and skip to step 5 (update path).

### 2. Back up the table

```sql
CREATE TABLE embed_partners_bak_20260827
AS SELECT * FROM embed_partners;
```

### 3. Create the row

```bash
cd /home/datamind/backend
/home/datamind/venv/bin/python scripts/add_brand.py brands/sellmo.json
```

The script generates the partner key and prints it. **Record the value shown
as `partner_key`** — it looks like `sl_live_XXXXXXXX`. It cannot be recovered
later in the same form, and it is what Sellmo needs for their iframe.

---

## Part 2 — Verify the row

### 4. Confirm branding, beta badge and free mode

```sql
SELECT partner_key,
       partner_name,
       active,
       JSON_UNQUOTE(JSON_EXTRACT(branding,'$.product_name'))  AS product,
       JSON_UNQUOTE(JSON_EXTRACT(branding,'$.logo_url'))      AS logo,
       JSON_UNQUOTE(JSON_EXTRACT(branding,'$.logo_mark_url')) AS mark,
       JSON_EXTRACT(branding,'$.show_beta_badge')             AS beta,
       JSON_EXTRACT(branding,'$.subscription_free')           AS free
FROM embed_partners WHERE partner_name = 'Sellmo';
```

Expected:

| Field | Value |
|---|---|
| `product` | Sellmo AI |
| `logo` | /brand/sellmo-logo.png |
| `mark` | /brand/sellmo-mark.png |
| `beta` | true |
| `free` | true |
| `active` | 1 |

### 5. Correct any wrong or missing branding value

```sql
UPDATE embed_partners
SET branding = JSON_SET(branding,
      '$.logo_url',          '/brand/sellmo-logo.png',
      '$.logo_mark_url',     '/brand/sellmo-mark.png',
      '$.show_beta_badge',   TRUE,
      '$.subscription_free', TRUE)
WHERE partner_name = 'Sellmo';
```

Then re-run step 4.

### 6. Confirm the POS endpoints

```sql
SELECT JSON_UNQUOTE(JSON_EXTRACT(api_config,'$.sync_base'))  AS sync_base,
       JSON_UNQUOTE(JSON_EXTRACT(api_config,'$.proxy_base')) AS proxy_base
FROM embed_partners WHERE partner_name = 'Sellmo';
```

Expected:

- `sync_base` — `https://api.backofficewebportal.com/v1.0`
- `proxy_base` — `https://sellmo.backofficewebportal.com/rest/v2.0/public/app`

> **Confirm these with Sellmo before any merchant onboards.** A wrong base
> means merchants sync against the wrong POS backend, and the bad data
> persists after the value is corrected.

### 7. Confirm Salesplay is untouched

```sql
SELECT partner_name,
       JSON_EXTRACT(branding,'$.show_beta_badge') AS beta
FROM embed_partners WHERE partner_name = 'Salesplay';
```

Expected: `beta = false`. Salesplay must **not** show a BETA badge.

---

## Part 3 — Assets

### 8. Confirm the Sellmo logo files are served

```bash
curl -sI https://ai.salesplay.com/brand/sellmo-logo.png | head -1
curl -sI https://ai.salesplay.com/brand/sellmo-mark.png | head -1
```

Both must return `200`. A `404` means the frontend build shipped without
`public/brand` — redeploy the frontend dist.

---

## Part 4 — Hosting for the Sellmo domain

Required only for the standalone app at `ai.sellmopos.com`. The embedded
widget works without it, served from the existing domain.

### 9. DNS

Point `ai.sellmopos.com` at the same IP as `ai.salesplay.com`.

### 10. TLS

Certificate covering `ai.sellmopos.com`.

### 11. Apache vhost

For `ai.sellmopos.com` — same `DocumentRoot` and same backend as
`ai.salesplay.com`, and it **must** set:

```apache
ProxyPreserveHost On
```

> Without this the backend cannot tell which brand a direct login belongs to,
> and Sellmo users are served Salesplay branding.

### 12. Verify brand resolution by domain

```bash
curl -s "https://ai.sellmopos.com/embed/context?pk=<PARTNER_KEY>" \
  | grep -o '"product_name":"[^"]*"'
```

Expected: `"product_name":"Sellmo AI"`

---

## Part 5 — Hand off to Sellmo

### 13. Give Sellmo the partner key and iframe tag

```html
<iframe
  src="https://ai.sellmopos.com/src/embed/embed.html?pk=<PARTNER_KEY>&aat=<APP_ACCESS_TOKEN>"
  width="420" height="680" frameborder="0"
  allow="clipboard-write"
  style="border-radius:12px;">
</iframe>
```

`aat` is the merchant's app access token, injected per session by Sellmo's
backoffice — the same mechanism Salesplay uses.

Until `ai.sellmopos.com` is live (Part 4), the widget can be served from
`https://ai.salesplay.com` with the same `pk`. Branding is resolved from the
partner key, not the domain, so it renders as Sellmo either way.

---

## Rollback

Disable Sellmo without deleting anything:

```sql
UPDATE embed_partners SET active = 0 WHERE partner_name = 'Sellmo';
```

Salesplay and Loyverse are unaffected.
