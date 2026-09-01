# SalesPlay AI — Standalone App Branding + Light Login

**Version:** Production Patch v1.201.0
**Baseline:** v1.200.0 (commit 7d97882)

Makes the standalone app read its brand from the database instead of
hardcoded strings, and starts the login screen in light mode.

The widget and the backend were already brand-aware. The standalone React
app was not — which is why `ai.sellmopos.com` showed the SalesPlay wordmark,
the SalesPlay tab title and favicon, and "Enjoy SalesPlay AI" in the billing
copy, while the assistant itself correctly said "I'm Sellmo AI".

- **Urgency:** Medium
- **Affects:** every brand's standalone app. Salesplay looks unchanged — the
  hardcoded values were already its own.
- **Database:** no schema change, no data change. Reads existing
  `embed_partners.branding`.

---

## What changed

| Area | Before | After |
|---|---|---|
| App logo | Hardcoded `/brand/salesplay-ai-logo.svg` | `branding.logo_url` from the partner row |
| Product name | Build-time `VITE_APP_NAME` | `branding.product_name` |
| Tab title & favicon | Hardcoded SalesPlay | Set at runtime from the brand |
| Login screen | Always dark | Follows the theme, light by default |

New endpoint: **`GET /v1/brand`** — unauthenticated, resolves the brand from
the `Host` header. The login screen needs its logo before anyone has an
account. Returns only what is already visible on screen; never
`allowed_origins` or `api_config`.

A brand with no logo configured falls back to its initial on an accent tile,
never to another brand's mark.

---

## Deploy

Both parts ship together. The frontend calls an endpoint the current backend
does not have, so deploy the backend first.

### 1. Back up what you are replacing

```bash
cd /home/datamind
cp backend/main.py backend/main.py.bak-v1.200.0
cp -r /var/www/datamind/dist /var/www/datamind/dist.bak-v1.200.0
```

### 2. Backend

```bash
cp backend/main.py /home/datamind/backend/main.py
cp backend/test_brand_resolution.py /home/datamind/backend/
sudo systemctl restart datamind-backend
```

### 3. Frontend

```bash
cp -r frontend/. /var/www/datamind/dist/
```

No web-server reload needed — static files only.

---

## Verify

### 4. The endpoint answers per host

```bash
curl -s -H "Host: ai.salesplay.com" http://127.0.0.1:8000/v1/brand \
  | grep -o '"product_name":"[^"]*"'
# "product_name":"SalesPlay AI"

curl -s -H "Host: ai.sellmopos.com" http://127.0.0.1:8000/v1/brand \
  | grep -o '"product_name":"[^"]*"'
# "product_name":"Sellmo AI"
```

An unknown host returns `{"branding": null}` — not an error. The app renders
neutrally rather than guessing a brand.

### 5. Brand resolution never leaks between brands

```bash
cd /home/datamind/backend
/home/datamind/venv/bin/python test_brand_resolution.py
# all brand resolution checks passed
```

### 6. In the browser

- [ ] `ai.sellmopos.com` shows the **Sellmo** logo, tab title and favicon
- [ ] `ai.salesplay.com` still shows **SalesPlay AI**, unchanged
- [ ] Login screen is **light**, and the theme toggle still works after login
- [ ] Billing page copy names the right brand
- [ ] Hard reload (Ctrl+Shift+R) — the old bundle is cached aggressively

---

## Rollback

```bash
cp /home/datamind/backend/main.py.bak-v1.200.0 /home/datamind/backend/main.py
rm -rf /var/www/datamind/dist
mv /var/www/datamind/dist.bak-v1.200.0 /var/www/datamind/dist
sudo systemctl restart datamind-backend
```

Nothing in the database changes, so there is nothing to undo there.

---

## Known limitation

`GET /v1/brand` resolves by `Host`, so the standalone app is only correctly
branded on a hostname listed in that brand's `app_domains`. Sellmo's row
already has `ai.sellmopos.com`. A brand reached at any other address gets the
neutral fallback — deliberately, since guessing would put one brand's mark in
another brand's app.

This requires `ProxyPreserveHost On` in the Apache vhost. Without it the
backend sees the proxy's own hostname and every brand resolves to neutral.
