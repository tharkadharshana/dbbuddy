# Embed Widget Customization Guide

## Overview

The DataMind embed widget is an iframe that partner companies (e.g. Salesplay)
embed inside their own web application. Each partner has a unique `partner_key`
stored in the `embed_partners` table. All customization is scoped to that key —
different partners get different branding, domains, and provider configurations.

---

## Architecture

```
Partner's web app (app.salesplay.io)
  └─ <iframe src="https://datamind.ai/embed/widget?pk=PARTNER_KEY&theme=dark">
       └─ EmbedApp.jsx          ← loads partner config from /embed/config
            └─ EmbedOnboarding  ← first-time: plan select + POS connect
            └─ EmbedChat        ← returning: analytics + AI queries
```

The partner key (`pk`) is passed as a query parameter. The backend validates it
against `embed_partners` and returns the partner's config (branding, provider,
allowed origins). Everything the iframe renders is driven by that config.

---

## The `embed_partners` Table

```sql
CREATE TABLE embed_partners (
    partner_key     VARCHAR(64) PRIMARY KEY,   -- unique key, passed as ?pk=
    partner_name    VARCHAR(128) NOT NULL,      -- internal label (not shown to users)
    provider_id     VARCHAR(50)  NOT NULL,      -- 'salesplay' | 'loyverse'
    allowed_origins TEXT         NOT NULL,      -- comma-separated domains
    branding        JSON,                       -- customization JSON (see below)
    active          TINYINT(1)   DEFAULT 1,     -- 0 = key disabled
    created_at      DATETIME     DEFAULT NOW()
)
```

---

## Customization Options

### 1. Branding (`branding` JSON column)

Stored as a JSON object. Controls the visual appearance of the embed widget.

**Full schema:**

```json
{
  "company_name":    "Salesplay",
  "logo_url":        "https://cdn.salesplay.io/logo.png",
  "primary_color":   "#0055FF",
  "welcome_message": "Welcome to your sales analytics dashboard.",
  "support_email":   "support@salesplay.io"
}
```

| Field | Type | What it controls |
|---|---|---|
| `company_name` | string | Shown on the consent/welcome screen and iframe header |
| `logo_url` | string (URL) | Partner logo shown in the iframe header |
| `primary_color` | string (hex) | Accent color for buttons, highlights, active states |
| `welcome_message` | string | Custom text shown on the first-load consent screen |
| `support_email` | string | Contact email shown when errors occur |

**Current wiring status:**
- `company_name` — ✅ read and passed to embed components
- `logo_url` — ⚠️ stored but not yet rendered in EmbedApp.jsx
- `primary_color` — ⚠️ stored but CSS injection not yet implemented
- `welcome_message` — ⚠️ stored but not yet rendered
- `support_email` — ⚠️ stored but not yet rendered

**How to set:**

```sql
UPDATE embed_partners
SET branding = JSON_OBJECT(
    'company_name',    'Salesplay',
    'logo_url',        'https://cdn.salesplay.io/logo.png',
    'primary_color',   '#0055FF',
    'welcome_message', 'Welcome to your Salesplay analytics.',
    'support_email',   'support@salesplay.io'
)
WHERE partner_key = 'YOUR_PARTNER_KEY';
```

---

### 2. Allowed Origins (`allowed_origins`)

Comma-separated list of domains that are allowed to embed the iframe widget.
Enforced in two places:

1. **Server-side CORS** — backend rejects requests from unlisted origins
2. **Session validation** — when the embed loads, the `Origin` header is checked
   against `allowed_origins`

**Format:** full origins including protocol, no trailing slash.

```
https://app.salesplay.io,https://backoffice.salesplay.io
```

**How to set:**

```sql
UPDATE embed_partners
SET allowed_origins = 'https://app.salesplay.io,https://backoffice.salesplay.io'
WHERE partner_key = 'YOUR_PARTNER_KEY';
```

**Also set in `.env` for the global CORS policy:**

```env
EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://backoffice.salesplay.io
```

The `.env` value controls `Content-Security-Policy: frame-ancestors` and the
global CORS `Access-Control-Allow-Origin` header. Both the DB value and the
`.env` value must include the domain for full protection.

---

### 3. POS Provider (`provider_id`)

Which POS integration the embed connects to. Set at partner creation time —
this determines which sync endpoints are called, which analytics templates are
shown, and which data tables (`sp_*` vs `ly_*`) are queried.

| Value | POS System |
|---|---|
| `salesplay` | Salesplay POS |
| `loyverse` | Loyverse POS |

**How to set (at creation):**

```sql
INSERT INTO embed_partners
    (partner_key, partner_name, provider_id, allowed_origins, branding, active)
VALUES
    ('sk_live_XXXX', 'Salesplay', 'salesplay',
     'https://app.salesplay.io',
     '{"company_name":"Salesplay"}',
     1);
```

**Cannot be changed after creation** without breaking existing embed sessions —
changing provider_id would disconnect all users' synced data.

---

### 4. Theme (`theme` query parameter)

The iframe `src` URL accepts a `theme` query parameter that switches the widget
between dark and light mode.

**Usage:**

```html
<!-- Dark mode (default) -->
<iframe src="https://datamind.ai/embed/widget?pk=YOUR_KEY&theme=dark"></iframe>

<!-- Light mode -->
<iframe src="https://datamind.ai/embed/widget?pk=YOUR_KEY&theme=light"></iframe>
```

This is read in `EmbedApp.jsx` at load time:

```js
const theme = new URLSearchParams(window.location.search).get('theme') || 'dark'
```

The theme value is applied to the iframe's `data-theme` attribute and all CSS
variables switch accordingly. No server-side configuration needed — it is a
pure frontend parameter.

---

### 5. Server-Level CORS (`EMBED_ALLOWED_ORIGINS` in `.env`)

Controls which origins are globally permitted to load the embed at the HTTP
level. This is a server-wide setting — not per-partner.

```env
# Leave blank in dev (allows all origins)
EMBED_ALLOWED_ORIGINS=

# In production — set to your partner's exact domain(s)
EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://backoffice.salesplay.io
```

When set:
- `Access-Control-Allow-Origin` is restricted to listed origins only
- `Content-Security-Policy: frame-ancestors` is set to listed origins only

Requires a **server restart** to take effect.

---

### 6. Active / Disabled (`active` flag)

Set `active = 0` to immediately disable a partner key. All requests using that
key will receive a `401 Unauthorized`. Useful for offboarding or key rotation.

```sql
-- Disable a partner key
UPDATE embed_partners SET active = 0 WHERE partner_key = 'YOUR_PARTNER_KEY';

-- Re-enable
UPDATE embed_partners SET active = 1 WHERE partner_key = 'YOUR_PARTNER_KEY';
```

Takes effect immediately — no server restart needed.

---

## How to Create a New Partner

```sql
INSERT INTO embed_partners (
    partner_key,
    partner_name,
    provider_id,
    allowed_origins,
    branding,
    active
) VALUES (
    'sk_live_REPLACE_WITH_SECURE_RANDOM_64_CHARS',
    'Partner Display Name',
    'salesplay',
    'https://app.partner.io',
    JSON_OBJECT(
        'company_name',    'Partner Name',
        'logo_url',        'https://cdn.partner.io/logo.png',
        'primary_color',   '#0055FF',
        'welcome_message', 'Welcome to your analytics dashboard.'
    ),
    1
);
```

**Generate a secure partner key (run once):**

```python
import secrets
print("sk_live_" + secrets.token_urlsafe(48))
```

---

## How to Embed the Widget

```html
<!-- Minimal embed -->
<iframe
  src="https://datamind.ai/embed/widget?pk=YOUR_PARTNER_KEY"
  width="100%"
  height="700"
  frameborder="0"
  allow="clipboard-write"
></iframe>

<!-- With theme and custom height -->
<iframe
  src="https://datamind.ai/embed/widget?pk=YOUR_PARTNER_KEY&theme=light"
  width="100%"
  height="800"
  style="border:none; border-radius:12px;"
  allow="clipboard-write"
></iframe>
```

**Available query parameters:**

| Parameter | Values | Default | Description |
|---|---|---|---|
| `pk` | string | required | Partner key from `embed_partners` |
| `theme` | `dark` \| `light` | `dark` | Widget color theme |

---

## What Is NOT Customizable Yet

The following are designed and stored but not yet fully wired in the frontend:

| Feature | Status | Notes |
|---|---|---|
| Partner logo in header | ⚠️ Stored, not rendered | `logo_url` in branding JSON is available but EmbedApp.jsx doesn't display it yet |
| Custom primary color | ⚠️ Stored, not applied | `primary_color` read but CSS variable injection not implemented |
| Custom welcome message | ⚠️ Stored, not rendered | `welcome_message` available but EmbedOnboarding uses a hardcoded string |
| Support email on errors | ⚠️ Stored, not rendered | `support_email` available but error screens don't reference it |
| Custom analytics template selection | ❌ Not built | All partners see all templates; no per-partner template allowlist |
| Partner self-service dashboard | ❌ Not built | All configuration requires direct SQL on the `embed_partners` table |
| Responsive sizing via postMessage | ❌ Not built | iframe height is set by the embedding page; no dynamic resize messaging yet |
| OAuth redirect inside iframe | ❌ Not built | Post-MVP |

---

## Security Notes

- **Never expose `partner_key` in client-side JavaScript.** It should be embedded
  in the `src` attribute of the iframe by your server-side template, not constructed
  in JavaScript visible to end users.
- **Always set `allowed_origins`** to your exact domain in production. Leaving it
  blank allows any website to embed the widget.
- **Rotate partner keys** if they are ever exposed. Disable the old key
  (`active = 0`) and create a new one — existing user sessions will need to
  re-authenticate.
- **`EMBED_ALLOWED_ORIGINS` in `.env`** must match `allowed_origins` in the DB.
  If they differ, the DB check may pass but the HTTP CORS check will fail (or
  vice versa), causing confusing 403 errors.
