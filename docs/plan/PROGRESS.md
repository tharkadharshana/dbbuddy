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
