# Salesplay Web Embedded Widget

## Overview

The Salesplay web embedded widget is an iframe that Salesplay embeds inside their backoffice application. It gives Salesplay users AI-powered analytics on their own sales data without leaving the Salesplay interface. The entire setup — account creation, data sync, API key management — is fully automatic. The user only sees a consent screen, then chat.

This integration is **Salesplay-specific**. Loyverse and all other partners use a separate manual onboarding flow that is completely unaffected.

---

## Architecture

```
Salesplay Backoffice (predev5backoffice.nvision.lk)
  │
  ├─ JavaScript snippet reads app_access_token from browser cookie
  ├─ Builds iframe URL: ?pk=sp_live_XXX&aat=TOKEN
  └─ Renders <iframe src="https://datamind.ai/src/embed/embed.html?pk=...&aat=...">
       │
       └─ DataMind Widget (React, runs at datamind.ai origin)
            │
            ├─ Reads pk + aat from its own URL params
            ├─ Validates partner key: GET /embed/context?pk=...
            ├─ Shows consent screen (user must accept)
            │
            └─ On Accept: POST /embed/salesplay/onboard { partner_key, aat }
                 │
                 └─ DataMind Backend
                      ├─ GET predev5api.nvision.lk/v2.0/public/app/profile
                      │    (server-to-server, no CORS) → email, name
                      │
                      ├─ Check users table → does account exist?
                      ├─ Check user_integrations → do credentials exist?
                      │
                      ├─ If no credentials:
                      │    POST predev5api.nvision.lk/v2.0/public/app/integrations/access_tokens
                      │    (server-to-server) → salesplay_api_token
                      │
                      ├─ Create DataMind account if new user
                      │    password = email.rsplit('.', 1)[0]  e.g. john@gmail.com → john@gmail
                      │
                      ├─ Store credentials in user_integrations (encrypted)
                      ├─ Trigger background data sync
                      └─ Return { jwt, user, sync: "started"|"skipped" }
                           │
                           └─ Widget: polls sync status → shows progress → opens chat
```

---

## Why the API Calls Are Made from the Backend (Not the Widget)

The Salesplay API enforces CORS and only allows requests from `predev5backoffice.nvision.lk`. Our widget runs at `datamind.ai`, so direct browser calls from the iframe are blocked. Server-to-server calls have no CORS restrictions, so our backend proxies these calls on behalf of the widget.

```
Widget (browser, datamind.ai) → Our backend → Salesplay API   ✓ (no CORS)
Widget (browser, datamind.ai) → Salesplay API directly         ✗ (CORS blocked)
```

---

## Flow Details

### First-Time User
1. Salesplay embeds widget with `?pk=sp_live_XXX&aat=SESSION_TOKEN`
2. Widget loads, validates partner key, shows consent screen
3. User reads consent and clicks **Accept & Connect**
4. Widget calls `POST /embed/salesplay/onboard`
5. Backend fetches Salesplay profile → gets `email`, `name`
6. No DataMind account → creates one (auto-generated password)
7. No Salesplay credentials → creates integration access token via Salesplay API
8. Stores encrypted credentials in `user_integrations`
9. Triggers full data sync in background
10. Returns JWT → widget polls sync progress → opens chat

### Returning User (Credentials Already Exist)
1. Same embed URL with fresh `aat`
2. Widget loads, consent already accepted → `dm_embed_token` in localStorage
3. Widget goes straight to chat — no onboarding shown

### Returning User (New Device / Cleared Storage)
1. Widget loads, no `dm_embed_token` found
2. Shows consent screen
3. User accepts → `POST /embed/salesplay/onboard`
4. Backend: finds existing account + existing credentials
5. Skips token creation and sync — just issues a new JWT
6. `sync: "skipped"` → widget goes straight to chat

---

## Key Design Decisions

### Password Derivation
Auto-created accounts use a deterministic password derived from the user's email:
```
john2@gmail.com  →  john2@gmail
test@company.io  →  test@company
```
Formula: `email.rsplit('.', 1)[0]`

This is intentional for the embed flow — users authenticate via Salesplay's session, not a DataMind password. If they later want to log into datamind.ai directly, they can use the password reset flow.

### Skip Credential Validation
The standard `connect_integration()` function validates API tokens against the Salesplay developer API (`api.salesplaypos.com`). In the embed onboard flow we skip this validation (`skip_validation=True`) because:
1. We just created the token from Salesplay's own API — it is guaranteed valid
2. The backoffice API token format may differ from what the developer API validates

### Token Storage
The Salesplay integration access token (not the session `aat`) is stored encrypted in `user_integrations.credentials_enc` as `{"api_token": "eyJ..."}`. This is the long-lived token used for ongoing data sync.

The session `aat` is never stored — it is only used during onboarding and is scoped to the user's current Salesplay session.

---

## Database Tables

### `embed_partners`
Stores partner configuration. One row per partner (Salesplay, Loyverse, etc.).

| Column | Value for Salesplay |
|---|---|
| `partner_key` | `sp_live_XXXXXXXXXXXXXXXXXXXXXXXX` |
| `partner_name` | `Salesplay` |
| `provider_id` | `salesplay` |
| `allowed_origins` | `https://app.salesplay.io,https://backoffice.salesplay.io` |
| `branding` | `{"accent_color": "#f59e0b", "product_name": "Ask Your Salesplay Data"}` |
| `active` | `1` |

To add or update: edit `datamind/backend/scripts/seed_embed_partners.py` and run:
```bash
cd datamind/backend
python scripts/seed_embed_partners.py
```

### `users`
Standard DataMind user accounts. Auto-created for Salesplay users with:
- `email` = from Salesplay profile
- `name` = `full_name` from Salesplay profile
- `password_hash` = bcrypt of derived password

### `user_integrations`
Stores encrypted Salesplay API token per user. One row per `(user_email, provider_id)` pair.

| Column | Description |
|---|---|
| `user_email` | User's email (FK to `users`) |
| `provider_id` | `salesplay` |
| `credentials_enc` | Encrypted `{"api_token": "eyJ..."}` |
| `status` | `active` / `syncing` / `error` |
| `table_prefix` | e.g. `dm_a1b2c3d4e5f6_salesplay` |

---

## Backend Endpoints

All embed endpoints live in `datamind/backend/embed.py` at prefix `/embed`.

### `POST /embed/salesplay/onboard` ← Main endpoint used by widget
**Request:**
```json
{ "partner_key": "sp_live_XXX", "aat": "eyJ..." }
```
**Response:**
```json
{
  "token": "<datamind_jwt>",
  "user": { "name": "John", "email": "john@example.com" },
  "provider_id": "salesplay",
  "is_new_user": true,
  "sync": "started"
}
```
- `sync: "started"` → new credentials were created, sync running in background
- `sync: "skipped"` → credentials already existed, goes straight to chat

### `GET /embed/context?pk=...`
Called by the widget on load to validate the partner key and get branding config.

### `POST /embed/salesplay/profile` ← Proxy (for testing/debugging)
Backend proxy: fetches Salesplay profile using `aat`. Returns `{ email, name }`.

### `POST /embed/salesplay/create-token` ← Proxy (for testing/debugging)
Backend proxy: creates Salesplay integration access token using `aat`. Returns `{ token }`.

### `POST /embed/salesplay/check-user` ← Utility (for testing/debugging)
Checks whether a DataMind account and Salesplay credentials exist for a given email.

### `POST /embed/salesplay/auto-init` ← Legacy (kept for reference)
Older multi-step endpoint, superseded by `/onboard`. Widget no longer calls this.

---

## Frontend Components

All embed frontend lives in `datamind/frontend/src/embed/`.

### `EmbedApp.jsx`
Root state machine. Reads `pk` and `aat` from URL params.

**Routing logic for Salesplay:**
- If `provider_id === 'salesplay'` AND `dm_embed_token` exists → go to chat
- If `provider_id === 'salesplay'` AND no token → show `EmbedSalesplayAutoInit`
- All other partners → show `EmbedOnboarding` (4-step manual wizard)

### `EmbedSalesplayAutoInit.jsx`
Salesplay-specific onboarding component. Phases:

| Phase | What user sees |
|---|---|
| `consent` | Data access list + "Accept & Connect" button |
| `loading` | "Setting up your account…" spinner |
| `sync` | Progress bar + row count + checklist |
| `error` | Error message + "Try Again" button |

### `EmbedOnboarding.jsx`
Manual 4-step wizard used by all other partners. **Not used for Salesplay.**

### `EmbedChat.jsx`
Chat interface shown after onboarding completes. Shared by all partners.

### `embedApi.js`
Axios client. Key functions for Salesplay:
- `salesplayOnboard(partnerKey, aat)` → `POST /embed/salesplay/onboard`
- `embedGetProviderStatus(connectionId)` → sync polling
- `embedGetSSOHandoff()` → `POST /auth/sso-handoff` (see SSO Handoff section below)

---

## SSO Handoff — "Open in DataMind" Without Re-Login

### Why this exists

`EmbedChat.jsx` has an "Open in DataMind" button that lets embed users leave the
iframe and use the full standalone app (billing, integrations, full analytics).
Their DataMind account was auto-created during onboarding with a **deterministic,
generated password** (see [Password Derivation](#password-derivation) — they have
never seen it and never typed it). Sending them to a plain login screen would
force them to either guess this password or go through "forgot password". Instead,
the widget hands the main app a **short-lived, single-use token** that silently
exchanges for a normal session — the user lands in the main app already signed in,
and the generated password is never displayed, transmitted, or otherwise involved.

### Flow

```
EmbedChat (iframe, already authenticated via dm_embed_token)
  │
  ├─ User clicks "Open in DataMind"
  ├─ window.open('about:blank') — opened synchronously in the click handler
  │    so Safari/iOS popup blockers don't kill it (no `noopener`, since we
  │    need the handle to redirect it once the token resolves — destination
  │    is always our own trusted app, so reverse-tabnabbing isn't a concern)
  │
  ├─ POST /auth/sso-handoff   (authenticated with the existing embed JWT)
  │    → { token: "<one-time JWT, purpose=embed_sso, jti=..., 90s TTL>" }
  │
  ├─ Redirects the opened tab to:
  │    https://app.datamind.ai/?sso=<token>
  │    (falls back to a plain, logged-out link if the handoff call fails)
  │
  └─ notifyParent('dm:open_main_app', { url })   — postMessage, harmless no-op
       if the partner page isn't listening

Main App (App.jsx, on mount)
  │
  ├─ Detects `?sso=<token>` in the URL → shows a loading spinner (`ssoPending`)
  ├─ POST /auth/sso-login  { token }
  │    │
  │    ├─ Verifies JWT signature/expiry (same SECRET_KEY/HS256 as everything else)
  │    ├─ Checks `purpose == "embed_sso"` — rejects normal session tokens
  │    ├─ Checks `jti` hasn't been redeemed before (replay protection)
  │    └─ On success: issues a NORMAL session token via create_token(email)
  │
  ├─ On success: stores dm_token / dm_user, signs the user in
  ├─ On failure (expired/already used/invalid): silently falls through to
  │    the normal login screen — no error shown, just a fresh start
  └─ Strips `?sso=...` from the URL via history.replaceState either way,
       so the link can never be bookmarked, shared, or replayed
```

### Backend pieces (`datamind/backend/auth.py`, `main.py`)

**`create_sso_handoff_token(email)`** — issues a JWT with:

| Claim | Purpose |
|---|---|
| `sub` | the user's email |
| `purpose` | `"embed_sso"` — scopes the token to this one exchange; a regular session token lacks this claim and is rejected by `/auth/sso-login` |
| `jti` | `secrets.token_urlsafe(16)` — unique ID used for one-time-use enforcement |
| `exp` | now + `SSO_HANDOFF_TTL_SECONDS` (90 seconds) |

**`redeem_sso_handoff_token(token)`** — validates and *consumes* the token:

1. Decodes/verifies signature + expiry (raises 401 on `JWTError`)
2. Rejects anything without `purpose == "embed_sso"`
3. Opportunistically prunes `_redeemed_sso_jti` of expired entries (keeps the
   in-memory dict bounded — entries live ≤ 90s so it never grows large)
4. Rejects if `jti` is missing or already present in `_redeemed_sso_jti`
   (replay protection — a leaked/bookmarked URL is a dead link)
5. Records the `jti` as redeemed and returns the email

**`POST /auth/sso-handoff`** *(requires the caller's existing — embed — session)*

```json
→ { "token": "<one-time JWT>" }
```

**`POST /auth/sso-login`** *(public — this IS the login exchange)*

```json
{ "token": "<one-time JWT>" }
→ { "token": "<normal_datamind_jwt>", "user": { "name": "...", "email": "...", "locale": {...} } }
```

Looks the user up with `get_user(email)`, then issues a completely ordinary
session token via the existing `create_token(email)` — from this point on the
session is indistinguishable from a normal password login.

### Frontend pieces

- `embedApi.js` → `embedGetSSOHandoff()` — `POST /auth/sso-handoff` using the embed JWT
- `utils/api.js` → `ssoLogin(token)` — `POST /auth/sso-login`, public (no `dm_token` needed)
- `EmbedChat.jsx` → `openMainApp()` — orchestrates the popup-safe open + handoff + redirect
- `App.jsx` → on-mount effect detects `?sso=`, calls `ssoLogin`, stores the
  session, and strips the param; an `ssoPending` flag renders a spinner while
  the exchange is in flight so the user never sees a flash of the login page

### Security properties (why this isn't "just sharing the password")

- **Scoped**: the `purpose: "embed_sso"` claim means this token can do exactly
  one thing — exchange for a session via `/auth/sso-login`. It cannot be used
  as a Bearer token against any other endpoint.
- **Short-lived**: 90-second expiry. A token sitting in browser history, a
  server access log, or a `Referer` header is worthless almost immediately.
- **Single-use**: the `jti` replay check means even a token captured within
  the 90-second window can be redeemed only once.
- **No new attack surface beyond the embed session itself**: minting a handoff
  token requires an already-authenticated embed session — anyone able to do
  that can already query that user's data through the embed, so this doesn't
  meaningfully extend what a compromised embed session could already do.
- **Zero impact on existing auth**: `/auth/login`, `/auth/register`,
  `create_token`, `decode_token`, `current_user`, and password hashing are
  untouched. Users who don't arrive with `?sso=` (i.e. everyone who isn't
  coming from this specific embed handoff) never touch any of this code —
  `App.jsx`'s `ssoPending` effect short-circuits to a no-op (`if (!token) return`)
  on the very first line.

---

## `aat` Token — How It Gets Into the Widget

The `app_access_token` is Salesplay's session cookie set when a user logs into the backoffice. It lives at:

```
DevTools → Application → Cookies → https://predev5backoffice.nvision.lk → app_access_token
```

Since this is a regular (non-HttpOnly) cookie, Salesplay's JavaScript can read it with `document.cookie`. Their snippet reads it and passes it as a URL parameter to our iframe:

```javascript
var match = document.cookie.match(/(?:^|;\s*)app_access_token=([^;]+)/)
var aat   = match ? decodeURIComponent(match[1]) : ''
// → passed as ?aat=<token> in the iframe src
```

The widget reads it from its own URL params:
```javascript
const aat = aatToken || new URLSearchParams(window.location.search).get('aat') || ''
```

**The `aat` is never stored.** It is used once during onboarding and discarded.

---

## Integration Snippet for Salesplay Team

```html
<!-- DataMind AI Widget -->
<div id="datamind-widget"></div>

<script>
(function () {
  var match = document.cookie.match(/(?:^|;\s*)app_access_token=([^;]+)/)
  var aat   = match ? decodeURIComponent(match[1]) : ''

  if (!aat) {
    console.warn('[DataMind] app_access_token cookie not found. Is the user logged in?')
    return
  }

  var iframe = document.createElement('iframe')
  iframe.src = 'https://datamind.ai/src/embed/embed.html'
            + '?pk=sp_live_XXXXXXXXXXXXXXXXXXXXXXXX'
            + '&aat=' + encodeURIComponent(aat)
  iframe.width       = '420'
  iframe.height      = '680'
  iframe.frameBorder = '0'
  iframe.allow       = 'clipboard-write'
  iframe.style.cssText = 'border-radius:12px; border:none;'

  document.getElementById('datamind-widget').appendChild(iframe)

  // Optional: listen for widget lifecycle events
  window.addEventListener('message', function (e) {
    if (!e.data || !e.data.type) return
    if (e.data.type === 'dm:ready')        console.log('[DataMind] Widget ready')
    if (e.data.type === 'dm:chat_open')    console.log('[DataMind] Chat ready')
    if (e.data.type === 'dm:sync_complete') console.log('[DataMind] Sync done, rows:', e.data.rows)
  })
})()
</script>
```

**Replace `sp_live_XXXXXXXXXXXXXXXXXXXXXXXX`** with the partner key from the `embed_partners` table.

---

## postMessage Events (Widget → Parent Page)

| Event | When fired | Payload |
|---|---|---|
| `dm:ready` | Widget loaded and partner key validated | `{ partner_name }` |
| `dm:onboarding_start` | Consent screen shown | — |
| `dm:onboarding_sync_started` | First sync beginning | — |
| `dm:sync_complete` | Sync finished | `{ rows: number }` |
| `dm:chat_open` | Chat interface ready | — |

The parent page can also send `{ type: "dm:logout" }` to the widget to force a logout.

---

## Testing

### Local testing with `iframe_test.html`

1. Start the dev server: `cd datamind/frontend && npm run dev`
2. Start the backend: `cd datamind/backend && uvicorn main:app --reload`
3. Open `http://localhost:5173/iframe_test.html`
4. Log into `https://predev5backoffice.nvision.lk` in another tab
5. Copy `app_access_token` from DevTools → Application → Cookies
6. Paste into the input box and click **Load Widget**

### Curl test (backend only)
```bash
curl -X POST "http://localhost:8000/embed/salesplay/onboard" \
  -H "Content-Type: application/json" \
  -d '{"partner_key":"sp_dev_test","aat":"YOUR_AAT_TOKEN"}'
```

Expected response:
```json
{
  "token": "<jwt>",
  "user": { "name": "...", "email": "..." },
  "provider_id": "salesplay",
  "is_new_user": true,
  "sync": "started"
}
```

### Adding a dev partner key
The test partner key `sp_dev_test` must exist in the `embed_partners` table. To seed it:
```bash
cd datamind/backend
python scripts/seed_embed_partners.py
```

---

## Production Deployment Checklist

- [ ] Run `seed_embed_partners.py` on production to generate the real `sp_live_` key
- [ ] Add `https://app.salesplay.io` and `https://backoffice.salesplay.io` to `EMBED_ALLOWED_ORIGINS` in `.env`
- [ ] Share the `sp_live_` key and integration snippet with Salesplay team
- [ ] Confirm `app_access_token` cookie is readable by JS (not HttpOnly) in Salesplay's production environment
- [ ] Update `allowed_origins` in `embed_partners` table to Salesplay's production domain(s)
- [ ] Test end-to-end on production with a Salesplay test account

---

## Files Changed in This Feature

| File | Change |
|---|---|
| `datamind/backend/embed.py` | Salesplay onboard endpoint + proxy endpoints |
| `datamind/backend/integrations.py` | Added `skip_validation` param to `connect_integration` |
| `datamind/frontend/src/embed/EmbedApp.jsx` | Route Salesplay to auto-init flow |
| `datamind/frontend/src/embed/EmbedSalesplayAutoInit.jsx` | New Salesplay-specific onboarding component |
| `datamind/frontend/src/embed/embedApi.js` | `salesplayOnboard()` API function |
| `datamind/backend/scripts/seed_embed_partners.py` | Partner config (reference) |
| `iframe_test.html` | Local test page for widget |
