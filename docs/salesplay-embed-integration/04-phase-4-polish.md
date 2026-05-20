# Phase 4 — Polish, postMessage API & Pre-Launch Checklist

## Goal

Finish the integration so it is ready to hand to Salesplay. This includes the postMessage communication API (Salesplay's developers will ask for this), final security checks, and a pre-launch checklist to run before going live.

---

## Step 4.1 — postMessage API

The postMessage API lets Salesplay's app react to events inside the DataMind iframe. For example, Salesplay might want to show a badge when sync completes, or log when a user asks a question.

**In `EmbedApp.jsx`**, add postMessage calls at the key state transitions. The `window.parent.postMessage` calls are already stubbed — here is the full set:

Add this helper function at the top of `EmbedApp.jsx`, above the component:

```jsx
function notifyParent(type, payload = {}) {
  try {
    window.parent.postMessage({ type, ...payload }, '*')
  } catch {
    // iframe may not have a parent (direct browser open) — ignore
  }
}
```

Then call `notifyParent` at the right moments. Here is where each event fires:

| Event Type | Where to Call | Payload |
|---|---|---|
| `dm:ready` | `EmbedApp.jsx` — when context loads successfully | `{ partner_name }` |
| `dm:onboarding_start` | `EmbedOnboarding.jsx` — when wizard mounts | `{}` |
| `dm:sync_complete` | `EmbedOnboarding.jsx` — when pollSync succeeds | `{ rows: totalRows }` |
| `dm:chat_open` | `EmbedApp.jsx` — when state becomes `'chat'` | `{}` |
| `dm:query` | `EmbedChat.jsx` — when a query is sent | `{ question }` |
| `dm:logout` | `EmbedApp.jsx` — when `handleLogout()` is called | `{}` |

**In `EmbedApp.jsx`**, update the `useEffect` that calls `embedValidateContext`:

```jsx
.then(ctx => {
  setContext(ctx)
  notifyParent('dm:ready', { partner_name: ctx.partner_name })  // ADD THIS
  if (existingToken) {
    setState('chat')
    notifyParent('dm:chat_open')                                // ADD THIS
  } else {
    setState('onboarding')
    notifyParent('dm:onboarding_start')                         // ADD THIS
  }
})
```

**In `EmbedOnboarding.jsx`**, update `pollSync` when it resolves successfully:

```jsx
clearInterval(interval)
setTimeout(() => {
  // rows_synced comes from the status endpoint's last_sync_rows field
  notifyParent('dm:sync_complete', { rows: r.last_sync_rows || 0 })  // ADD THIS
  onComplete(token, { email: email.trim().toLowerCase(), name: name.trim() })
}, 600)
```

Because `EmbedOnboarding.jsx` doesn't import `notifyParent`, pass it down from `EmbedApp` as a prop, or duplicate the helper function at the top of `EmbedOnboarding.jsx`.

**In `EmbedChat.jsx`**, add to the `send` function before `setLoading(true)`:

```jsx
notifyParent('dm:query', { question: q })  // ADD THIS
setLoading(true)
```

**Listening to postMessage events from Salesplay's side:**

Salesplay's developers add this to their app to receive events:

```js
window.addEventListener('message', (event) => {
  // Security: only accept messages from your DataMind embed origin
  if (event.origin !== 'https://datamind.ai') return

  const { type, ...data } = event.data
  switch (type) {
    case 'dm:ready':
      console.log('DataMind widget loaded', data.partner_name)
      break
    case 'dm:sync_complete':
      console.log('Sync done, rows:', data.rows)
      break
    case 'dm:chat_open':
      // Show a "New" badge on their chat button, etc.
      break
  }
})
```

**Salesplay can also send messages INTO the iframe:**

```js
const iframe = document.querySelector('iframe[src*="datamind.ai"]')
iframe.contentWindow.postMessage({ type: 'dm:set_theme', theme: 'light' }, 'https://datamind.ai')
```

To handle incoming messages in `EmbedApp.jsx`, add to the `useEffect`:

```jsx
useEffect(() => {
  function handleIncoming(event) {
    // Only accept from known partner origins
    // In production, check event.origin against the partner's allowed_origins
    if (event.data?.type === 'dm:logout') handleLogout()
    if (event.data?.type === 'dm:set_theme') {
      document.documentElement.setAttribute('data-theme', event.data.theme || 'dark')
    }
  }
  window.addEventListener('message', handleIncoming)
  return () => window.removeEventListener('message', handleIncoming)
}, [])
```

---

## Step 4.2 — Branding Config in `embed_partners`

When you have a second partner (not just Salesplay), you'll want each partner's embed to look slightly different. Add branding config to the `embed_partners` table now so you don't need a schema change later.

**Run this migration:**

```sql
ALTER TABLE embed_partners
  ADD COLUMN branding JSON AFTER allowed_origins;
```

**Update the Salesplay row with branding:**

```sql
UPDATE embed_partners
SET branding = JSON_OBJECT(
  'accent_color', '#4f8ef7',
  'logo_emoji',   '🏪',
  'product_name', 'Ask Your SalesPlay Data'
)
WHERE partner_key = 'sp_live_abc123';
```

**Return branding from `/embed/context`:**

In `embed.py`, update the `get_embed_context` return value:

```python
import json

@router.get("/context")
def get_embed_context(pk: str):
    partner = _get_partner(pk)
    if not partner:
        raise HTTPException(status_code=404, detail="Invalid or inactive partner key.")
    branding = {}
    if partner.get("branding"):
        branding = json.loads(partner["branding"]) if isinstance(partner["branding"], str) else partner["branding"]
    return {
        "partner_name": partner["partner_name"],
        "provider_id":  partner["provider_id"],
        "partner_key":  pk,
        "branding":     branding,
    }
```

**Use branding in `EmbedApp.jsx`:**

The `context` object now has `context.branding`. Pass it down to `EmbedOnboarding` and `EmbedChat`. They can use `context.branding.accent_color` or `context.branding.product_name` to customise what is displayed.

---

## Step 4.3 — Pre-Launch Security Checklist

Run through every item before handing the iframe URL to Salesplay.

**Backend:**

- [ ] `SECRET_KEY` env var is set to a long random string (not the default `datamind-secret-change-in-production-2024`)
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(48))"
  ```
- [ ] `ENCRYPTION_KEY` env var is set (used by `integrations.py` to encrypt Salesplay API tokens)
- [ ] `EMBED_ALLOWED_ORIGINS` is set to Salesplay's production domain
- [ ] `DB_POOL_SIZE` is set appropriately for your server
- [ ] The Salesplay partner row uses a real random key, not `sp_dev_test`
- [ ] HTTPS is configured on your server — the iframe **will not load** on HTTP from an HTTPS parent page
- [ ] Test the `/embed/context` endpoint with the production partner key returns correct data
- [ ] Test `/embed/init` with a real Salesplay API token end-to-end in production

**Frontend:**

- [ ] `VITE_API_URL` is set to your production API URL in the `.env.production` file
- [ ] The embed bundle is built with `npm run build` and deployed
- [ ] The embed URL in production is `https://datamind.ai/src/embed/embed.html?pk=sp_live_xxx`
- [ ] Test the embed URL in a browser directly — onboarding wizard should appear
- [ ] Test the embed URL inside an iframe from a different origin — should work without CORS errors

**The iframe tag to give Salesplay:**

```html
<iframe
  src="https://datamind.ai/src/embed/embed.html?pk=sp_live_abc123"
  width="420"
  height="680"
  frameborder="0"
  allow="clipboard-write"
  style="border-radius: 12px;"
></iframe>
```

Give Salesplay this exact tag. The only thing they change is the iframe dimensions to fit their UI layout.

---

## Step 4.4 — Handle the LLM Key Requirement

Looking at `datamind/backend/main.py` line 900–931, the `/query` endpoint calls `_resolve_api_key(user, llm)`. For embed users, they have not set a Gemini or DeepSeek API key in their settings — they will get a `422 Gemini API key not set` error when they try to chat.

You have two options:

**Option A — Server-level fallback key (recommended for embed)**

Set a server-level Gemini API key in your `.env`:

```
GEMINI_API_KEY=AIzaSy...
```

The `_resolve_api_key` function in `main.py` (line 137–143) already has this fallback:

```python
key = s.get("gemini_api_key", "").strip()
if not key:
    # Last resort: server-level env var (for server admins only)
    key = os.getenv("GEMINI_API_KEY", "").strip()
```

So embed users will automatically use the server's Gemini key. The LLM usage will be charged against that key. Make sure you account for this in your billing model — embed users should consume tokens from their DataMind subscription, which pays for your server's LLM costs.

**Option B — Prompt embed users to add their own key**

After sync completes in `EmbedOnboarding.jsx`, add a step 3 that asks the user to add a Gemini API key. This is more setup friction but means users bring their own LLM costs.

For launch, **Option A is strongly recommended** — reducing friction in the iframe is critical. Option B can be offered later as a "power user" setting accessible via the full datamind.ai app.

---

## Step 4.5 — Sync Interval for Embed Users

Embed users' integrations are picked up by the background scheduler in `datamind/backend/integrations.py` (the `_scheduler_tick` function at line 521). The Salesplay manifest (`manifest.json`) sets `sync_interval_minutes: 60`.

This means every Salesplay embed user gets a delta sync every hour automatically. No additional work needed — the scheduler already handles this for all `user_integrations` rows regardless of how they were created.

Verify this is working 1 hour after your first embed user connects by checking the `sync_logs` table:

```sql
SELECT ui.user_email, sl.sync_type, sl.started_at, sl.status, sl.rows_fetched
FROM sync_logs sl
JOIN user_integrations ui ON sl.integration_id = ui.id
ORDER BY sl.started_at DESC
LIMIT 20;
```

You should see `delta` sync entries appearing hourly for every active embed user.

---

## Step 4.6 — Monitoring Queries

Once live, run these queries periodically to understand your embed user base:

**How many embed-originated accounts exist:**

```sql
-- Users created via the embed will have a Salesplay integration but no DB configs.
-- (DB configs are stored in users.settings JSON, integrations in user_integrations table)
SELECT COUNT(DISTINCT ui.user_email) AS embed_users
FROM user_integrations ui
WHERE ui.provider_id = 'salesplay';
```

**Sync health across all embed users:**

```sql
SELECT
  status,
  COUNT(*) AS integrations,
  AVG(last_sync_rows) AS avg_rows,
  MAX(last_sync_at) AS most_recent_sync
FROM user_integrations
WHERE provider_id = 'salesplay'
GROUP BY status;
```

**Users who connected but never synced successfully:**

```sql
SELECT user_email, status, last_error, created_at
FROM user_integrations
WHERE provider_id = 'salesplay'
  AND status = 'error'
ORDER BY created_at DESC;
```

---

## Step 4.7 — What to Tell Salesplay

Send this short integration guide to Salesplay's developers:

---

**DataMind Embed Integration Guide for Salesplay**

**Step 1 — Add the iframe:**

```html
<iframe
  src="https://datamind.ai/src/embed/embed.html?pk=YOUR_PARTNER_KEY"
  width="420"
  height="680"
  frameborder="0"
  allow="clipboard-write"
  style="border-radius: 12px;"
></iframe>
```

Replace `YOUR_PARTNER_KEY` with the key we provide.

**Step 2 — Listen for events (optional):**

```js
window.addEventListener('message', (event) => {
  if (event.origin !== 'https://datamind.ai') return
  console.log('DataMind event:', event.data.type, event.data)
})
```

Events you will receive:
- `dm:ready` — widget loaded, partner validated
- `dm:onboarding_start` — user is new, starting setup
- `dm:sync_complete` — data sync finished, `{ rows: N }`
- `dm:chat_open` — user is in chat mode
- `dm:query` — user asked a question, `{ question: "..." }`
- `dm:logout` — user disconnected

**Step 3 — That's it.**

No backend changes on Salesplay's side. Users authenticate against DataMind directly inside the iframe. Their accounts at datamind.ai are automatically created.

---

## Final File Structure After All Phases

```
datamind/
├── backend/
│   ├── main.py              (modified: +embed router, +pool init, +CORS, +rate limiter)
│   ├── embed.py             (NEW)
│   ├── pool.py              (NEW)
│   ├── auth.py              (modified: _get_conn → pool)
│   ├── integrations.py      (modified: _get_internal_conn → pool)
│   ├── billing.py           (modified: _get_conn → pool)
│   └── ... all other files unchanged
│
└── frontend/
    ├── vite.config.js       (modified: +embed entry point)
    └── src/
        ├── embed/
        │   ├── embed.html   (NEW)
        │   ├── embed.css    (NEW)
        │   ├── EmbedApp.jsx (NEW)
        │   ├── EmbedOnboarding.jsx (NEW)
        │   ├── EmbedChat.jsx (NEW)
        │   └── embedApi.js  (NEW)
        └── ... all other files unchanged

docs/salesplay-embed-integration/
├── 00-overview.md
├── 01-phase-1-backend.md
├── 02-phase-2-frontend.md
├── 03-phase-3-scale.md
└── 04-phase-4-polish.md     ← this file
```

Total new files: **8**
Total modified files: **5**
Zero existing user flows affected.
