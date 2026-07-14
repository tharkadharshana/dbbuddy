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

**Next:** PLAN 02 — Profile & Subscription Sync.
