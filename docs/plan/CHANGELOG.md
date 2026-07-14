# Report-Cache Project — Developer Changelog

> Scoped to this project only (`docs/plan/PLAN_00`–`PLAN_08`): the report-cache-backed, streaming, insight-generating assistant for SalesPlay-embed users. Kept separate from the main `docs/CHANGELOG.md` so this work has its own record without touching that file. For phase-by-phase completion status (checklists), see `docs/plan/PROGRESS.md` — this file is the narrative/why version: what changed, what flow it replaces, and what it impacts.
>
> **Last updated:** 2026-07-14 | **Branch:** `enhance/mcp_server_with_reports`

---

## PLAN 01 — Foundations: Data Model, Report Client, Registry, Normalization

**Status: Complete**

### The problem this solves

Today, when a SalesPlay-embed user asks the chatbot a business question ("what were my sales last week?"), the LLM writes a SQL query against the raw synced tables (`sp_receipts`, `sp_receipt_line_items`, etc.) and runs it. The POS backoffice dashboard, however, computes the same-looking numbers using PHP business logic that's much more involved than a simple `SUM()` — restructuring discounts, addon costs/sales, included vs. excluded tax handling, credit-note vs. cash-refund distinctions, and so on (confirmed by reading the actual report controllers — see `docs/salesplay-internal-api-v2/app/Http/Controllers/App/Reports/`). An ad-hoc SQL query can't easily reproduce that logic, so **the chatbot's number can quietly disagree with the merchant's own dashboard.** There's also no caching — every question recomputes from scratch.

### Previous flow (what happens today, unchanged so far)

```
User asks a business question in embed chat
        │
        ▼
LLM writes SQL directly against sp_* raw tables
        │
        ▼
SQL runs against the shared DB → numbers computed on the fly by the AI
        │
        ▼
LLM writes a narrative answer from that SQL result
```
This is the `/v1/query` path (`natural_language_query` in `main.py`) and it is **completely untouched** by this work — see Impact below.

### Target flow (once all 8 phases are live — not active yet)

```
User asks a business question in embed chat   (SSE stream, PLAN 07)
        │
        ▼
Router: is this a general question or a business/forecast question?
        │
        ├─ general question ─────────────► LLM answers directly (as today)
        │
        └─ business/forecast question
                 │
                 ▼
        Look in the report cache first (new tables, built in PLAN 01)
                 │
        ┌────────┴─────────┐
        │                  │
   already cached     cache miss, or the day is still "open" (today)
        │                  │
        │                  ▼
        │        Call the SAME report API the POS dashboard itself calls
        │        (sales_summary, receipts, refunds, credit_notes, taxes,
        │        charges, sales_by_products, sales_by_category)
        │        → guaranteed to match what the merchant sees in their
        │        own backoffice reports
        │                  │
        │                  ▼
        │        Normalize the response and save it to the cache
        │                  │
        └────────┬─────────┘
                 ▼
        Combine the cached days into the requested date range — knowing
        which numbers are safe to add up, which need recalculating as a
        ratio, and which (e.g. unique-customer counts) must never be
        summed and require a fresh fetch for the exact range asked
                 ▼
        LLM streams back a narrative answer + a data table
```

### What was actually built (Phase 1 of 8 — "Foundations")

Nothing in the target flow above is wired up yet. This phase only built the plumbing everything else depends on:

1. **New database tables** (`scripts/migrations/2026_07_report_cache.sql`) — a place to store daily/monthly business numbers pulled from the POS report APIs, plus per-tenant profile info (shops, cashiers, currency, subscription tier). **Applied** to the local dev DB on 2026-07-14 (see "Post-review fixes" below for how).
2. **A catalog of the 8 report APIs** (`report_cache/registry.py`) — for each one, the exact field names it returns (verified by reading the real Laravel controller source, not guessed) and a tag on every number saying whether it's safe to add across days ("sum"), must be recalculated as a ratio ("ratio", e.g. profit margin %), or can never be summed and must be re-fetched exactly ("non_additive", e.g. a count of unique customers).
3. **A cleanup layer** (`report_cache/normalize.py`) — the POS API returns numbers as formatted text like `"201,852.00"` and dates like `"Apr 05,2026"`; this turns them into real floats and ISO dates DataMind can store and calculate with, without ever crashing on a weird value.
4. **An HTTP client for the report APIs** (`report_cache/client.py`) — knows how to call all 8 endpoints, reusing the same base URL and auth header the existing SalesPlay embed proxy (`embed.py`) already uses for this exact POS API (see Post-review fixes — the first draft got this wrong).
5. **A way to fetch a tenant's stored API token** (`report_cache/auth.py`) — reuses the encryption DataMind already uses for SalesPlay credentials; no new secret-storage mechanism was introduced.

### Impact

- **Right now: none.** Nothing in `main.py` was changed. No existing endpoint, route, or chatbot answer behaves any differently. All new code lives in the new `report_cache/` package and nothing outside of it imports it yet.
- **Not affected, ever, by design:** Loyverse (`ly_*`) users, BYODB users, and External-API users — this project only ever touches SalesPlay-embed tenants (see `PLAN_00` scope guardrails).
- **Once later phases (2–8) wire this in:** SalesPlay-embed users asking business questions will get numbers computed by the POS's own report engine instead of AI-generated SQL — so answers will match the merchant's own dashboard by construction. This ships behind a feature flag (`REPORT_CACHE_ENABLED`, default `false`) with a per-tenant allowlist (`REPORT_CACHE_TEST_EMAILS`) for staged rollout, and every new code path falls back to today's behavior on any error — it should never be possible for this project to turn a working chatbot answer into a 500 error.

### Files added
```
datamind/backend/report_cache/
  __init__.py
  registry.py      — the 8-report catalog + additivity tags
  normalize.py     — number/date cleanup
  client.py        — ReportAPIClient (HTTP calls to the POS report APIs)
  auth.py          — per-tenant token lookup (reuses existing encryption)
  store.py         — placeholder, filled in during Phase 3

datamind/backend/scripts/migrations/2026_07_report_cache.sql   — new tables (6)
datamind/backend/scripts/run_migration.py                       — migration runner

datamind/backend/tests/test_normalize.py     — 16 tests
datamind/backend/tests/test_registry.py      — 6 tests
datamind/backend/tests/test_client.py        — 6 tests
datamind/backend/tests/fixtures/sales_summary_sample.json
```

---

## Post-review fixes (2026-07-14)

Three real bugs were found and fixed after the initial PLAN 01 build, before Phase 2 started.

### 1. Duplicate base-URL setting

`report_cache/client.py`'s first draft introduced a **second**, independent base-URL env var (`REPORT_API_BASE_URL`) for a POS API host that already had one — `SALESPLAY_EMBED_PROXY_BASE`, which `embed.py`'s SalesPlay proxy has used since M4 to call this exact same API (it proxies `/profile` and token-creation calls for the embed onboarding flow).

Digging into *why* it's the same API surface turned up the real problem: `SALESPLAY_EMBED_PROXY_BASE` already resolves to `.../public/app` — confirmed by reading `routes/app.php` (which defines all 8 report routes *and* `/profile`) and finding it's mounted at `Route::prefix('app')` in the POS backend's `RouteServiceProvider.php`. The registry's endpoint paths were written as `/app/sales_summary`, `/app/receipts`, etc. — so reusing the existing base URL correctly would have produced `.../public/app/app/sales_summary` (a duplicated `/app` segment, i.e. a 404).

**Fix:** dropped `REPORT_API_BASE_URL` entirely. `client.py` now reads `SALESPLAY_EMBED_PROXY_BASE` (same default as `embed.py:_SALESPLAY_BASE`) as the single source of truth, and every `Report.endpoint` in `registry.py` had its redundant `/app/` prefix stripped (`/sales_summary`, not `/app/sales_summary`). `fetch_profile()` fixed the same way (`/profile`, not `/app/profile`).

### 2. Wrong auth header

`client.py` was sending `Token: Bearer <token>` — copied from `SalesPlayAPIClient`, which authenticates against a *completely different* SalesPlay API (the v1.0 data-sync API `providers/salesplay/sync.py` talks to). The report routes actually sit in `routes/app.php`'s `Route::middleware(['app.auth'])` group, guarded by the `app_api` JWT guard (`config/auth.php`), which expects the standard `Authorization: Bearer <token>` header — exactly what `embed.py`'s `salesplay_proxy_profile` already sends.

**Fix:** changed the header to `Authorization: Bearer <token>`.

`REPORT_API_HTTP_TIMEOUT` (90s) was kept as a genuinely separate setting from `SALESPLAY_EMBED_PROXY_TIMEOUT` (10s) — that one is tuned for light profile/token calls, not the heavy report endpoints (`set_time_limit(90)` server-side) — this was not a duplicate, just a different concern on the same host.

Regression tests added: `tests/test_registry.py::test_endpoint_does_not_repeat_app_prefix` and a new `tests/test_client.py` (6 tests) asserting the resolved base URL, the `Authorization` header, and that no report/profile URL contains `/app/app/`.

### 3. Migration runner connected to the wrong DB

Running the migration for real surfaced a third bug: `run_migration.py` connected via `db.get_connection()` (no args), which only reads `DB_HOST`/`DB_NAME`/`DB_USER`/`DB_PASSWORD` — the "user default DB" fallback, blank in this repo's `.env` — not the core DB. This produced `Access denied for user 'ODBC'@'localhost'` (mysql-connector's behavior when handed an empty user on Windows), which looked like a credentials problem but wasn't.

The actual core DB (holding `sp_*`, `user_integrations`, and now `report_cache` tables) is reached via `pool.get_internal_conn()` (`pool.py:_build_pool()`), which correctly tries `DATAMIND_DB_*` first, falling back to `DB_*` — the same helper `integrations.py` uses everywhere else for this DB.

**Fix:** `run_migration.py` now uses `pool.get_internal_conn()`.

Ran it against the local dev DB (XAMPP MySQL) — succeeded, all 6 tables created (`report_daily_fact`, `report_dim_fact`, `report_sync_state`, `tenant_profile`, `tenant_shop`, `tenant_cashier`), and a second run confirmed idempotency (`IF NOT EXISTS` guards hold).

**Test suite after all three fixes: 28/28 passing.**

---

## PLAN 02 — Profile & Subscription Sync

**Status: Code-complete, unit-tested (58/58). Live end-to-end pending a real browser onboarding session (see "The live auth-gap discovery" below).**

### What this delivers

`tenant_profile`, `tenant_shop`, `tenant_cashier` populated from the POS's own `/app/profile` — the data every later phase needs for entity resolution (shop name → id), output formatting (currency, number format), authorization (is this shop_id really this tenant's?), and the AI's own data-history window (3/12/~unlimited months, by subscription plan).

### Previous flow (before this phase)

There wasn't one, for this specific data. `tenant_shop`/`tenant_cashier`/`tenant_profile` were empty tables created in PLAN 01 with nothing populating them. Anything that needed a tenant's shop list, currency, or AI plan tier had no single place to read it from report_cache's perspective.

### New flow

```
SalesPlay embed widget opens (onboarding OR returning user)
        │  widget already has a live, short-lived Salesplay session token (the "aat")
        ▼
embed.py:/embed/salesplay/onboard   (existing endpoint, unchanged core logic)
        │
        ├─ existing: fetch /app/profile with aat → create/reuse DataMind account,
        │            connect the SalesPlay integration, start/continue sync
        │
        └─ NEW, additive, feature-flagged (REPORT_CACHE_ENABLED):
                 report_cache.profile.sync_tenant_profile(tenant_id, access_token=aat)
                         │
                         ├─ map_profile(raw) → profile fields / shop list / cashier list
                         │  (pure function — currency, number format, timezone, shops, cashiers)
                         │
                         ├─ report_cache.tiers.get_ai_tier(tenant_id)
                         │  → reads DataMind's OWN billing.py (Starter/Growth/Pro),
                         │    NEVER the POS profile's own subscription field
                         │
                         └─ upsert tenant_profile / tenant_shop / tenant_cashier
```
Later, `report_cache.lookups` gives the answer layer (PLAN 05) simple reads on top of this: `resolve_shop(tenant_id, "Colombo")` → `"1072"`, `is_shop_allowed(tenant_id, shop_id)` as a security guard before any model-suggested shop_id reaches a report call, `currency_symbol(tenant_id)` for display formatting.

### The most important thing this phase got right (and had to fix along the way)

**Two different "subscriptions" that must never be confused.** The POS `/app/profile` response does carry a subscription status — but that's the *merchant's SalesPlay POS plan*, completely unrelated to what DataMind charges for AI usage. The AI's own tier (which controls how many months of history get synced/cached) comes from DataMind's own `billing.py` — the same system that already meters tokens and rows. `report_cache/tiers.py` reads billing.py's real plan names (`"Starter"/"Growth"/"Pro"`, not the made-up "basic/standard/unlimited" names originally sketched in the plan doc) and reuses its existing `get_plan_history_limit()` function verbatim, rather than re-deriving the months/cutoff-date math a second time. That reuse matters: if report_cache computed its own, slightly-different cutoff date than the one `billing.py` already uses everywhere else to limit a user's visible history, the AI could end up showing/caching data the user's plan isn't actually supposed to include — a silent policy violation, not just a display bug.

**The live auth-gap discovery.** Testing this for real against the one live SalesPlay test tenant failed with `404 "User not found"` — not the 401 you'd expect from a bad token. Tracing it down: the token DataMind stores per tenant (used for the *data-sync* API that pulls receipts/products/etc.) is a different, more narrowly-scoped credential than what the *report* API (`/app/profile` and all 8 report endpoints) actually needs. The report API is guarded end-to-end by SalesPlay's own login-session mechanism, and the credential DataMind currently persists was never validated against it — it just happens to also be a JWT, so the guard didn't reject it outright, it just couldn't resolve a real user from it.

The fix: DataMind's existing embed onboarding flow *already* successfully calls `/app/profile` today, using a fresh, short-lived session token the browser widget hands over on each onboarding/widget-open (the "aat"). Rather than inventing a new auth mechanism, this phase hooks `sync_tenant_profile` into that exact same already-working call, reusing the same token. It runs automatically every time a user opens the embed widget (new user or returning), feature-flagged off by default, and never breaks onboarding if it fails — same fallback discipline as everywhere else in this project.

**What's still open:** this only works when a live browser session is present. A true background job (no user watching) — which is exactly what PLAN 03's scheduled report ingestion is specced to be — hits the same wall. That's now an explicit, documented open question for PLAN 03 to resolve before it can call the report endpoints unattended.

### Impact

- **Right now: none**, unless `REPORT_CACHE_ENABLED=true` is set (default `false`). Even then, the new profile sync only ever adds a best-effort, non-fatal step to onboarding — every failure mode falls back to today's onboarding behavior with a logged warning, never a broken signup.
- **Also found and fixed while starting this phase:** `report_cache/auth.py:get_report_token()` had the exact same wrong-DB-connection bug PLAN 01's migration runner had (`db.get_connection()` instead of `pool.get_internal_conn()`) — would have failed the same way the migration did, the first time it was actually called. Fixed before it ever shipped.
- **For PLAN 05 (answer layer) later:** `resolve_shop`/`is_shop_allowed`/`currency_symbol`/`list_cashiers` are ready to use, fully unit-tested. One sharp edge documented for whoever wires cashier filtering: the report APIs' `cashier_id` query parameter is actually matched against a cashier's *name*, not a numeric id — `list_cashiers()` returns the right field for that (`cashier_name`), and it's called out prominently so this isn't rediscovered the hard way later.

**Next:** PLAN 03 — Report Ingestion & Cache Store. Must resolve the background-auth question above before ingestion jobs can call the 8 report endpoints unattended.

---

## PLAN 03 — Report Ingestion & Cache Store

**Status: Complete. 103/103 tests passing (45 new). DB write/read path verified against the real dev database.**

### What this delivers

The actual read/write engine of the cache: given a tenant, a report, and a period, fetch it from the POS, normalize it, and store it correctly — daily rows for the 5 "scalar" reports (sales summary, receipts, refunds, credit notes, taxes, charges), monthly per-product/per-category rows for the 2 "dimensional" reports (sales by product, sales by category). This is the piece PLAN 01–02 built the plumbing for and PLAN 04–05 will schedule and read from. No answering logic yet — after this phase you can populate and query the cache programmatically, that's all.

### Previous flow

None, for this data. `report_daily_fact`/`report_dim_fact`/`report_sync_state` were empty tables since PLAN 01 with no code that wrote to them.

### New flow

```
A job (PLAN 04) or a manual call decides: "ingest sales_summary for tenant T, month April 2026"
        │
        ▼
ingest_period(conn, tenant_id, report_id, token, period)
        │
        ├─ scalar report? (sales_summary, receipts, refunds, credit_notes, taxes, charges)
        │       │
        │       ▼
        │  ingest_scalar_report — one API call for the WHOLE month (the POS
        │  API already returns every day's numbers pre-grouped in one
        │  response — no need to call it 30 times) → one report_daily_fact
        │  row per day, each tagged 'open' (still today) or 'closed' (done,
        │  but not yet "finalized" — that only happens in PLAN 04)
        │
        └─ dimensional report? (sales_by_products, sales_by_category)
                │
                ▼
           ingest_dimensional_report — one API call for the month → one
           report_dim_fact row per product/category, capped to the top N by
           sales size with everything else folded into one "Other" row (a
           merchant with 3,000 SKUs doesn't get 3,000 rows/month/report)
        │
        ▼
   Both paths: never fetch older than what the tenant's AI plan allows
   (reuses PLAN 02's tiers.window_start — same cutoff the rest of the app
   already enforces), record what happened in report_sync_state (including
   failures — a fact table can't record "we tried and it failed", only a
   sync-state row can), and are safe to re-run any time (upsert, not insert)
```
`report_cache/read.py` then answers the question PLAN 05 actually needs: "is this date range fully cached, and is any part of it still the live/mutating today-period?" (`coverage()`) — so the answer layer knows whether it can serve from cache or has to go live.

### The interesting engineering decisions in this phase

**Why `report_sync_state` tracks scalar reports by month, not by day.** `report_daily_fact` already carries its own per-day `status`/`fetched_at` — a second per-day tracking table would just be a duplicate. What `sync_state` is actually for: (a) recording an ingestion attempt as a unit that matches how ingestion is actually requested (one API call = one month, per the POS API's own behavior), and (b) being the *only* place a **failed** fetch can be recorded at all — if the API call fails, zero fact rows get written, so there's nothing in `report_daily_fact` to mark as errored. A month at the edge of a requested range that's only partially covered deliberately does *not* get a completeness row in `sync_state`, even though whatever days it did return are still saved — otherwise a later coverage check could wrongly believe that month is fully cached.

**The top-N cap had to be generic, not hardcoded.** The two dimensional reports use *different field names* for what's conceptually "net sales" — `sales_by_products` calls it `net_sale`, `sales_by_category` calls it `net_sales` (confirmed from the actual POS backend source, not a typo). Ranking "top products by sales" had to work for both without hardcoding either name — solved by reusing the metric-additivity metadata PLAN 01 already tagged in the registry: every dimensional report's profit-margin ratio metric already names its own denominator, which is exactly "the sales-size metric" for that report. Ranking by that, generically, means this keeps working correctly if a ninth dimensional report gets added later with yet another field name. The same additivity tags also decide what's safe to fold into the "everything else" row when a merchant has thousands of products: only the metrics tagged as safely-summable get aggregated — a per-unit price or a profit-margin percentage correctly does *not* appear on the "Other" row at all, rather than being silently (and wrongly) summed or averaged.

**Tests run without a database.** Rather than requiring a live MySQL connection to unit-test 45 new tests, this phase built a small in-memory stand-in for the three cache tables — just enough to handle the handful of fixed query shapes the store/read code actually issues, not a general SQL engine. Everything from that suite was then re-verified for real, once, against the actual dev MySQL database (with only the network call to the POS mocked, since the credential gap from PLAN 02 is still unresolved) — confirming the real upsert syntax, real idempotency, and real value round-tripping all work, not just the in-memory approximation of them.

### Impact

- **Right now: none.** Nothing wired into any request path — this phase is explicitly "plumbing you can call programmatically," same as PLAN 01. `main.py` untouched.
- **For PLAN 04 (jobs):** `ingest_period()` is the exact function a scheduled job calls per (tenant, report, period) — it already handles idempotency, window enforcement, and error recording, so the job layer's job is purely "decide what to ingest and when," not "figure out how to ingest safely."
- **For PLAN 05 (answer layer):** `coverage()` is the exact decision point for cache-hit-vs-go-live the whole point of this cache exists for.
- **Confirmed clean:** none of this phase's new code used the wrong DB-connection helper (the bug found and fixed three times already in PLAN 01–02) — every write goes through `pool.get_internal_conn()` or a connection passed in by the caller.

**Next:** PLAN 04 — Jobs: Onboarding, Backfill, Rollover, Retention. The background-auth gap flagged in PLAN 02 (no confirmed way for an unattended job to call the report API without a live browser session) is still open and now directly blocks this phase's scheduled ingestion.

---

## PLAN 04 — Background Jobs: Onboarding, Backfill, Rollover, Retention, Re-finalization

### What this delivers

Everything that keeps the cache **populated and pruned runs off the request thread now**. PLAN 03 gave us `ingest_period()` — a function you could call by hand. PLAN 04 is the machinery that decides *what* to ingest, *when*, and *how hard to push* the shared POS backend — plus the lifecycle jobs that keep last-month numbers final and storage inside the merchant's subscription window.

### Previous flow (before this phase)

- Ingestion existed only as a library function. Nothing called it automatically. A tenant connecting the AI got their profile synced (PLAN 02) and… that was it — the report cache stayed empty until someone ran a Python snippet.
- No concept of "this month is now closed and final," no pruning of data older than the plan window, no protection if the POS report backend started timing out.

### New flow

```
embed onboarding (first connect)
    └─ enqueue job_onboard_tenant(tenant, aat)   ← fast INSERT, carries the fresh v2.0 token
                                                   (request returns immediately)
report_cache_job  (DB queue)  ◄─── request_backfill() from PLAN 05 on a cache miss
    │
    ▼
worker process  (python -m report_cache.jobs.worker)
    ├─ drains the queue every 5s  → job_onboard_tenant / job_ingest_period / job_sync_profile
    │      each guarded by:  circuit breaker (POS down?)  +  per-tenant rate limiter
    └─ APScheduler cron:
         refinalize   daily  → re-fetch trailing 45d/2mo, mark safely-past facts 'finalized'
         retention    daily  → delete facts older than the tier window (skip 'unlimited')
         rollover     month-start (+ daily guard) → finalize last month, then purge
         profile_sync daily  → refresh shops/currency/tier per active tenant
```

**Queue technology.** PLAN 04 recommends ARQ (Redis). This environment has no Redis and `arq` isn't installed — but **APScheduler already is** — so the plan's own documented fallback was taken: a **DB-backed job table** (`report_cache_job`) is the queue, and APScheduler runs the cron jobs. **No new dependency, no new infrastructure.** The worker is a single process with deliberately small concurrency so the rate limiter and breaker genuinely cap load — these reports are 90-second-class calls.

**Onboarding is eager-but-shallow.** On first connect, the last 3 months (configurable) of all 8 reports are ingested at their native grain, `shop_id='all'`. Anything older is *lazy* — PLAN 05's answer layer calls `request_backfill()` on a cache miss, which enqueues the missing months month-by-month so they warm the cache for next time without blocking the question being asked now.

**Re-finalization + rollover keep numbers honest.** POS data mutates after the fact (late refunds, voids, edits). A period stays `open` while it contains today, becomes `closed` when it doesn't, and only becomes `finalized` after the re-finalization job re-fetches a trailing window and confirms the day is safely in the past (default 2-day lag). Rollover is the same finalize step scoped to the just-closed month at month-start, followed by retention.

**Retention respects the plan.** Facts and sync-state older than the tenant's tier window are deleted daily; `unlimited` (Pro) tenants are never purged. This is pure DB work — no POS calls — so it runs fully unattended.

### The one honest caveat: background auth

The v2.0 report API needs the embed session's short-lived `aat`. The **stored** `api_token` is the v1.0 data-sync token and does **not** authenticate against it (flagged in PLAN 02, still open). PLAN 04 does not fix this — it **threads a token through the job payload**, so:
- Onboarding + lazy backfill (triggered from a live embed request that *has* a fresh `aat`) **work end-to-end today.**
- Unattended API-touching jobs (refinalize/rollover/profile-sync) fall back to the stored token and **fail safe** (error sync-state + breaker, never a crash) until the auth gap is closed.
- Retention (DB-only) is unaffected.

This is deliberately surfaced rather than hidden: the queue/schedule/lifecycle machinery is all built and verified; only unattended *live fetching* waits on the PLAN 02 token resolution.

### Impact

- **Web request path: unchanged and unblocked.** The app only ever *enqueues* (a fast INSERT) or reads the cache — no 90-second report call ever touches a request thread. All of it is behind `REPORT_CACHE_ENABLED` (default OFF) and non-fatal on any error.
- **For PLAN 05:** `request_backfill(tenant, report, start, end)` is the exact cache-miss warm-up hook to call, and `report_cache_state.onboarded_at` tells it whether a tenant's recent window is already warm.
- **Operability:** one new process to run (`python -m report_cache.jobs.worker`); tune load entirely via `REPORT_CACHE_*` env knobs; `storage_metrics()` gives per-tenant row counts for cost monitoring.

### Files added / changed

- **added** `scripts/migrations/2026_07_report_cache_jobs.sql` — `report_cache_job`, `report_cache_state`.
- **added** `report_cache/jobs/{__init__,guards,enqueue,tasks,worker}.py`.
- **added** `tests/test_jobs.py` (9 tests; full suite 112/112).
- **changed** `embed.py` — first-connect enqueues `job_onboard_tenant` with the fresh `aat` (non-fatal, flag-gated).
- **changed** `.env.example` — 11 `REPORT_CACHE_*` job/breaker/rate-limit knobs (safe defaults).

---

## PLAN 05 — Answer Layer: Router, Cache-First Report Tools, Additivity Aggregation

### What this delivers

This is the phase where the **user-visible behavior finally changes** for SalesPlay-embed tenants (behind the flag). The cache built in PLAN 01–04 gets wired into the chat: a lightweight router decides what kind of question it is, business questions are answered from **pre-built, known-correct reports** (cache-first) instead of blind SQL, and the numbers are **additivity-correct** so a quarter's average ticket is never the mean of three monthly averages.

### Previous flow (what happens today)

Every question → one giant `classify_question` prompt → `data_query` → the model writes one blind SQL query over the raw `sp_*` tables (or, with the MCP flag, a tool-loop that still writes SQL) → run it → narrate. Correctness (exclude VOIDs, right revenue column, timezone) rides along as prompt text the model *usually* follows. "The AI's number doesn't match my POS dashboard" is a structural risk because the AI re-derives the number instead of using the report the dashboard uses.

### New flow (SalesPlay tenants, flag on)

```
question
  └─ router (cheap LLM, JSON)  →  general_knowledge → persona answer (NO data tools)
                               →  conversational/clarification → (existing classifier)
                               →  business / forecast / insight
                                     └─ report tool-loop:
                                          list_reports → pick a known-correct report
                                          get_report_metrics(report, dates, shop)
                                              └─ answer_metric_query:
                                                   tier window? → refuse + upsell
                                                   covered+closed+additive? → SUM cached daily facts
                                                   else → live exact-range summary fetch (dashboard number)
                                          (run_select_query still available as fallback)
                                     └─ model narrates the (already-correct) numbers
```

**The model's job shrinks from "write correct SQL" to "pick the right report and fill in dates"** — something LLMs do reliably. Correctness moved out of the prompt and into code: the report's own SQL/summary, the additivity rules, the tenant/mutation guards.

### The three things that make numbers correct

1. **Additivity aggregation (`aggregate.py`).** Base metrics are summed; ratios are recomputed from the summed numerator/denominator (avg_ticket = Σnet ÷ Σcount, gross_margin = Σprofit ÷ Σnet); distinct-counts and per-unit values are **refused** and force a live re-fetch. This is the single rule that prevents "wrong-but-plausible" quarterly/annual numbers (doc 09 C3).
2. **Cache-first, live-on-miss (`answer.py`).** A closed, fully-covered, additive range is answered by summing cached daily facts (fast, no POS call). An open period, a cache miss, or a non-additive metric forces a live exact-range fetch whose **summary block is exactly the dashboard number** (doc 09 C4/C6).
3. **`daily_cacheable` honesty.** Only `sales_summary` has a valid per-day additive breakdown today; every other scalar report answers from a live exact-range summary rather than trusting a per-day breakdown that isn't valid for it. Correct by construction, at the cost of some cache-hit rate — flip the flag per report when PLAN 03 grows per-report daily ingestion.

### Prompt hygiene (doc 07 Part 3)

The one giant prompt is split: a tiny **router** (type only, no rules), a short **persona** (who the assistant is + currency + profile, soft scope nudge, *no* correctness rules), and **correctness-as-code** (report tools + the existing AST/tenant safety). General-knowledge and insight questions can answer from the model's own reasoning with no data tools.

### Impact

- **Flag OFF (default): nothing changes.** The branch is skipped entirely; the legacy classify/SQL pipeline is byte-for-byte unchanged. Loyverse/BYODB/own-DB users are never affected (SalesPlay-tenant-only branch).
- **Flag ON:** business questions answer from known-correct reports, matching the POS dashboard for covered ranges; general-knowledge answers without touching data; out-of-window questions get a tier-upsell refusal instead of a wrong or empty answer.
- **Honest limitation (token wrinkle, PLAN 02/04):** live fetches need the v2.0 report token the chat path doesn't have yet — so today cache-hit historical `sales_summary` questions work fully, and live/uncovered questions **fall back to the existing SQL pipeline** rather than failing. General-knowledge + refusals work regardless.
- **For PLAN 06/07:** forecast/insight already route to the report loop (their dedicated tools slot in next); the loop returns a ready-to-stream narrative + data table for the SSE endpoint.

### Files added / changed

- **added** `report_cache/aggregate.py`, `report_cache/answer.py`, `report_cache/router.py`, `report_cache/prompts.py`, `mcp_server/report_tools.py`.
- **added** `tests/test_aggregate.py` (7), `tests/test_answer.py` (12) — full suite 124/124.
- **changed** `report_cache/registry.py` — `daily_cacheable` flag (True only for `sales_summary`).
- **changed** `main.py` — `_report_cache_enabled_for` flag + `_try_report_cache_answer` branch before the legacy path (fully fenced by the flag + fallback rule).
