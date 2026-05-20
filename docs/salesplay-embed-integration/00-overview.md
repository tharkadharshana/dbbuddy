# Salesplay Embed Integration — Overview

## What We Are Building

Salesplay wants to embed DataMind's "Ask Your Data" chat widget directly inside the Salesplay app as an `<iframe>`. Salesplay users will see the widget, enter their Salesplay API key once, create a DataMind account, sync their data, and ask questions — all without ever leaving Salesplay.

This is a **Partner Embed** system. It is entirely additive. Zero changes to your existing user flows, sync logic, billing, or auth. Existing DataMind users are completely unaffected.

---

## The Iframe Tag Salesplay Will Drop In

```html
<iframe
  src="https://datamind.ai/embed?pk=sp_live_abc123"
  width="420"
  height="680"
  frameborder="0"
  allow="clipboard-write"
  style="border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.15);"
></iframe>
```

That is the only change on Salesplay's side.

---

## Does Every Salesplay User Get a DataMind Account?

**Yes.** Every Salesplay user who goes through the iframe onboarding gets a real, full DataMind account (email + password they set themselves). They can:

- Log in directly at `datamind.ai` any time and access the full product — analytics hub, forecasting, reports, everything
- Manage their own subscription and billing
- Connect additional data sources if they want
- The iframe is an acquisition funnel — some Salesplay users will discover DataMind and want the full experience

---

## Full User Flow (What Happens Step by Step)

```
Salesplay user opens the embedded chat panel in Salesplay
│
├── FIRST VISIT
│   Step 1 → "Enter your Salesplay API key"
│              User types their Salesplay access token
│              DataMind validates it against Salesplay's /merchant API
│
│   Step 2 → "Create your DataMind account"
│              User enters name, email, password
│              DataMind creates account + starts free trial automatically
│
│   Step 3 → "Syncing your data…" (progress bar)
│              Background sync pulls: shops, categories, payment types,
│              products, customers, receipts
│              Iframe polls /providers/{id}/status every 2 seconds
│
│   Step 4 → Chat unlocks
│              User asks questions in plain English
│              Results shown as charts + tables
│
└── RETURN VISIT
    Token is stored in the iframe's own localStorage (dm_embed_token)
    scoped to datamind.ai — auto-authenticated, chat shown directly
```

---

## What Changes and What Does Not

| Area | Changes? | Notes |
|---|---|---|
| `POST /query` | No | Embed uses the exact same endpoint |
| `POST /auth/register` | No | Embed calls the same endpoint |
| `POST /providers/connect` | No | Embed calls the same endpoint |
| Sync logic | No | Already runs in a background thread |
| Billing / trial | No | Same `start_trial()` call |
| Data isolation | No | Table prefix per user, unchanged |
| Existing DataMind users | No | They see nothing different |
| CORS config | Yes | Add Salesplay origin (currently `"*"`) |
| New backend endpoints | Yes | `/embed/context` and `/embed/init` |
| New frontend bundle | Yes | Separate Vite entry for embed UI |
| New DB table | Yes | `embed_partners` |

---

## Architecture Diagram

```
datamind.ai (your servers)
│
├── MAIN APP (existing, unchanged)
│   └── index.html → App.jsx → Sidebar + all pages
│
└── EMBED APP (new, separate bundle)
    └── embed.html → EmbedApp.jsx
          ├── Validates partner_key via GET /embed/context
          ├── State A: New user → EmbedOnboarding.jsx (3-step wizard)
          └── State B: Returning user → EmbedChat.jsx

                           ↕ same API endpoints ↕

    BACKEND (FastAPI — datamind/backend/main.py)
    ├── GET  /embed/context?pk=...    ← NEW
    ├── POST /embed/init              ← NEW
    ├── POST /auth/register           ← unchanged
    ├── POST /auth/login              ← unchanged
    ├── POST /providers/validate      ← unchanged
    ├── POST /providers/connect       ← unchanged
    ├── GET  /providers/{id}/status   ← unchanged
    └── POST /query                   ← unchanged

    DATABASE (DataMind's own MySQL)
    ├── users                         ← unchanged
    ├── user_integrations             ← unchanged
    ├── sync_logs                     ← unchanged
    ├── subscription_plans            ← unchanged
    └── embed_partners                ← NEW
```

---

## Important Things to Know About the Current Codebase

Before you start building, understand these facts about how the code works today:

**1. Sync is already in the background**
In `datamind/backend/main.py` line 1542, `/providers/connect` already calls `background_tasks.add_task(trigger_sync, ...)`. In `datamind/backend/integrations.py` line 481, `_start_sync_thread()` spins up a `threading.Thread`. Sync does NOT block the API response. This is already done correctly.

**2. JWT token is in localStorage**
The frontend stores `dm_token` in `localStorage`. In an iframe context, `localStorage` is scoped to `datamind.ai` — Salesplay's JavaScript on `salesplay.io` **cannot read it**. This is secure and correct. The embed will use a separate key `dm_embed_token` to avoid collisions.

**3. CORS is currently wide open**
`datamind/backend/main.py` line 70: `allow_origins=["*"]`. This works but should be restricted once you register Salesplay as a partner. The embed approach gives you the data to do this properly.

**4. Every DB connection is a new connect()**
`datamind/backend/db.py` line 6 and `datamind/backend/auth.py` line 33 and `datamind/backend/integrations.py` line 43 all call `mysql.connector.connect()` fresh every request. At scale (hundreds of concurrent requests) this will exhaust your DB's `max_connections`. This is addressed in Phase 3.

**5. No react-router — manual page state**
`datamind/frontend/src/App.jsx` uses a simple state variable `[page, setPage]` to switch between pages. The embed app will use the same pattern (no router needed).

---

## Phase Summary

| Phase | What It Does | Estimated Effort |
|---|---|---|
| **Phase 1** | Backend foundation: `embed_partners` table, `/embed/context`, `/embed/init`, CORS update | 1–2 days |
| **Phase 2** | Embed frontend: second Vite entry, onboarding wizard, chat UI | 2–3 days |
| **Phase 3** | Scale hardening: DB connection pooling, rate limiting | 1 day |
| **Phase 4** | Polish: postMessage API, branding config, end-to-end testing | 1 day |

Work in this exact order. Phase 2 depends on Phase 1. Phase 3 is independent and can be done alongside Phase 2.

---

## Files You Will Create

```
datamind/backend/
  embed.py                          ← all embed-specific backend logic

datamind/frontend/src/embed/
  embed.html                        ← second HTML entry point
  EmbedApp.jsx                      ← root component, state machine
  EmbedOnboarding.jsx               ← 3-step wizard for new users
  EmbedChat.jsx                     ← chat UI (adapted from ChatPage.jsx)
  embed.css                         ← minimal reset/variables for iframe
  embedApi.js                       ← axios client pointing to real API URL

docs/salesplay-embed-integration/
  00-overview.md                    ← this file
  01-phase-1-backend.md
  02-phase-2-frontend.md
  03-phase-3-scale.md
  04-phase-4-polish.md
```

---

## Files You Will Modify

```
datamind/backend/main.py            ← import embed router, update CORS
datamind/frontend/vite.config.js    ← add second build entry
```

That is all. Everything else stays the same.
