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

**Next:** PLAN 02 — Profile & Subscription Sync.
