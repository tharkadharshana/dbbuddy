# Salesplay Embed Integration — Complete Reference

## Table of Contents

1. [How the System Works](#1-how-the-system-works)
2. [The Iframe Tag](#2-the-iframe-tag)
3. [Customising the Widget](#3-customising-the-widget)
4. [Partner Management](#4-partner-management)
5. [API Reference](#5-api-reference)
6. [postMessage Event Reference](#6-postmessage-event-reference)
7. [User Account Lifecycle](#7-user-account-lifecycle)
8. [Running the Test Suite](#8-running-the-test-suite)
9. [Monitoring & Operations](#9-monitoring--operations)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. How the System Works

### The complete user journey

```
Salesplay app (salesplay.io)
  <iframe src="https://datamind.ai/src/embed/embed.html?pk=sp_live_xxx">

    EmbedApp.jsx loads
      |
      +--> GET /embed/context?pk=sp_live_xxx
      |      Returns: partner_name, provider_id, branding
      |
      +--> Check localStorage for dm_embed_token
             |
             YES (returning user) -----> EmbedChat.jsx
             |                              POST /query  (same as main app)
             |
             NO (new user) -----------> EmbedOnboarding.jsx
                                          Step 0: Enter Salesplay API key
                                            POST /embed/validate-token  (no auth needed)
                                          Step 1: Create DataMind account
                                            (or log in to existing one)
                                          Step 2: Connect + sync
                                            POST /embed/init
                                              creates DataMind account
                                              starts free trial
                                              connects Salesplay provider
                                              kicks off background sync
                                            polls GET /providers/salesplay/status
                                            on 'connected' -> stores dm_embed_token
                                                           -> EmbedChat.jsx
```

### What is and is NOT changed

Every embed user gets a **real, full DataMind account**. The embed is just an acquisition funnel — after going through the iframe wizard once, users can log in at datamind.ai directly with the same credentials and access forecasting, reports, anomaly detection, and everything else.

| Component | Changed for embed? |
|---|---|
| `/query` endpoint | No — embed uses it unchanged |
| `/auth/register` + `/auth/login` | No |
| `/providers/connect` + sync | No |
| Billing / trial system | No |
| User data isolation (table prefixes) | No |
| Existing user flows | No |
| CORS config | Yes — env-driven instead of hardcoded `"*"` |
| New backend endpoints | Yes — 3 new routes under `/embed/*` |
| New frontend bundle | Yes — separate Vite entry at `src/embed/` |
| New DB table | Yes — `embed_partners` |

---

## 2. The Iframe Tag

### Minimum tag

```html
<iframe
  src="https://datamind.ai/src/embed/embed.html?pk=YOUR_PARTNER_KEY"
  width="420"
  height="680"
  frameborder="0"
></iframe>
```

### Recommended production tag

```html
<iframe
  src="https://datamind.ai/src/embed/embed.html?pk=sp_live_abc123"
  width="420"
  height="680"
  frameborder="0"
  allow="clipboard-write"
  loading="lazy"
  title="Ask Your Salesplay Data"
  style="border-radius: 12px; border: none; box-shadow: 0 4px 24px rgba(0,0,0,0.15);"
></iframe>
```

### URL parameters

| Parameter | Required | Description |
|---|---|---|
| `pk` | Yes | Partner key. Issued by DataMind when a partner is registered in `embed_partners`. |

### Recommended iframe dimensions

| Use case | Width | Height |
|---|---|---|
| Sidebar panel | 360–420px | 100% of viewport height |
| Floating chat bubble (open state) | 400px | 600–680px |
| Full-width bottom drawer | 100% | 480px |
| Embedded in a dashboard tile | 100% | 500px |

The widget is responsive — it adapts to any width above 320px. Below 320px the table results become hard to read.

---

## 3. Customising the Widget

### 3.1 Branding via the database

The quickest way to customise the widget is via the `branding` JSON column in `embed_partners`. No code changes needed — update the row and the iframe picks it up on next load.

```sql
UPDATE embed_partners
SET branding = JSON_OBJECT(
  'accent_color',  '#f59e0b',
  'product_name',  'Ask Your Salesplay Data',
  'logo_emoji',    'POS'
)
WHERE partner_key = 'sp_live_abc123';
```

| Branding key | Type | Effect |
|---|---|---|
| `accent_color` | CSS colour (`#hex` or `rgb()`) | Replaces the blue highlight colour (`--blue` CSS var) across all buttons, links, charts, and progress bars |
| `product_name` | String | Widget title shown in the header and onboarding wizard. Defaults to `"DataMind AI"` / `"Ask Your Data"` |
| `logo_emoji` | String (1–3 chars) | Reserved for future use — not yet rendered |

The `accent_color` is applied as a CSS custom property at runtime:

```js
// EmbedApp.jsx line ~100
const accentColor = context?.branding?.accent_color
if (accentColor) {
  document.documentElement.style.setProperty('--blue', accentColor)
}
```

### 3.2 Dimensions and positioning

All sizing is controlled from the embedding app (Salesplay's side) via the `<iframe>` tag's `width` and `height` attributes or CSS. The widget does not impose minimum dimensions via JavaScript.

**Floating chat bubble pattern (Salesplay's HTML):**

```html
<style>
  #dm-bubble {
    position: fixed;
    bottom: 24px;
    right: 24px;
    width: 420px;
    height: 640px;
    border-radius: 16px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.25);
    border: none;
    display: none;       /* hidden by default */
    z-index: 9999;
  }
  #dm-bubble.open { display: block; }
</style>

<button onclick="document.getElementById('dm-bubble').classList.toggle('open')">
  Ask Your Data
</button>

<iframe
  id="dm-bubble"
  src="https://datamind.ai/src/embed/embed.html?pk=sp_live_abc123"
  allow="clipboard-write"
  title="Ask Your Data"
></iframe>
```

### 3.3 CSS variables available for theming

All colours inside the widget are CSS custom properties. They are set in `datamind/frontend/src/embed/embed.css` and can be overridden by injecting a `<style>` tag before the widget loads, or via the `accent_color` branding field.

| Variable | Default | Usage |
|---|---|---|
| `--blue` | `#4f8ef7` | Primary action colour — buttons, links, charts, progress bars |
| `--green` | `#34d17a` | Success states, positive % values |
| `--red` | `#f05050` | Error states, negative values |
| `--bg` | `#0f1117` | Page background |
| `--bg1` | `#14161f` | Card / input background |
| `--bg2` | `#1a1d2e` | Secondary surface |
| `--bg3` | `#22263a` | Tertiary surface / disabled state |
| `--text` | `#e8eaf6` | Primary text |
| `--text2` | `#9ca3c8` | Secondary text |
| `--text3` | `#5a6080` | Placeholder / hint text |
| `--border` | `rgba(255,255,255,0.08)` | Subtle dividers |

### 3.4 Suggestion chips

The four quick-prompt buttons shown on the empty chat state are defined at the top of `datamind/frontend/src/embed/EmbedChat.jsx`:

```js
const SUGGESTIONS = [
  { icon: '💰', text: 'What was my total revenue last month?' },
  { icon: '📦', text: 'Which products are selling the fastest?' },
  { icon: '👥', text: 'Who are my top 10 customers?' },
  { icon: '📍', text: 'Compare sales across all my locations' },
]
```

Edit this array to change the prompts. Good prompts are short, use plain English, and work against Salesplay data (receipts, products, customers, shops).

### 3.5 Widget dimensions at runtime

The widget does not communicate its preferred size to the parent. If you want the parent to resize the iframe based on content (e.g. make it taller when results appear), use postMessage:

```js
// Inside EmbedChat.jsx — fire this after results render
window.parent.postMessage({ type: 'dm:resize', height: 800 }, '*')

// In Salesplay's JS:
window.addEventListener('message', e => {
  if (e.data.type === 'dm:resize') {
    document.getElementById('dm-bubble').style.height = e.data.height + 'px'
  }
})
```

This event is not implemented by default — add it to `EmbedChat.jsx` if you need it.

### 3.6 Light mode

The widget defaults to dark mode. To force light mode, set CSS variables via the `accent_color` approach or inject a class:

There is no built-in light theme yet. To add one, duplicate the CSS variable block in `embed.css` under a `.light` class and toggle it via a postMessage from the parent:

```js
// Parent sends:
iframe.contentWindow.postMessage({ type: 'dm:set_theme', theme: 'light' }, '*')

// EmbedApp.jsx already listens for this:
if (event.data?.type === 'dm:set_theme') {
  document.documentElement.setAttribute('data-theme', event.data.theme || 'dark')
}
```

---

## 4. Partner Management

### 4.1 Registering a new partner

Every partner (e.g. Salesplay) needs one row in `embed_partners`. This is a one-time setup.

**Step 1 — Generate a partner key:**

```python
import secrets
key = "sp_live_" + secrets.token_urlsafe(18)
print(key)   # e.g. sp_live_X7kQmPzR4nVwAcLs2T
```

**Step 2 — Insert the row:**

```sql
INSERT INTO embed_partners (
  partner_key,
  partner_name,
  provider_id,
  allowed_origins,
  branding
) VALUES (
  'sp_live_X7kQmPzR4nVwAcLs2T',
  'Salesplay',
  'salesplay',
  'https://app.salesplay.io,https://backoffice.salesplay.io',
  JSON_OBJECT(
    'accent_color', '#f59e0b',
    'product_name', 'Ask Your Salesplay Data'
  )
);
```

**Step 3 — Set the env var and deploy:**

```
EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://backoffice.salesplay.io
```

**Step 4 — Give Salesplay the iframe tag:**

```html
<iframe
  src="https://datamind.ai/src/embed/embed.html?pk=sp_live_X7kQmPzR4nVwAcLs2T"
  width="420" height="680" frameborder="0"
  allow="clipboard-write"
></iframe>
```

### 4.2 Adding a second partner (e.g. Loyverse POS)

If you want to embed for Loyverse users as well, insert a second row pointing at the `loyverse` provider:

```sql
INSERT INTO embed_partners (partner_key, partner_name, provider_id, allowed_origins)
VALUES (
  'ly_live_YourKeyHere',
  'Loyverse',
  'loyverse',
  'https://app.loyverse.com'
);
```

The `provider_id` must match a value in the `providers/` directory (`salesplay` or `loyverse`).

### 4.3 Temporarily disabling a partner

Set `active = 0` — the iframe will show an error screen and no new users can onboard. Existing users with a stored `dm_embed_token` will also be blocked.

```sql
UPDATE embed_partners SET active = 0 WHERE partner_key = 'sp_live_xxx';
```

Re-enable with `SET active = 1`.

### 4.4 Rotating a partner key

Generate a new key, insert a second row, give Salesplay the new tag, wait for them to deploy, then delete the old row.

```sql
-- 1. Add new key
INSERT INTO embed_partners (partner_key, partner_name, provider_id, allowed_origins)
VALUES ('sp_live_NewKey', 'Salesplay', 'salesplay', 'https://app.salesplay.io');

-- 2. After Salesplay deploys the new iframe tag, remove the old key
DELETE FROM embed_partners WHERE partner_key = 'sp_live_OldKey';
```

### 4.5 `embed_partners` table schema

```sql
CREATE TABLE embed_partners (
    partner_key     VARCHAR(64) PRIMARY KEY,
    partner_name    VARCHAR(128) NOT NULL,
    provider_id     VARCHAR(50)  NOT NULL,   -- must match providers/ directory
    allowed_origins TEXT         NOT NULL,   -- comma-separated, used for CORS + CSP
    branding        JSON,                    -- accent_color, product_name
    active          TINYINT(1)   DEFAULT 1,
    created_at      DATETIME     DEFAULT NOW()
);
```

---

## 5. API Reference

All three embed-specific endpoints live in `datamind/backend/embed.py` and are mounted under `/embed`.

### `GET /embed/context`

**Authentication:** None required.

**Purpose:** Called by the iframe on load to validate the partner key and get context. If this returns 404, the widget shows an error screen.

**Query params:**

| Param | Required | Description |
|---|---|---|
| `pk` | Yes | Partner key |

**Response (200):**

```json
{
  "partner_name": "Salesplay",
  "provider_id":  "salesplay",
  "partner_key":  "sp_live_abc123",
  "branding": {
    "accent_color": "#f59e0b",
    "product_name": "Ask Your Salesplay Data"
  }
}
```

**Errors:**

| Code | Reason |
|---|---|
| `404` | Partner key not found or `active = 0` |
| `422` | `pk` query param missing |

---

### `POST /embed/validate-token`

**Authentication:** None required (the partner key acts as the gate).

**Purpose:** Validates a Salesplay API token before the user creates a DataMind account. This is Step 0 of the onboarding wizard. The reason this endpoint exists rather than reusing `POST /providers/validate` is that `/providers/validate` requires a JWT — and new users have no account yet.

**Request body:**

```json
{
  "partner_key": "sp_live_abc123",
  "api_token":   "eyJhbGciOiJSUzI1NiJ9..."
}
```

**Response (200, token valid):**

```json
{
  "ok": true,
  "error": null,
  "details": {
    "merchant_name": "My Business Name",
    "shop_count": 3,
    "currency": "USD",
    "merchant_id": "abc123"
  }
}
```

**Response (200, token invalid):**

```json
{
  "ok": false,
  "error": "SalesPlay API token is invalid or expired.",
  "details": null
}
```

**Errors:**

| Code | Reason |
|---|---|
| `404` | Partner key invalid or inactive |

---

### `POST /embed/init`

**Authentication:** None required (creates the account in this call).

**Rate limit:** 5 requests per 60 seconds per IP address. Returns `429` when exceeded.

**Purpose:** One-shot onboarding. In a single call: validates partner key → creates DataMind account → starts free trial → connects Salesplay provider → kicks off background sync → returns JWT.

After this call, the iframe switches to chat mode and uses standard endpoints (`/query`, `/providers/*/status`, etc.) with the returned JWT.

**Request body:**

```json
{
  "partner_key": "sp_live_abc123",
  "api_token":   "eyJhbGciOiJSUzI1NiJ9...",
  "name":        "Jane Smith",
  "email":       "jane@mycompany.com",
  "password":    "securepassword"
}
```

**Response (200):**

```json
{
  "token":       "eyJhbGciOiJIUzI1NiJ9...",
  "user":        { "name": "Jane Smith", "email": "jane@mycompany.com" },
  "provider_id": "salesplay",
  "sync":        "started"
}
```

**Errors:**

| Code | Reason |
|---|---|
| `400` | Email already registered with a different password |
| `404` | Partner key invalid or inactive |
| `422` | Salesplay API token failed validation |
| `429` | Rate limit exceeded (5 calls/min per IP) |
| `500` | Unexpected error connecting provider |

**What happens when the email is already registered:**
If the user already has a DataMind account and enters their existing email + correct password, the endpoint re-authenticates them (no duplicate account is created) and reconnects the Salesplay provider. If the password is wrong, it returns `400` with a message to log in at datamind.ai.

---

## 6. postMessage Event Reference

The embed widget communicates with the parent window via `window.parent.postMessage`. All events use `'*'` as the target origin in development. In production, restrict to your domain.

### Events sent FROM the iframe TO the parent

| Event type | When fired | Payload |
|---|---|---|
| `dm:ready` | Partner key validated on load | `{ partner_name: "Salesplay" }` |
| `dm:onboarding_start` | New user — wizard shown | `{}` |
| `dm:onboarding_sync_started` | Sync kicked off (Step 2 of wizard) | `{}` |
| `dm:sync_complete` | Sync finished successfully | `{ rows: 42000 }` |
| `dm:chat_open` | User is in chat mode (new or returning) | `{}` |
| `dm:query` | User submitted a question | `{ question: "total revenue last month?" }` |
| `dm:logout` | User clicked Disconnect | `{}` |

**Listening on the Salesplay side:**

```js
window.addEventListener('message', (event) => {
  // Security: only accept messages from your DataMind origin
  if (event.origin !== 'https://datamind.ai') return

  const { type, ...data } = event.data
  switch (type) {
    case 'dm:ready':
      console.log('DataMind widget loaded for', data.partner_name)
      break
    case 'dm:sync_complete':
      // Show a notification badge: "Your data is ready!"
      showBadge(`Synced ${data.rows.toLocaleString()} records`)
      break
    case 'dm:chat_open':
      // Remove the "New" badge on your chat button
      document.querySelector('.dm-badge')?.remove()
      break
    case 'dm:query':
      // Log analytics for your own product telemetry
      analytics.track('datamind_query', { question: data.question })
      break
  }
})
```

### Commands sent FROM the parent TO the iframe

| Command type | Effect | Payload |
|---|---|---|
| `dm:logout` | Clears the stored token and returns to onboarding | `{}` |
| `dm:set_theme` | Switches the widget theme | `{ theme: 'light' \| 'dark' }` |

**Sending from the Salesplay side:**

```js
const iframe = document.querySelector('iframe[src*="datamind.ai"]')

// Force logout (e.g. when the Salesplay user logs out of Salesplay)
iframe.contentWindow.postMessage({ type: 'dm:logout' }, 'https://datamind.ai')

// Switch theme to match your app's current theme
iframe.contentWindow.postMessage({ type: 'dm:set_theme', theme: 'light' }, 'https://datamind.ai')
```

---

## 7. User Account Lifecycle

### Account creation

Every Salesplay user who completes the onboarding wizard gets a full DataMind account. Their account is identical to one created via datamind.ai — same tables, same billing system, same API.

```
embed onboarding               datamind.ai
       |                            |
POST /embed/init ---------> users table (email PK)
       |                            |
       +---> user_integrations ---> same table
       |
       +---> start_trial() -------> user_subscriptions
```

### Accessing the full app

After the first embed session, any user can visit `datamind.ai`, click Log In, and use the email/password they entered in the iframe. They get the full experience — sidebar, analytics, forecasting, reports, account settings — not just the chat widget.

This is intentional and is the upgrade path: embed users who love the product discover and upgrade to the full experience.

### Account deletion

If a user deletes their account from the full app (`DELETE /auth/account`), their data is fully removed — including from the embed. Next time they open the iframe they will see the onboarding wizard again.

### Data sync schedule

Embed users' integrations are managed by the same background scheduler as all other integrations. The Salesplay provider runs a delta sync every 60 minutes (set in `datamind/backend/providers/salesplay/manifest.json` → `sync_interval_minutes: 60`). No additional configuration is needed.

---

## 8. Running the Test Suite

The test suite at `tests/test_embed_integration.py` covers the 7 automated checks (Steps 8 and 9 from the manual testing guide, plus 5 additional sanity checks).

**Requirements:**
- Backend running at `http://localhost:8000`
- A DataMind account that has already connected Salesplay (created during the manual Step 5 test)
- `requests` Python package: `pip install requests`

**Run:**

```bash
python tests/test_embed_integration.py --email YOUR_EMAIL --password YOUR_PASSWORD
```

**Tests covered:**

| Test | What it verifies |
|---|---|
| Test 1 — context endpoint | Valid key → 200, invalid → 404, missing param → 422 |
| Test 2 — validate-token | Works without JWT (the Step-5 bug fix) |
| Test 3 — security headers | `frame-ancestors` + `nosniff` on embed routes only |
| Test 7 — bad Salesplay token | `/embed/init` returns 422, not 500 |
| Step 8 — rate limiter | `/embed/init` returns 429 after 5 calls per 60s per IP |
| Step 9 — pool under load | 30 concurrent authenticated requests all return 200 |
| Test 6 — pool recovery | Single request works normally after a concurrent burst |

**Note on the rate limiter test:** The test fires 10 calls and checks that SOME pass and SOME get 429. It does not assert exact counts because earlier tests in the same run may consume some of the 5-call budget. To test with a clean slate, wait 60 seconds between runs.

---

## 9. Monitoring & Operations

### Checking embed user activity

```sql
-- Total Salesplay embed users
SELECT COUNT(DISTINCT user_email) AS embed_users
FROM user_integrations
WHERE provider_id = 'salesplay';

-- Breakdown by integration status
SELECT status, COUNT(*) AS count, AVG(last_sync_rows) AS avg_rows
FROM user_integrations
WHERE provider_id = 'salesplay'
GROUP BY status;

-- Users who connected but never synced successfully
SELECT user_email, status, last_error, created_at
FROM user_integrations
WHERE provider_id = 'salesplay' AND status = 'error'
ORDER BY created_at DESC;

-- Recent sync activity (last 24 hours)
SELECT ui.user_email, sl.sync_type, sl.started_at,
       sl.rows_fetched, sl.status, sl.error_message
FROM sync_logs sl
JOIN user_integrations ui ON sl.integration_id = ui.id
WHERE sl.started_at >= NOW() - INTERVAL 1 DAY
  AND ui.provider_id = 'salesplay'
ORDER BY sl.started_at DESC;

-- Average sync time
SELECT
  AVG(TIMESTAMPDIFF(SECOND, started_at, finished_at)) AS avg_sync_seconds,
  MAX(TIMESTAMPDIFF(SECOND, started_at, finished_at)) AS max_sync_seconds,
  COUNT(*) AS total_syncs
FROM sync_logs sl
JOIN user_integrations ui ON sl.integration_id = ui.id
WHERE ui.provider_id = 'salesplay' AND sl.status = 'success';
```

### Checking connection pool health

```sql
-- MySQL: current active connections
SHOW STATUS LIKE 'Threads_connected';

-- How close to the limit you are
SHOW VARIABLES LIKE 'max_connections';

-- Current pool usage pattern (run from Python)
-- python -c "from pool import get_pool; p = get_pool(); print(p.pool_size)"
```

### Embed partner table

```sql
-- List all registered embed partners
SELECT partner_key, partner_name, provider_id, active, created_at,
       JSON_EXTRACT(branding, '$.accent_color') AS accent
FROM embed_partners;

-- Check which origins are allowed
SELECT partner_name, allowed_origins FROM embed_partners WHERE active = 1;
```

---

## 10. Troubleshooting

### "Invalid or inactive partner key" on iframe load

The widget shows an error screen immediately on load.

**Causes and fixes:**

1. **The `embed_partners` table doesn't exist yet** — the backend hasn't started after the Phase 1 deployment. Restart the backend; `bootstrap_embed_tables()` runs on startup and creates it.

2. **The seed row hasn't been inserted** — run the `INSERT INTO embed_partners` SQL from Phase 1.

3. **Wrong `pk` in the iframe URL** — verify the `?pk=` value exactly matches `partner_key` in the database.

4. **`active = 0`** — re-enable: `UPDATE embed_partners SET active = 1 WHERE partner_key = '...';`

---

### "Not authenticated" on API token verification (Step 0)

This was a bug in the initial implementation. `POST /providers/validate` requires a JWT, but Step 0 runs before account creation. The fix adds a dedicated unauthenticated endpoint `POST /embed/validate-token`.

If you still see this error after the fix, the backend hasn't reloaded. Restart it.

---

### Sync stuck on "Syncing…" forever

The progress bar animates indefinitely but the chat never appears.

**Check the sync log:**

```sql
SELECT sl.status, sl.error_message, sl.started_at, sl.finished_at
FROM sync_logs sl
JOIN user_integrations ui ON sl.integration_id = ui.id
WHERE ui.user_email = 'user@example.com'
ORDER BY sl.started_at DESC
LIMIT 5;
```

**Common causes:**

| `status` in sync_logs | Cause | Fix |
|---|---|---|
| `running` (no `finished_at`) | Sync thread is still running | Wait — large accounts (years of receipts) can take 5–10 min |
| `error` | Salesplay API rejected mid-sync | Check `error_message`, try manual sync |
| No row exists | `/embed/init` failed silently | Check backend logs |

The frontend polls for 3 minutes then auto-advances the user to chat even if sync hasn't confirmed completion (in `EmbedOnboarding.jsx` → `pollSync`, after 90 × 2s = 180s).

---

### "Too many connections" errors under load

MySQL's `max_connections` is being hit.

**Fix in order:**

1. Check current load: `SHOW STATUS LIKE 'Threads_connected';`
2. Increase MySQL limit: `SET GLOBAL max_connections = 300;` (make permanent in `my.cnf`)
3. Reduce `DB_POOL_SIZE` if running multiple server instances
4. Add a read replica for read-heavy workloads (status checks, queries)

---

### Rate limiter blocks legitimate users

The in-memory rate limiter (`_rate_store` in `embed.py`) is per IP. If multiple Salesplay users are behind a corporate NAT and share a single IP, they all share the same 5-call budget.

**Workaround:** Increase the rate limit in `embed.py`:

```python
_RATE_LIMIT  = 20   # raise from 5
_RATE_WINDOW = 60   # seconds
```

Or switch to a per-email rate limit:

```python
def _check_rate(req: EmbedInitRequest):
    key = req.email.lower()   # rate by email instead of IP
    ...
```

---

### Widget appears but chat returns empty results

The user's Salesplay data sync may not have completed, or they connected a Salesplay account with no receipts.

**Check:**

```sql
SELECT TABLE_NAME,
       (SELECT COUNT(*) FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = t.TABLE_NAME) AS exists_flag
FROM information_schema.TABLES t
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME LIKE 'dm_%salesplay_receipts';
```

If the table exists but has 0 rows, the sync ran but found no data. If the table doesn't exist, the sync didn't create tables yet — check `user_integrations.status`.

---

### iframe doesn't load in production (X-Frame-Options or CSP error)

The browser blocks the iframe from loading.

**Check:**

1. **`EMBED_ALLOWED_ORIGINS` is set but doesn't include the parent domain** — the `Content-Security-Policy: frame-ancestors` header rejects it. Add the exact origin:
   ```
   EMBED_ALLOWED_ORIGINS=https://app.salesplay.io,https://backoffice.salesplay.io
   ```

2. **Parent app is on HTTPS but DataMind is on HTTP** — browsers block HTTP iframes inside HTTPS pages. DataMind must be on HTTPS in production.

3. **A legacy `X-Frame-Options` header is set elsewhere** — check for any nginx/CDN config that adds `X-Frame-Options: SAMEORIGIN` globally and exclude the `/src/embed/` path.
