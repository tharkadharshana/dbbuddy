# Progress Log

## PLAN 01 — Foundations: Data Model, Report Client, Registry, Normalization

- [x] Step 1 — `report_cache/` package created (`__init__.py`, `registry.py`, `normalize.py`, `client.py`, `store.py` stub, `auth.py`).
- [x] Step 2 — Migration `scripts/migrations/2026_07_report_cache.sql` (6 tables) + runner `scripts/run_migration.py`. **Applied** to the local dev DB on 2026-07-14 — all 6 tables confirmed present (`report_daily_fact`, `report_dim_fact`, `report_sync_state`, `tenant_profile`, `tenant_shop`, `tenant_cashier`); re-run confirmed idempotent. See "Post-review fix" below — the runner originally used `db.get_connection()`, which doesn't reach the core DB in this repo's `.env`.
- [x] Step 3 — `registry.py`: all 8 reports (`sales_summary`, `receipts`, `refunds`, `credit_notes`, `taxes`, `charges`, `sales_by_products`, `sales_by_category`) with metric additivity tags. Metric keys verified against the actual Laravel controllers in `docs/salesplay-internal-api-v2/app/Http/Controllers/App/Reports/StandardReports/*.php` — not guessed. Found and handled a real API inconsistency: `sales_summary`'s `table_data` rows use `tips_amount`/`surcharge_amount` but its `summary` block uses `tips`/`surcharge` for the same figures — added `Metric.aliases` to resolve it.
- [x] Step 4 — `normalize.py` (`parse_number`, `parse_api_date`, `normalize_summary`, `normalize_daily_rows`, `normalize_dim_rows`) + `tests/test_normalize.py` (16 tests) + `tests/fixtures/sales_summary_sample.json`. All green.
- [x] Step 5 — `client.py` (`ReportAPIClient`): `fetch_report`, `fetch_report_all_pages` (page-capped), `fetch_profile`. Mirrors `SalesPlayAPIClient`'s Token/Bearer auth + retry/backoff/429/401 handling. Library only — not wired into any web-request path.
- [x] Step 6 — `fastmcp==3.4.3` already present in `requirements.txt` (added by a prior commit, verified not missing). Added `REPORT_API_BASE_URL`, `REPORT_CACHE_ENABLED`, `REPORT_CACHE_TEST_EMAILS`, `REPORT_API_HTTP_TIMEOUT`, `REPORT_API_RETRY_ATTEMPTS`, `REPORT_API_VERIFY_SSL`, `REPORT_API_MAX_PAGE_CAP` to `.env.example`. `report_cache/auth.py:get_report_token(tenant_id)` reuses `integrations.py`'s existing Fernet encryption (queries `user_integrations` by `table_prefix` instead of `user_email`, since jobs only have `tenant_id`).
- [x] `tests/test_registry.py` (5 tests): all 8 reports present, every `agg` valid, every `ratio` metric's `num`/`den` resolve to declared metrics, dimensional reports have a `dim_type`, every report has an endpoint + at least one metric.

**Acceptance status:** code + unit tests pass (28/28). Registry imports cleanly (`8 reports`). `ReportAPIClient` instantiates and builds correct URLs (verified without a live token). Migration SQL applied and confirmed idempotent. Nothing wired into `main.py` (correct — out of scope for PLAN 01).

### Post-review fix (2026-07-14)

User flagged that `report_cache/client.py` had introduced a second, independent
base-URL env var (`REPORT_API_BASE_URL`) duplicating one that already existed
(`SALESPLAY_EMBED_PROXY_BASE`, used by `embed.py`'s SalesPlay proxy for the
exact same POS API host). Investigating turned up two real bugs, both fixed:

1. **Duplicate base URL.** `SALESPLAY_EMBED_PROXY_BASE` already resolves to
   `.../public/app` (confirmed: `routes/app.php` — which defines all 8 report
   routes AND `/profile` — is mounted at `Route::prefix('app')` in
   `RouteServiceProvider.php`). `registry.py`'s `Report.endpoint` values were
   `/app/sales_summary` etc., which — if `client.py` had reused
   `SALESPLAY_EMBED_PROXY_BASE` as it now correctly does — would have built a
   broken `.../public/app/app/sales_summary` URL (duplicate `/app` segment).
   **Fix:** dropped `REPORT_API_BASE_URL` entirely; `client.py` now reads
   `SALESPLAY_EMBED_PROXY_BASE` (same default as `embed.py:_SALESPLAY_BASE`)
   as the single source of truth, and every `Report.endpoint` in `registry.py`
   had its redundant `/app/` prefix stripped (`/sales_summary`, not
   `/app/sales_summary`). `fetch_profile()` fixed the same way (`/profile`).
2. **Wrong auth header.** `client.py` was sending `Token: Bearer <token>` —
   copied from `SalesPlayAPIClient`, which authenticates against a completely
   different API (the v1.0 data-sync API). The report routes actually sit in
   `routes/app.php`'s `Route::middleware(['app.auth'])` group, guarded by the
   `app_api` JWT guard (`config/auth.php`), which expects the standard
   `Authorization: Bearer <token>` header — the same header `embed.py`'s
   `salesplay_proxy_profile` already sends. **Fix:** changed the header to
   `Authorization: Bearer <token>`.

Also kept `REPORT_API_HTTP_TIMEOUT` (90s) as a genuinely separate setting from
`SALESPLAY_EMBED_PROXY_TIMEOUT` (10s) — that one is tuned for light
profile/token calls, not the heavy report endpoints (`set_time_limit(90)`
server-side) — this was not a duplicate.

Added regression tests: `tests/test_registry.py::test_endpoint_does_not_repeat_app_prefix`
and a new `tests/test_client.py` (6 tests) asserting the resolved base URL,
the `Authorization` header, and that no report/profile URL contains
`/app/app/`. Full suite: **28/28 passing.**

### Migration run + runner fix (2026-07-14)

Attempting the live migration surfaced a third bug: `run_migration.py` connected
via `db.get_connection()` (no args), which only reads `DB_HOST`/`DB_NAME`/
`DB_USER`/`DB_PASSWORD` — the "user default DB" fallback, blank in this repo's
`.env` — not the core DB. This produced `Access denied for user 'ODBC'@'localhost'`
(mysql-connector's behavior when handed an empty user on Windows), not a
credentials problem. The actual core DB (holding `sp_*`, `user_integrations`,
and now `report_cache` tables) is reached via `pool.get_internal_conn()`
(`pool.py:_build_pool()`), which correctly tries `DATAMIND_DB_*` first, falling
back to `DB_*` — the same helper `integrations.py` uses everywhere else for
this DB. **Fix:** `run_migration.py` now uses `pool.get_internal_conn()`.

Ran it against the local dev DB (XAMPP MySQL) — succeeded, all 6 tables
created, and a second run confirmed idempotency (`IF NOT EXISTS` guards hold).

---

## PLAN 02 — Profile & Subscription Sync

- [x] Found + fixed a **fourth** carry-over bug while starting this phase: `report_cache/auth.py:get_report_token()` had the exact same `db.get_connection()` core-DB bug as `run_migration.py` (fixed above) — `user_integrations` lives in the core DB, only reachable via `pool.get_internal_conn()`. Fixed the same way. Also added `get_tenant_user_email(tenant_id)` to `auth.py` — needed to bridge `tenant_id` (table_prefix) to `user_email`, since `billing.py`'s plan functions are keyed by email.
- [x] Step "AI tier resolution" — `report_cache/tiers.py`. **Deviated from PLAN_02's literal spec** (`TIER_HISTORY_MONTHS = {"basic": 3, "standard": 12, "unlimited": None}`, hand-rolled `window_start()`) in favor of reusing `billing.py`'s existing `get_plan_history_limit()`/`get_user_subscription()` — confirmed real plan names are `"Starter"/"Growth"/"Pro"` (not literally "basic/standard/unlimited" — that ENUM is a display label only), and billing.py's own `_PLAN_HISTORY` already computes `{months, row_limit, cutoff_date}` with `Pro: 200 months` as its own deliberate non-`None` stand-in for "unlimited" (its comment: "there's no unlimited sentinel"). Reusing this exact function means report_cache's history window can never silently disagree with the row-limit the rest of the app already enforces for the same user — the same class of "two systems computing a different answer" bug fixed in PLAN 01's post-review. `PLAN_NAME_TO_TIER = {"Starter":"basic","Growth":"standard","Pro":"unlimited"}` is the label mapping.
- [x] Step 1 — `report_cache/profile.py`: `map_profile(raw)` (pure), `sync_tenant_profile(tenant_id, access_token=None)`, `ensure_profile_fresh(tenant_id, max_age_hours=24)`. Response shape verified against the actual `ProfileController@profile` + `ProfileDataRepository` source, not guessed — found two non-obvious facts worth flagging:
  - `currency`/`ui_language`/`number_format`/`timezone` live under `raw["user"]`, not top-level (`shop_list`/`cashier_list` ARE top-level).
  - **`cashier_id` in every report API filter is actually matched against a cashier's display *name*** (`invoice_cashier_name` column), not a numeric id — confirmed in `SalesSummaryController@getMainSalesData`. `tenant_cashier.cashier_id` stores the stable `user_id` (safe DB key); `tenant_cashier.cashier_name` holds the value that must actually be sent as the report APIs' `cashier_id` query param. Documented prominently in `profile.py`'s module docstring and `lookups.py:list_cashiers()` so PLAN 05 doesn't get this wrong.
  - No ISO currency code is available from this endpoint — `user.currency` and `user.number_format.profile_currency` are the same underlying display-symbol value (e.g. "Rs.", "$"), consistent with how `llm.py:fix_currency_symbol` already treats "currency" elsewhere in this codebase. `tenant_profile.currency` and `.currency_symbol` are populated with the same value.
- [x] Step 2 — `report_cache/lookups.py`: `get_profile`, `list_shops`, `resolve_shop` (exact id → exact name → unique substring → `None` if ambiguous/no match), `is_shop_allowed` (`"all"`/blank always allowed — it's the report APIs' own sentinel, not a specific shop), `list_cashiers`, `currency_symbol` (defaults to `"$"`, the same "no correction" sentinel `fix_currency_symbol` uses, if no profile synced yet).
- [x] Step 4 — Tests: `tests/test_profile.py` (8 tests, `tests/fixtures/profile_sample.json`), `tests/test_tiers.py` (9 tests, billing.py mocked via monkeypatch), `tests/test_lookups.py` (13 tests). **Full suite: 58/58 passing.**

### Live auth-gap discovery + fix (2026-07-14)

Manual verification (`sync_tenant_profile()` against the one real SalesPlay test tenant, `dm_0cf49b994a96f68e_salesplay`) failed with `404 {"message":"User not found"}` — not a 401. Diagnosed properly rather than guessing:

- `RouteServiceProvider.php:50-53` wraps the **entire** `routes/app.php` file (all 8 report routes *and* `/profile`) in the `app_api` guard at route-group registration — confirmed via a live unauthenticated call to `/app/status` (no per-route middleware) returning `401`.
- The stored token (`user_integrations.credentials_enc.api_token`) decodes as a real JWT and was **accepted** by the guard (no 401) but resolved to no matching user (`AppAuth::user()` came back empty → ProfileController's own `404 "User not found"` fallback fired). Tried both `Authorization: Bearer` and `Token: Bearer` — identical result, ruling out a header-convention issue.
- Traced where that token comes from: `embed.py`'s onboarding flow mints it via `POST {SALESPLAY_EMBED_PROXY_BASE}/integrations/access_tokens`, authenticated by a short-lived `aat` (app_access_token) that only exists during a live SalesPlay browser session. That minted token is what `providers/salesplay/sync.py` uses for the **v1.0 data-sync API** (different host, different auth family) — it was never validated against the v2.0 `/app/*` JWT-guarded surface PLAN 02 needs, and empirically does not work there.

**User's direction:** the onboarding flow already successfully fetches `/app/profile` today — using the live `aat`, not the stored token — so wire `report_cache` into that existing, already-working path instead of inventing new auth.

**Fix implemented:**
- `embed.py` — added `_REPORT_CACHE_ENABLED`/`_REPORT_CACHE_TEST_EMAILS` flags (mirrors `main.py`'s `MCP_TOOL_CALLING_ENABLED`/`_TEST_EMAILS` staged-rollout pattern exactly) and `_sync_report_cache_profile(table_prefix, email, aat)` — a best-effort, try/except-wrapped, non-fatal call to `sync_tenant_profile(table_prefix, access_token=aat)` using the **same already-validated `aat`** the onboarding request fetched its own profile copy with in step 1. Wired into both onboarding branches in `salesplay_onboard()`: the fresh-token path (right after `connect_integration()`, using its returned `table_prefix`) and the returning-user delta-sync path (looks up `table_prefix` via `get_integration()`). Default OFF (`REPORT_CACHE_ENABLED=false`); on any failure it logs a warning and the existing onboarding flow is completely unaffected — verified by syntax-checking and importing `embed.py` after the change (flags correctly default to `False`/`set()`).
- `report_cache/profile.py:sync_tenant_profile`'s `access_token` parameter (already in PLAN_02's original spec — `token = access_token or get_report_token(tenant_id)`) is now the **primary** intended calling convention; `get_report_token(tenant_id)` remains as a structural fallback but is now a documented known limitation (see below), not a working background path.

**Known limitation carried forward to PLAN 03:** the `aat` is short-lived and only exists during a live, logged-in browser session inside the SalesPlay iframe. There is currently no confirmed mechanism for a true background/scheduled job (no live user session) to authenticate against `/app/*` — which is exactly the credential path PLAN 03's report-ingestion jobs are specced to use for all 8 report endpoints (same `app_api` guard, confirmed double-wrapped via both the route-group middleware and an explicit `Route::middleware(['app.auth'])` block). Options for PLAN 03 to evaluate (not decided here — out of scope for PLAN 02): (a) drive ingestion only from live widget-open events reusing a fresh `aat`, same pattern as this fix and the existing delta-sync trigger; (b) investigate whether `POST /app/autologin` (accepts `{"token": "..."}`, returns a fresh `{access_token, refresh_token}` pair) can exchange the stored long-lived token for a working session — untested, not attempted live to avoid unintended side effects without product sign-off; (c) ask SalesPlay for a proper service-account/integration-token flow for this API surface.

No fully-live end-to-end test of `sync_tenant_profile` was possible in this session (would require a real `aat` from an actual browser onboarding session, which can't be produced standalone). Everything unit-testable — `map_profile`, `tiers.py`, `lookups.py` — is tested against realistic fixtures derived from the real controller/repository source. The DB write path (`_upsert_profile_row`/`_replace_shops`/`_replace_cashiers`) follows the exact same upsert/delete-then-insert patterns already proven live in PLAN 01's migration work, but has not itself been exercised against a real 200 response yet.

**Acceptance status:** code + unit tests pass (58/58). `map_profile` correctly excludes any tier/subscription data from the POS payload (explicit regression test). `resolve_shop`/`is_shop_allowed` unit-tested for exact/fuzzy/ambiguous cases. AI tier resolution reuses `billing.py` verbatim rather than re-deriving it. Live profile-fetch path is wired into the existing, working onboarding flow, feature-flagged, and non-fatal on failure — full live verification pending a real onboarding session with a fresh `aat`.

**Next:** PLAN 03 — Report Ingestion & Cache Store. Must address the background-auth question above before ingestion jobs can call the report endpoints for real.

---

## PLAN 03 — Report Ingestion & Cache Store

- [x] `report_cache/client.py` — added `REPORT_API_PAGE_SLEEP` backpressure (0.5s default) between paginated report calls (doc 09 Part 7), mirroring `SALESPLAY_RATE_SLEEP`'s existing convention. Not called before the first page, only between subsequent ones.
- [x] `report_cache/periods.py` — `month_bounds`, `is_open_period`, `status_for`, `daterange_to_months`, plus `daterange_days` (not in the original spec list, added because `read.py:coverage()` needed exactly this and it's cheap/pure to test in isolation rather than duplicate inline). `status_for` never returns `"finalized"` — that's PLAN 04's re-finalization job only.
- [x] `report_cache/store.py` — `upsert_daily_fact`, `upsert_dim_fact`, `set_sync_state`, all `INSERT ... ON DUPLICATE KEY UPDATE`. **Transaction ownership clarified in code**: none of these (or `ingest.py`'s functions) call `conn.commit()`/`close()` — the caller owns that, matching PLAN_03's own manual-verification snippet (`ingest_period(conn, ...); conn.commit()` is external to the call).
- [x] `report_cache/ingest.py` — `ingest_scalar_report`, `ingest_dimensional_report`, `ingest_period` dispatcher. Notable implementation decisions beyond the literal spec:
  - **Window enforcement** (`tiers.window_start`) happens before any HTTP call; a range straddling the window boundary is clipped forward (`effective_start = max(start, win_start)`) rather than rejected outright.
  - **`report_sync_state` for scalar reports is tracked per fully-covered calendar month**, not per day — `report_daily_fact` itself already carries per-day `status`/`fetched_at`, so a second per-day tracking row in `sync_state` would just duplicate that. `sync_state` earns its keep by recording months as a unit (matches how `ingest_period` requests one month at a time) and by being the only place a **failed fetch** can be recorded at all (a failed API call produces zero fact rows — there's nothing in `report_daily_fact` to mark as errored). A boundary month not fully covered by `[start,end]` intentionally does **not** get a `sync_state` row, even though its partial days are still written as facts — tested explicitly (`test_ingest_scalar_report_partial_month_range_skips_sync_state`).
  - **Top-N cap for dimensional reports** (doc 09 C7): ranks dimension rows by the report's own `ratio` metric's `den` (e.g. `net_sale` for `sales_by_products`, `net_sales` for `sales_by_category` — these are genuinely different field names between the two reports, confirmed in `registry.py`, so a hardcoded metric name would have been wrong for one of them). Overflow beyond `top_n` collapses into one `__other__` row, summing only `agg="sum"` metrics — `agg="ratio"`/`"non_additive"` metrics (e.g. `product_cost`, a per-unit price) are correctly excluded from the aggregate rather than silently averaged/summed wrong.
  - **Shop authorization guard** (`is_shop_allowed`) added at the top of both ingest functions, defense-in-depth per PLAN_00 §0.6, even though the real trust boundary is PLAN 05's answer layer.
  - **Error handling**: the HTTP fetch happens before any DB write, so a fetch failure never leaves partial fact rows. On failure, a best-effort `sync_state` row (`status='error'`, `last_error=...`) is written (swallowing its own failure so it can't mask the original exception), then the exception is **re-raised** — ingestion is a background-job concern (PLAN 04), so letting the job's own retry/backoff catch it is correct; this is not a user-facing request path subject to PLAN_00's "never surface a 500" rule.
- [x] `report_cache/read.py` — `get_daily_facts`, `get_dim_facts`, `coverage`. `coverage()` is scalar-report-only (raises for dimensional report_ids, since "missing days" isn't a meaningful concept at monthly dim-fact grain); `has_open` is true both when a present row's `status='open'` AND when today falls in the requested range even before that day has been ingested at all.
- [x] Tests: `tests/test_periods.py` (18 tests), `tests/test_ingest.py` (16 tests), `tests/test_read.py` (11 tests) — **45 new tests, all against `tests/fakedb.py`**, a small purpose-built in-memory fake of the three report_cache tables (routes by table name in the SQL string; not a general SQL engine — these three tables only ever see a small, fixed set of query shapes from `store.py`/`read.py`). Chosen over requiring a live MySQL connection for unit tests: fast, hermetic, no DB credentials needed to run `pytest` in CI. **Full suite: 103/103 passing.**

### Live DB verification (2026-07-14)

Same credential gap as PLAN 02 (no working SalesPlay report-API token available in this session) meant a genuinely live HTTP fetch couldn't be exercised — but unlike the token itself, the **database round-trip** was fully verifiable by mocking only `ReportAPIClient.fetch_report_all_pages` and running the real `ingest_scalar_report`/`ingest_dimensional_report`/`coverage`/`get_daily_facts`/`get_dim_facts` against the actual dev MySQL via `pool.get_internal_conn()` (a synthetic `test_plan03_verification` tenant, cleaned up after). Confirmed against real MySQL, not just the fake:
- 3 daily fact rows written with correct parsed values (`gross_sales: 201852.0`, etc.), correct `status`.
- Re-running the same ingest is idempotent (still 3 rows, not 6) and `report_sync_state.attempts` correctly incremented 1 → 2.
- `coverage()` correctly reports `covered=True` for the exact ingested range.
- Dimensional top-N cap (`top_n=2` against 5 synthetic products) correctly kept the top 2 by `net_sale` and collapsed the remaining 3 into one `__other__` row — with `product_cost`/`product_price`/`profit_margin` (non-additive/ratio metrics) correctly **absent** from the aggregated row rather than wrongly summed.

Also confirmed `db.get_connection()` was **not** used anywhere in this phase's new code — every DB touch goes through `pool.get_internal_conn()` (or is passed in externally), avoiding a fifth instance of the PLAN 01/02 core-DB-connection bug.

**Acceptance status:** all four acceptance criteria met — scalar ingest populates `report_daily_fact` (daily) + `report_sync_state`; dimensional ingest populates `report_dim_fact` (monthly); `coverage()` correctly reports hit/miss/open; all writes idempotent and window-enforced. Verified against both the in-memory fake (103 unit tests total, 45 new this phase) and the real dev database (manual verification above).

**Next:** PLAN 04 — Jobs: Onboarding, Backfill, Rollover, Retention. Still needs to resolve the background-auth question flagged in PLAN 02 before scheduled ingestion jobs can call the report API unattended.

---

## PLAN 04 — Background Jobs: Onboarding, Backfill, Rollover, Retention, Re-finalization

**Queue tech decision (documented per Preconditions):** no Redis / `arq` in this environment, but **APScheduler is already a dependency** — so PLAN 04's documented fallback was taken: a **DB-backed job table** (`report_cache_job`) drained by a worker + **APScheduler** for the cron lifecycle jobs. Zero new dependencies added.

- [x] `scripts/migrations/2026_07_report_cache_jobs.sql` — `report_cache_job` (durable queue) + `report_cache_state` (per-tenant `onboarded_at` marker, kept out of `tenant_profile.profile_json` because that column is overwritten on every profile re-sync). Applied to dev DB and column-verified.
- [x] `report_cache/jobs/guards.py` — in-process `CircuitBreaker` (per-tenant + a higher-threshold global key so one tenant can't halt everyone) and per-tenant `RateLimiter` (min-interval token bucket, reserves its slot then sleeps *outside* the lock so tenants aren't serialised). `ponytail:` noted — process-local state, correct for the single-worker fallback; upgrade path to Redis if horizontally scaled.
- [x] `report_cache/jobs/enqueue.py` — the DB queue: `enqueue`, `request_backfill` (month-by-month, window-clipped — PLAN 05 calls this), plus worker-side `claim_next` (optimistic `WHERE status='queued'` claim), `complete`, `fail` (requeue-with-linear-backoff up to `JOB_MAX_ATTEMPTS`, else `error`), `pending_count`.
- [x] `report_cache/jobs/tasks.py` — the seven task functions (sync, plain functions, no `ctx` — the APScheduler fallback, not arq): `job_sync_profile`, `job_onboard_tenant` (eager last-N-months × 8 reports, **inline** so one short-lived aat is used within a single job rather than fanned across queued rows), `job_ingest_period`, `job_rollover`, `job_refinalize` (re-fetch trailing 45d daily / 2mo dim then mark safely-past facts `finalized`), `job_retention_purge` (delete out-of-window, **skip `unlimited`**, pure DB), `job_sync_profile_all`. Plus `_finalize_past`, `_purge_tenant`, `storage_metrics`, and tenant/marker helpers.
- [x] `report_cache/jobs/worker.py` — `run_worker()` entrypoint (`python -m report_cache.jobs.worker`): registers the queue drain (interval) + refinalize/retention/profile-sync (daily) + rollover (monthly + daily guard) on a `BlockingScheduler`; `TASKS` registry maps queueable task names; `run_job`/`drain_once` never propagate so the loop survives a bad job.
- [x] Onboarding trigger wired into `embed.py:_sync_report_cache_profile` — first connect for a tenant enqueues `job_onboard_tenant` carrying **this request's fresh v2.0 `aat`** (see below), behind `REPORT_CACHE_ENABLED`, idempotent (`is_onboarded` gate), non-fatal.
- [x] `.env.example` — 11 new `REPORT_CACHE_*` job/breaker/rate-limit knobs, all with safe defaults (no `.env` change needed to run).
- [x] Tests: `tests/test_jobs.py` (9 tests) — onboarding ingests exactly the recent-N-month set; `_purge_tenant` deletes out-of-window & keeps in-window (real filtering fake); retention skips `unlimited`; `request_backfill` enqueues month-by-month; breaker opens after N failures / resets on success / is per-tenant. **Full suite: 112/112 passing.**

### The background-auth wrinkle (PLAN 02's open item) — how PLAN 04 handles it

The v2.0 `/app/*` report API needs the embed session's short-lived `aat`; the **stored** `api_token` is v1.0 and does **not** authenticate against it. PLAN 04 does not try to fix this (out of scope) — instead it **threads a token through job payloads**:
- Jobs triggered from a live embed request (onboarding, lazy backfill) carry the fresh `aat` → **work end-to-end today**.
- Unattended API-touching jobs (refinalize/rollover/profile-sync) fall back to `get_report_token()` and **fail safe** (error sync-state + breaker, never a crash) until the auth item is resolved.
- **Retention purge is pure DB** → works fully unattended regardless.

### Live verification (2026-07-14)

- Migration applied; both tables column-verified against dev MySQL.
- Full queue round-trip against real DB: `enqueue` → `claim_next` (attempts→1, status→`running`) → `drain_once` dispatched the payload → `complete` (status→`done`). Cleaned up after.
- APScheduler wiring loads and registers all 6 jobs with the expected triggers (drain 5s; refinalize/retention/profile-sync daily; rollover monthly + daily guard).

**Acceptance status:** worker drains the queue and dispatches tasks; scheduled jobs registered; retention respects tier (skips `unlimited`) and re-finalize marks trailing-window facts `finalized`; **no ingestion runs on a web-request thread** (the web app only enqueues); rate-limiter + breaker cap POS load. The only criterion not exercised against a *live* POS fetch is onboarding's actual HTTP ingest — blocked solely by the still-open PLAN 02 background-auth token gap, not by anything in this phase (the DB/queue/schedule/lifecycle machinery is all verified).

**Next:** PLAN 05 — Answer layer, cache-first MCP tools, additivity aggregation, router/persona. `request_backfill()` is the exact cache-miss warm-up hook PLAN 05 calls.

---

## PLAN 05 — Answer Layer: Router, Cache-First MCP Tools, Additivity Aggregation

- [x] `report_cache/aggregate.py` — the additivity core (doc 09 C3). `aggregate_scalar` sums `sum` metrics then derives `ratio` metrics from the summed num/den (avg/margin computed as Σnum÷Σden, **never** the mean of daily ratios); `non_additive` metrics are refused (listed in `non_additive_skipped`), never fabricated. `aggregate_dim` combines dim members across months, ranks by the report's sales-size metric, caps top-N. `needs_live_fetch` = non-daily-cacheable report OR non-additive metric OR missing days OR open period.
- [x] `report_cache/registry.py` — added `daily_cacheable: bool` (default False; **True only for `sales_summary`**). Encodes the real constraint that only `sales_summary`'s `table_data` is GROUP-BY-DATE — the other scalar reports' daily facts aren't a valid per-day breakdown (`normalize_daily_rows`), so they're always answered by a live exact-range summary fetch (still dashboard-correct). Flip the flag per report once PLAN 03 does per-report daily summary ingestion.
- [x] `report_cache/answer.py` — read-through resolver `answer_metric_query`. Order: (1) tier-window refusal + upsell (doc 09 Part 5); (2) scalar → coverage + `needs_live_fetch` → cache fast-path (sum daily facts) or live exact-range **summary** fetch; dimensional → per-month cache check (all present & closed) → `aggregate_dim` or live range fetch. Returns `{columns, data, summary, provenance, source}` or `{refusal}`. Live path best-effort enqueues `request_backfill` to warm the cache.
- [x] `mcp_server/report_tools.py` — `ReportToolContext` (wraps the generic `ToolContext` for the SQL fallback + adds tenant/token/tier/currency/shops, identity server-side). `build_report_mcp` = generic tools **+** `list_reports` (keyword-ranked registry), `get_report_metrics` (primary analytics tool → `answer_metric_query`, shop name→id resolved & authorized), `get_report_detail` (live bounded row detail). `answer_report_question` runs the tool loop and keeps the model's **own** final narrative (report numbers are already correct — no Think-Mode regeneration) + the last tool's data table; raises `NoReportAnswer` → caller falls back.
- [x] `report_cache/router.py` — `route()` → `business_data | forecast | insight | general_knowledge | conversational | clarification` via the existing call_llm JSON idiom. Never raises; defaults to `business_data`.
- [x] `report_cache/prompts.py` — short persona (doc 07 Part 3.2): lets users ask business/forecast/general freely, soft scope nudge, injects profile + currency, **no** correctness rules. `persona_answer()` answers general-knowledge with no data tools.
- [x] `main.py` — flag reader `_report_cache_enabled_for` (SalesPlay + staged rollout, mirrors embed.py) + `_try_report_cache_answer` helper, wired as a branch **before** the legacy classify/SQL path. general_knowledge → persona (no tools); business/forecast/insight → report loop; conversational/clarification → `None` (falls through to the existing classifier, which handles those best). Any failure → warning + fall back. `forecast`/`insight` currently route through the same report loop (PLAN 06 adds their dedicated tools).
- [x] Tests: `tests/test_aggregate.py` (7) + `tests/test_answer.py` (12) — additivity (Σnum÷Σden not mean-of-ratios), metric filter, non-additive skip, zero-den omission, `needs_live_fetch` matrix, dim combine/rank/cap; tier refusal (no fetch), cache fast-path, non-additive→live, open→live, non-cacheable→live, shop resolve/authorize. **Full suite: 124/124 passing.**

### The correctness call worth calling out

The cache fast-path (summing cached daily facts) is gated on `daily_cacheable`, which is **True only for `sales_summary`**. Every other scalar report answers from a **live exact-range summary fetch** — the summary block is exactly the number the POS dashboard shows, so answers match by construction rather than by trusting a per-day breakdown that (today) isn't valid for those reports. This deliberately trades some cache-hit rate for zero silently-wrong numbers — the failure mode doc 09 warns about most.

### The token wrinkle here (PLAN 02/04 open item)

Live fetches need the v2.0 report-API token; the chat path only has the stored v1.0 token, which 401s. So: **cache-hit questions (covered/closed historical `sales_summary` ranges) answer correctly with no token; live/uncovered questions 401 → `answer_metric_query` raises → main.py falls back to the existing SQL pipeline.** General-knowledge (persona, no data) and tier refusals work regardless. This is the honest working surface until the background-auth item is resolved.

**Acceptance status:** aggregation is additivity-correct (unit-proven); flag-OFF path is byte-for-byte the old pipeline (branch skipped entirely); router sends general-knowledge to a no-tools persona; business questions answer cache-first with live+write-through on miss. Not exercised end-to-end against a live tenant this session (no LLM key + the token gap), same constraint as PLAN 02–04.

**Next:** PLAN 06 — forecast + grounded insight tools (registered into the same report loop). PLAN 07 — SSE streaming of the report-loop narrative.

---

## PLAN 06 — Forecasting, Predictions & Business Insights

All gated by `INSIGHTS_ENABLED` (default OFF); requires `REPORT_CACHE_ENABLED`. Off = the report loop and routes are exactly PLAN 05.

- [x] `report_cache/insights/forecast.py` — `forecast_metric()` pulls the cached daily series (`read.get_daily_facts` over the tier window), guards **additive-only** (rejects ratio/non_additive), **daily-cacheable-only** (`sales_summary`), and **min ~6 weeks non-zero history** (graceful "not enough history" else), then reuses `analytics.run_forecast` (Prophet) — not re-implemented. Horizon capped (default 90). Returns history + forecast band + summary + disclaimer.
- [x] `report_cache/insights/trends.py` — `detect_anomalies()` wraps `analytics.run_anomaly_detection` over the cached series; `growth_summary()` computes additive MoM totals/% from cached daily facts (never sums non-additive).
- [x] `report_cache/insights/provenance.py` — pure numeric-provenance guard: `unsupported_numbers(text, allowed)` flags figures in generated advice not traceable to the fact pack (small ints 0–31 treated as dates/counts). Hard-asserted in tests; **soft signal (logged) in production** because general benchmarks legitimately introduce numbers.
- [x] `report_cache/insights/prompts.py` — insight system prompt: two clearly separated parts (**what your data shows** = only supplied numbers; **what I'd suggest** = general reasoning), currency + merchant context, uncertainty reminder.
- [x] `report_cache/insights/insight.py` — `generate_insight()` orchestration: builds a small **insight pack** (growth + forecast, both cache/token-free; top-products best-effort) memoised per (tenant, day); empty pack → graceful message with **no LLM call** (no fabrication risk); else LLM synthesis + provenance check. Returns the unified answer dict.
- [x] `report_cache/insights/tools.py` — `register_insight_tools(mcp, rctx)` adds `forecast_sales` / `sales_anomalies` / `sales_growth` to the report loop, no-op unless `INSIGHTS_ENABLED`. Identity stays server-side; shop names resolved+authorized.
- [x] `mcp_server/report_tools.py` — `build_report_mcp` now calls `register_insight_tools` (verified: 10 tools with the flag on, 7 with it off).
- [x] `main.py` — `_INSIGHTS_ENABLED` flag; `insight` route → `generate_insight` (when flag on) else the loop; `forecast`/`business_data` → the loop (which now has the forecast tools). Unified result handling unchanged.
- [x] `.env.example` — `INSIGHTS_ENABLED` + `INSIGHTS_FORECAST_MIN_DAYS` / `_MAX_HORIZON` / `_ANOMALY_LOOKBACK_DAYS` (safe defaults).
- [x] Tests: `tests/test_forecast.py` (7 — rising series projects forward with ordered band, insufficient history graceful, ratio rejected, non-cacheable rejected, horizon cap, MoM growth, ratio-growth rejected; Prophet runs for real) + `tests/test_insight.py` (5 — provenance extract/flag, small-int exemption, pack walk, insight cites only pack numbers, empty pack → no LLM call). **Full suite: 136/136 passing.**

### Design decisions worth noting

- **Forecast/anomaly/growth are TOOLS in the loop** (tools-first, consistent with PLAN 05) so the model parses metric/horizon/shop from the question; **insight is an orchestrated synthesis** (deterministic grounded pack + provenance-guarded prompt) for higher-quality, honest advice — matching the plan's split (Steps 1–2 tools vs Step 3 orchestration).
- **Cache/token-free by design.** Forecast + growth read only cached daily `sales_summary` facts, so they work in the chat path despite the PLAN 02/04 token gap. The top-products pack item needs a live token and is best-effort (skipped silently when unavailable).
- **`business_data` proactive one-liner (plan Step 4, optional): skipped** — kept lazy; add later behind its own flag if wanted.

**Acceptance status:** "forecast next month" returns a data-grounded forecast with a confidence range + disclaimer; "any suggestions?" returns advice separating real data findings from general recommendations with a provenance check; insufficient-history / out-of-window / ratio-metric cases handled gracefully; flag OFF = no change. Not exercised end-to-end against a live tenant this session (no LLM key + token gap), same constraint as PLAN 02–05; the deterministic pieces (forecast math, growth, provenance) are unit-proven with Prophet running for real.

**Next:** PLAN 07 — SSE streaming of whatever this returns (`/v1/query/stream`).
