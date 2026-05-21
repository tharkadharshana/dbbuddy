# DataMind AI — Engineering Changelog

> **Last updated:** 2026-05-21  
> **Branch at time of writing:** `feature/m4-embed-api-v1`  
> This document is a running record of every significant change made to the platform — including implementation logic, design decisions, known gaps, and work still outstanding. It is intended as the canonical reference for onboarding, post-mortems, and handoff.

---

## Table of Contents

1. [Phase 1 — Core Platform](#phase-1--core-platform)
2. [Phase 2 — Dynamic Analytics](#phase-2--dynamic-analytics)
3. [Phase 3 — SalesPlay & Loyverse Integration](#phase-3--salesplay--loyverse-integration)
4. [M1 — Licensing & Billing](#m1--licensing--billing)
5. [M2 — Unified DB Architecture](#m2--unified-db-architecture)
6. [M4 — Embed API & iFrame Widget](#m4--embed-api--iframe-widget)
7. [M4 — Partner API v1](#m4--partner-api-v1)
8. [M4 — API Infrastructure & Versioning](#m4--api-infrastructure--versioning)
9. [Security Hardening](#security-hardening)
10. [Bug & Stability Fixes](#bug--stability-fixes)
11. [M3 — Infrastructure (post-MVP)](#m3--infrastructure-post-mvp)
12. [Phase 4–6 — QA, UI/UX, Stabilization](#phase-46--qa-uiux-stabilization)
13. [Known Gaps & Assumptions](#known-gaps--assumptions)
14. [Outstanding Work](#outstanding-work)

---

## Phase 1 — Core Platform

**Status: Completed**

The foundational layer of the platform. Covers everything needed to run DataMind as a standalone product before any external integrations.

### What was built
- Full-stack architecture: FastAPI backend, React + Vite frontend
- User authentication with JWT (HS256, 7-day expiry, `auth.py`)
- User settings storage (DB connection config, API keys)
- BYODB (bring-your-own-database): users connect their own MySQL/MariaDB instance
- Schema introspection: `get_table_schemas()`, `get_foreign_keys()`, `get_sample_data()` in `db.py`
- Natural language → SQL query engine (`main.py` + `llm.py`)
- Analytics engine: RFM segmentation, cohort retention, basket analysis, growth metrics (`analytics.py`)
- Report generation with AI-written narrative summaries
- Time series forecasting using **Prophet**
- Anomaly detection using **IsolationForest** (scikit-learn)
- Light Mode UI support

### Key design decisions
- Two separate database contexts exist throughout the entire codebase:
  - **User DB**: the customer's own MySQL instance, connected via their saved credentials
  - **DataMind internal DB**: the platform's own storage for users, subscriptions, integrations, etc.
- Schema sent to LLM is pre-filtered to strip sensitive columns (passwords, keys, SSNs — see SEC-12)
- Sample row data is never sent to LLM; only column names and types are transmitted (SEC-13)

---

## Phase 2 — Dynamic Analytics

**Status: Completed**

Added cache-based analytics so that frequently-used queries and template outputs are not re-computed on every call.

### What was built
- Cache layer keyed on (user_email, template_id, date_range)
- Schema discovery: auto-scans user DB at connect time to build a column→table map
- Analytics template generation: LLM-assisted creation of SQL templates from schema
- Analytics catalog builder: user can browse, save, and run their own templates

### Key design decisions
- Cache TTL: 1 hour — evicted via `_build_status` TTL mechanism (also capped at 500 progress lines to prevent unbounded memory growth)
- Templates are stored per-user; catalog is not shared between accounts
- Template SQL goes through `_guard_sql()` before execution (see SEC-04)

---

## Phase 3 — SalesPlay & Loyverse Integration

**Status: Completed**

Added the first two external POS data providers: SalesPlay and Loyverse.

### SalesPlay
- REST API integration: products, shops, categories, receipts
- Sync writes to per-user tables: `{prefix}shops`, `{prefix}products`, `{prefix}categories`, `{prefix}receipts`, `{prefix}receipt_line_items`, `{prefix}customers`, `{prefix}payment_types`
- **Note:** These per-user tables were later replaced by the unified schema in M2 and now exist only as SQL views

### Loyverse
- Full POS integration: stores, employees, categories, products, variants, customers, receipts, receipt line items, payment line items (9 entity types)
- Same sync pattern as SalesPlay; same migration to unified views in M2

### Sync scheduling (original)
- `threading.Thread` based scheduler, 60-second tick
- Each provider has a configured sync interval (SalesPlay = 30 min)
- **Known issue at time of writing:** `threading.Thread` is a single-process solution; replaced by MySQL advisory lock to prevent duplicate runs in multi-worker deployments (see Bug Fixes). Full replacement with SQS queue is scheduled for M3.

---

## M1 — Licensing & Billing

**Status: Completed** | Branch: `feature/licensing-credit-module`

Complete subscription, credit, and plan enforcement system. Replaces the original ad-hoc credit mechanism with a unified token model.

### Subscription Plans

Three tiers seeded into `subscription_plans` at startup:

| Plan | Price | Tokens/mo | DB Rows | History | Features |
|------|-------|-----------|---------|---------|----------|
| Starter | $5 | 500 | 2M | 30 days | Analytics only |
| Growth | $10 | 1,500 | 5M | 90 days | + Forecasting, Anomaly Detection |
| Pro | $25 | 10,000 | 20M | 365 days | + Partner API |

### Token Formula

Every chargeable operation converts to tokens using:

```
Tokens = (llm_tokens / 1000) + (rows_returned / 1000) + FEATURE_COST[operation_type]
```

Minimum charge per operation: **0.1 tokens** (prevents free-riding on tiny queries).

`FEATURE_COST` mapping (hardcoded in `billing.py`):
- `forecasting` → 2.0
- `anomaly_detection` → 2.0
- `analytics` → 1.0–2.0 depending on complexity
- `template_run` → 1.0
- `query` → 0.5

### Credit Deduction Flow

1. Operation completes and returns a row count + LLM token count
2. `calculate_tokens(op_type, llm_tokens, rows_returned)` → token amount
3. `charge_tokens(user_email, tokens, op_type)` → writes to `usage_log`, increments `subscription_usage.tokens_used`
4. `check_ai_limit(user_email)` → called pre-operation; blocks if `tokens_used >= tokens_limit`

### Database Tables

- **`subscription_plans`**: id, name, price_cents, tokens_limit, trial_days=14, validity_days=30
- **`user_subscriptions`**: user_email, plan_id, status ENUM(`trial|active|expired|cancelled`), period_start, period_end
- **`subscription_usage`**: user_email, period_start, tokens_used DECIMAL(12,4) — one row per billing period per user
- **`usage_log`**: user_email, tokens, operation_type, llm_tokens, rows_charged, created_at — one row per operation (audit trail)
- **`addon_purchases`**: user_email, addon_type, units_remaining

### Free Trial
- `start_trial()` called on new user registration → 14-day trial at Starter tier
- Same token limits as Starter plan apply during trial
- Trial expiry handled by `_process_subscription()` which runs on each subscription read: sets `status='expired'` when `period_end < today`

### Add-On Packages
```python
ADDON_PACKAGES = {
    "ai_credits": {"units": 50, "price_cents": 100},   # $1 per 50 tokens
    "db_rows":    {"units": 100_000, "price_cents": 100}  # $1 per 100K rows
}
```
Add-on balance carries over; doesn't reset with billing period.

### Feature Gates
```python
_PLAN_FEATURE_GATE = {
    "forecast":           ["growth", "pro"],
    "anomaly_detection":  ["growth", "pro"],
    "partner_api":        ["pro"],
    "external_api":       ["starter", "growth", "pro"],  # all tiers
}
```
`check_plan_feature(user_email, feature)` raises HTTP 403 if the user's plan doesn't include the feature.

### Plan Upgrade/Downgrade
- `subscribe_to_plan()` cancels the active subscription and creates a new one immediately
- No proration logic — billing period resets on plan change
- Usage counter resets at the start of each new subscription period

### Usage Endpoint
- `GET /v1/billing/usage` → returns current period tokens used, remaining, percentage, plan details, trial end date

### Assumptions & Gaps
- `ai_credit_rate` multiplier in `billing_config` table only scales `T_llm`, not `T_db` or `T_feature` — was intentionally left simple
- Integration sync (full row ingest) does **not** charge tokens (Gap 1 in `docs/token-system.md`)
- Report section rows are not charged separately (Gap 2)
- Charges are applied to rows *returned* to the client, not rows *scanned* in the DB — intentional simplification (Gap 3)
- Cache build LLM cost is unverifiable on mid-build failure (Gap 5)

---

## M2 — Unified DB Architecture

**Status: Completed** | Completed: 2026-05-20

Replaced the previous per-user table architecture (one MySQL table per user per entity type) with a shared multi-tenant schema plus SQL views for backward compatibility.

### Problem with the old architecture
- Each user connection created 7–16 physical tables (e.g., `abc123_shops`, `abc123_receipts`)
- No shared indexes; analytics SQL had to be duplicated per user
- Migration tooling was fragile; adding a new provider required schema changes per existing user

### New Schema: Two Core Tables

**`integration_records`**
```sql
CREATE TABLE integration_records (
  id               BIGINT AUTO_INCREMENT PRIMARY KEY,
  tenant_id        VARCHAR(64)  NOT NULL,   -- == user's table_prefix
  user_email       VARCHAR(255) NOT NULL,
  provider_id      VARCHAR(64)  NOT NULL,
  record_type      VARCHAR(64)  NOT NULL,   -- e.g. 'receipts', 'products'
  external_id      VARCHAR(255) NOT NULL,   -- PK from external API
  data             LONGTEXT     NOT NULL,   -- JSON blob of normalized fields
  external_created_at DATETIME(3),
  external_updated_at DATETIME(3),
  synced_at        DATETIME(3)  NOT NULL,
  created_at       DATETIME(3)  NOT NULL,
  updated_at       DATETIME(3)  NOT NULL,
  UNIQUE KEY uq_record (tenant_id, provider_id, record_type, external_id),
  INDEX idx_sync   (tenant_id, provider_id, record_type, synced_at DESC),
  INDEX idx_user   (user_email, provider_id)
) ROW_FORMAT=COMPRESSED;
```

`ROW_FORMAT=COMPRESSED` gives ~30–50% disk savings on JSON-heavy rows.

**`integration_sync_state`**
```sql
CREATE TABLE integration_sync_state (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  tenant_id   VARCHAR(64)  NOT NULL,
  user_email  VARCHAR(255) NOT NULL,
  provider_id VARCHAR(64)  NOT NULL,
  record_type VARCHAR(64)  NOT NULL,
  last_synced_at DATETIME(3),
  status      ENUM('ok','error','syncing') NOT NULL DEFAULT 'ok',
  error_message TEXT,
  UNIQUE KEY uq_state (tenant_id, provider_id, record_type)
);
```

### SQL Compatibility Views

At integration connect time, `_create_views_for_integration()` generates views named identically to the old per-user tables. All existing analytics SQL continues to work unchanged.

**16 views total — created per integration:**

SalesPlay (7): `{prefix}shops`, `{prefix}categories`, `{prefix}payment_types`, `{prefix}products`, `{prefix}customers`, `{prefix}receipts`, `{prefix}receipt_line_items`

Loyverse (9): `{prefix}_stores`, `{prefix}_employees`, `{prefix}_categories`, `{prefix}_products`, `{prefix}_variants`, `{prefix}_customers`, `{prefix}_receipts`, `{prefix}_receipt_line_items`, `{prefix}_payment_line_items`

**MariaDB 10.4 JSON extraction syntax used throughout views:**
```sql
-- Strings:
JSON_UNQUOTE(JSON_EXTRACT(data, '$.field_name'))

-- Numerics:
CAST(JSON_EXTRACT(data, '$.field_name') AS DECIMAL(12,4))
```

Standard `->` and `->>` operators are NOT used because they require MariaDB 10.5+.

### `providers/upsert.py` — Shared Write Helper

All providers call `upsert_record()` instead of writing their own INSERT logic:

```python
def upsert_record(
    conn, tenant_id, user_email, provider_id, record_type,
    record, id_field="id", ext_created_field=None,
    ext_updated_field=None, budget=None
) -> bool:
```

Executes:
```sql
INSERT INTO integration_records (...)
VALUES (...)
ON DUPLICATE KEY UPDATE
  data = VALUES(data),
  external_updated_at = VALUES(external_updated_at),
  updated_at = NOW(3)
```

Returns `False` immediately if `budget.request()` fails (row quota exhausted).

`lookup_map(conn, tenant_id, provider_id, record_type, id_field, name_field)` pre-fetches reference data (shop names, customer names, payment type names) so receipt records can be enriched with human-readable names without extra queries per row.

### Reference vs Transactional Data Split

| Category | Tables | Sync mode | Row budget |
|----------|--------|-----------|------------|
| Reference | shops, categories, payment_types, products | Always full sync | No limit |
| Transactional | customers, receipts | Delta from history cutoff | Budget enforced |

Reference tables are small and needed for JOIN enrichment — syncing them fully on every run is intentional.

History cutoff by plan:
- Starter: 30 days
- Growth: 90 days
- Pro: 365 days

### Row Budget System

`RowBudget` instance created per sync run:
```
row_budget = plan.db_rows_limit - current_total_rows_used
```
Passed into `upsert_record()`. When the budget hits 0, the sync stops inserting and returns. The user is not shown an error — the quota is silently enforced.

### Sync Scheduling Logic

- Scheduler thread runs on 60-second tick
- MySQL advisory lock: `GET_LOCK('datamind_scheduler', 0)` — only the first worker acquires it, preventing duplicate runs in multi-process deployments
- Per-provider sync interval from manifest (SalesPlay = 30 min)
- Error backoff: failed integrations wait `interval × 5` before next attempt
- Stuck timeout: integrations stuck in `'syncing'` for >30 minutes are reset to `'error'` by the scheduler
- On startup, any integrations left in `'syncing'` state (from a previous crash) are reset to `'error'`

### `_sync_active` Guard

A module-level set:
```python
_sync_active: Set[int] = set()
```

Before any sync starts, the integration's ID is added. After completion (or failure), it's removed in a `finally` block. If a sync is already in progress for an integration, the scheduler and manual trigger both skip it — preventing the double-sync race condition.

### Migration Documentation
Full schema migration notes at `docs/unified-db-schema-migration.md`.

### MySQL `my.cnf` Tuning
- Red Hat deployment guide: `docs/deployment/` ← **⏳ To Do (target 2026-05-26)**

---

## M4 — Embed API & iFrame Widget

**Status: Partially complete (MVP subset done)**

Allows third-party platforms (e.g., SalesPlay's web portal) to embed DataMind's analytics as an iframe widget, with their own branding and API credentials.

### Architecture Overview

```
Partner website
  └── <script> tag loads embed bundle
       └── iframe → datamind.ai/embed
            └── EmbedApp.jsx
                 ├── EmbedOnboarding.jsx  (first-time users)
                 └── EmbedChat.jsx         (returning users)
```

### API Key & Partner Registry

- Partners are registered in the `embed_partners` table (bootstrapped at startup by `bootstrap_embed_tables()`)
- Schema: `partner_key (PK)`, `partner_name`, `provider_id`, `allowed_origins` (comma-separated), `branding` (JSON), `active` (TINYINT), `created_at`
- `partner_key` is the public identifier embedded in the `<script>` tag — it's not a secret
- **No per-user partner records** — one row per integration partnership, not per end user

### Embed Token System & Domain Allowlist

- At iframe load, frontend calls `GET /embed/context?pk={partner_key}`
- Response includes `allowed_origins[]` array
- Frontend `EmbedApp.jsx` uses this array for `postMessage` origin validation
- Requests from origins not in the allowlist are rejected at the JS level before any API calls

### Session Tokens (JWT)

- On successful `POST /embed/init`, a standard JWT is returned (same format as main app — HS256, 7-day expiry)
- Token stored in `localStorage` within the iframe's origin
- `embedApi.js` attaches it as `Authorization: Bearer <token>` on all subsequent requests
- On 401 response, `embedApi.js` calls `onExpired()` callback — the parent `EmbedApp.jsx` clears storage and shows onboarding again (iframe cannot redirect like a normal page)

### Onboarding Flow (`POST /embed/init`)

Single endpoint that handles the entire first-time setup:

1. Validate `partner_key` — 404 if unknown or inactive
2. Validate provider API token against the provider's own API
3. If `email` already exists → re-authenticate (return existing account's JWT)
4. If new → `create_user()` + `start_trial()` (14-day free trial, Starter plan)
5. `connect_provider()` — validates token, creates views, triggers initial sync
6. Return JWT + `{sync: "started"}`

Rate limit on `/embed/init` and `/embed/validate-token`: **5 calls/minute per IP** (in-memory store, `_check_rate()` in `embed.py`).

`_client_ip()` correctly handles `X-Forwarded-For` headers and strips ports from IPv6 addresses.

### Standalone iFrame Bundle

- Built with Vite as a separate entry point from the main app
- Deployed as a static bundle; embedded via `<script>` tag
- Theme (light/dark) passed from parent page via postMessage

### Known Gaps / Not Yet Implemented

| Item | Status | Target |
|------|--------|--------|
| Trial status check on every iframe load | ⏳ To Do | 2026-05-29 |
| Inline upgrade CTA inside iframe | ⏳ To Do | 2026-05-29 |
| Responsive sizing via postMessage | ⏳ To Do | 2026-07-08 |
| OAuth redirect inside iframe | ⏳ To Do | 2026-07-11 |
| Silent token refresh | ⏳ To Do | 2026-07-18 |
| Full OAuth 2.0 consent flow | ⏳ To Do | 2026-06-13 |

**Assumption:** For MVP, the embed widget only supports providers that use API-key authentication (SalesPlay, Loyverse). OAuth-based providers require the post-MVP OAuth flow.

---

## M4 — Partner API v1

**Status: Completed** | Completed: 2026-05-20

Server-to-server API allowing partner platforms to programmatically access DataMind on behalf of their users.

### Authentication

- Header: `X-API-Key: <partner_key>`
- Validated by `_require_partner()` — queries `embed_partners` table
- User identified by `user_email` query param or request body
- **Requires Pro plan** — `_require_pro()` returns HTTP 403 for non-Pro users

### Rate Limiting

- Separate `partner_limiter` instance (from `limiter.py`)
- Key function: `X-API-Key` value, fallback to IP
- Default limit: **30 requests/minute per key** (configurable via `RATE_LIMIT_V1` env var)

### Endpoints

**`GET /v1/partner/integrations`**
- Returns list of user's connected integrations with status, `last_sync`, `row_count`
- Status mapping: `active→connected`, `syncing→syncing`, `error→error`, `pending→pending`

**`POST /v1/partner/sync/{provider}`**
- Body: `{user_email, full: bool}`
- Per-user+provider rate limit: 1 manual sync per 5-minute window
- Returns: `{ok, status, provider, sync_type}`

**`GET /v1/partner/records/{provider}/{type}`**
- Paginated access to synced records by `record_type` (e.g., `receipts`, `products`)
- Supports `limit` / `offset` pagination params
- Queries `integration_records` table directly

**`GET /v1/partner/analytics/{template_id}`**
- Runs a pre-built analytics template
- Deducts credits from the user's account (same as running it in the iframe)
- Returns: `{ok, source, provider, title, row_count, truncated, data, columns}`
- Available `template_id` values:

| template_id | Description |
|-------------|-------------|
| `customer_rfm` | RFM segmentation |
| `customer_cohort` | Cohort retention matrix |
| `basket_analysis` | Association rules + lift |
| `growth_metrics` | Revenue growth over time |
| `cashier_performance` | Sales per employee |
| `product_velocity` | Top/bottom moving products |
| `payment_methods` | Payment breakdown |
| `location_comparison` | Revenue by store |

**`GET /v1/partner/usage`**
- Returns: `plan`, `status`, `tokens_used`, `tokens_remaining`, `tokens_limit`, `tokens_pct`, `trial_ends_at`, `period_start`, `period_end`

### Charge Logic

```python
def _charge_op(user_email, op_type, rows):
    tokens = calculate_tokens(op_type, rows_returned=rows)
    charge_tokens(user_email, tokens, op_type)
```

Never raises on charge failure — charge failures are logged as `WARNING` but the request still succeeds (fail-open, consistent with billing.py pattern).

### OpenAPI Spec & SDKs

- `openapi.yaml` — static OpenAPI 3.0.3 spec committed to repo root
- `scripts/export_openapi.py` — regenerates spec from live FastAPI app without running the server
- Python SDK: `sdk/python/` — zero-dependency, stdlib `urllib` only, `setup.py` included
- JavaScript SDK: `sdk/javascript/` — ESM module, native `fetch`, requires Node ≥18

### Pydantic Response Models

All 5 Partner API endpoints have explicit Pydantic `response_model` declarations, producing a typed OpenAPI schema with no `{}` or `Any` return types.

---

## M4 — API Infrastructure & Versioning

**Status: Completed** | Completed: 2026-05-20

### Route Versioning

All 47 user-facing routes moved under `/v1/` prefix:
```
Before: /query, /analytics/run, /integrations, /billing/usage ...
After:  /v1/query, /v1/analytics/run, /v1/integrations, /v1/billing/usage ...
```

**Exceptions (intentionally kept unversioned):**
- `GET /health` — kept at root for load balancer health checks; versioning it would break existing infra configs
- `/embed/*` — kept unversioned because these URLs are live in partner iframes; changing them would silently break deployed embeds on partner sites

Vite dev proxy updated: `/api → /v1` to match.

### Rate Limiting Architecture

Two separate `slowapi` `Limiter` instances in `limiter.py`:

| Instance | Key function | Used by |
|----------|-------------|---------|
| `limiter` | Client IP (respects `X-Forwarded-For`) | All 47 user endpoints |
| `partner_limiter` | `X-API-Key` header, fallback to IP | All `/v1/partner/*` endpoints |

Six configurable tiers via `.env`:

| Env var | Default | Applied to |
|---------|---------|-----------|
| `RATE_LIMIT_AUTH` | `5/minute` | `POST /v1/auth/register` |
| `RATE_LIMIT_AUTH_LOGIN` | `10/minute` | `POST /v1/auth/login` |
| `RATE_LIMIT_COMPUTE` | `10/minute` | `/v1/query`, `/v1/analytics/run`, `/v1/forecast`, `/v1/anomalies`, `/v1/report` |
| `RATE_LIMIT_READ` | `60/minute` | All GET endpoints |
| `RATE_LIMIT_WRITE` | `30/minute` | Settings, billing, sync endpoints |
| `RATE_LIMIT_V1` | `30/minute` | All `/v1/partner/*` endpoints (per API key) |

Rate limits were previously applied only to 2 auth endpoints. Now applied to all 47.

### `X-Request-ID` Middleware

Every response includes a `X-Request-ID` header:
- If client sends `X-Request-ID` in the request → the same value is echoed back
- If not sent → a new UUID4 is generated
- All log lines include the request ID for correlation

### CORS Configuration

```python
# Defaults (local dev)
origins = ["http://localhost:5173", "http://localhost:3000"]

# Production: read from env
origins += os.getenv("EMBED_ALLOWED_ORIGINS", "").split(",")
```

Never defaults to `*`. Allowed methods: GET, POST, PUT, PATCH, DELETE, OPTIONS.

---

## Security Hardening

**Status: Mostly complete; 2 items deferred**

All items tagged with `SEC-##` comments in source code.

### SEC-01 — Rotate Exposed DeepSeek API Key
**⚠️ Manual action required** — a DeepSeek API key was exposed in git history. Must be rotated in the DeepSeek dashboard and updated in all deployment `.env` files. No code change needed.

### SEC-02 — Strip Raw Exception Strings from API Responses
All exception handlers replaced with `_server_error()` wrapper:
```python
# Before: {"detail": str(e)}  ← leaks stack traces, internal paths
# After:  {"ok": false, "error": "Internal server error"}
```
Production error detail is logged server-side only.

### SEC-03 — SQL Injection in Integration Forecast
Table and column names from user-provided data are now validated against the actual schema before being interpolated into SQL. `_validate_table_column(name, allowed_set)` raises 422 if the name isn't in the schema. Backticks are applied around validated identifiers.

### SEC-04 — LLM-Generated SQL Mutation Guard
`_guard_sql(sql)` runs a regex before executing any LLM-generated query:
```python
MUTATION_PATTERN = re.compile(
    r'\b(DROP|DELETE|INSERT|UPDATE|TRUNCATE|ALTER|CREATE|REPLACE|GRANT|REVOKE|CALL|EXEC)\b',
    re.IGNORECASE
)
```
Raises HTTP 400 if matched. Applied to all `/v1/query` executions.

### SEC-05 — Startup Warning for Default SECRET_KEY
`auth.py` issues `warnings.warn()` at import time if `SECRET_KEY` matches the default development value `"datamind-secret-change-in-production-2024"`.

### SEC-06 — DB Passwords Encrypted at Rest (Fernet)
User-provided database passwords are encrypted before storing in `user_settings`:
```python
# Key derivation
key = base64.urlsafe_b64encode(hashlib.sha256(ENCRYPTION_KEY.encode()).digest())
fernet = Fernet(key)

# Encrypt on save, decrypt on use
_encrypt_db_password(pw) → token
_decrypt_db_password(token) → pw  # gracefully falls back to plaintext for migration
```
Same Fernet approach used for integration credentials in `integrations.py` (`_get_fernet()`, `_encrypt()`, `_decrypt()`).

### SEC-07 — CORS No Longer Defaults to Wildcard
See [CORS Configuration](#cors-configuration) above.

### SEC-08 — Standard HTTP Error Envelope
All 4xx and 5xx responses return:
```json
{"ok": false, "error": "..."}
```
Implemented via FastAPI exception handlers for `HTTPException` and `RequestValidationError`.

### SEC-09 — Upgrade KDF from SHA-256 to PBKDF2
**⏳ Deferred to post-MVP.** Currently key derivation uses `SHA256(ENCRYPTION_KEY)`. Should be upgraded to PBKDF2 (or Argon2) with a per-record salt for defense-in-depth. Risk is low because `ENCRYPTION_KEY` is a secret env var — but a key derivation upgrade is still best practice.

### SEC-10 — Rate Limiting on All Endpoints
See [Rate Limiting Architecture](#rate-limiting-architecture) above.

### SEC-11 — Table Prefix Entropy Expanded
Integration table prefixes (used as tenant IDs) expanded from 8 hex chars to **16 hex chars** using SHA-256 of the user's email + timestamp. Reduces collision risk and makes prefix enumeration impractical.

### SEC-12 — Sensitive Columns Stripped Before LLM Transmission
`_filter_sensitive_schema()` in `llm.py` drops columns matching:
```
password|passwd|secret|api_key|access_token|ssn|cvv|card_number|bank_account|...
```
Applied before sending any schema to Gemini or DeepSeek.

### SEC-13 — Sample Data Replaced with Type Statistics
Schema introspection no longer sends actual row values to the LLM — only column names and types. `get_sample_data()` still works internally for report context but is not included in LLM prompts.

### SEC-14 — HTTPS Redirect + HSTS + Security Headers
When `FORCE_HTTPS=true` in `.env`:
```
HTTP → HTTPS redirect (HTTPSRedirectMiddleware)
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
```
Red Hat nginx + HTTPS deployment guide created in `docs/deployment/`.

---

## Bug & Stability Fixes

**Completed: 2026-05-20**

### `_build_status` TTL Eviction
Analytics build status cache entries now expire after 1 hour. The progress list per build is capped at 500 lines. Without this, long-running deployments accumulated unbounded in-memory state.

### Double-Sync Race Condition
`_sync_active` set (see M2) prevents scheduler and manual trigger from running the same integration concurrently. Previously, a manual "sync now" click during a scheduled sync would corrupt the sync state.

### Connection Leaks in `_run_sync`
`_run_sync()` now wraps the entire sync execution in `try/finally`, ensuring the database connection is always returned to the pool even if the sync raises an exception mid-way.

### Scheduler Watchdog Restart
If the scheduler thread dies unexpectedly (unhandled exception in `_scheduler_tick`), a watchdog thread detects this and restarts it. MySQL advisory lock is reacquired on restart.

### Billing Fail-Open Escalated Logging
The billing system is designed to fail-open (let operations through if billing DB is unavailable). This is intentional for availability. However, repeated billing failures now emit `log.error()` (previously `log.warning()`), ensuring they are caught by production alerting.

### Embed Rate Limiter — Proxy-Aware IP
`_client_ip()` in `embed.py` reads `X-Forwarded-For` correctly so that users behind an ALB or reverse proxy are rate-limited by their real IP, not the proxy IP.

### Provider Analytics SQL — Table Prefix Validation
`{prefix}` in analytics SQL templates is now validated against a regex (`^[a-f0-9]{16}$`) before string formatting. Prevents SQL injection if a malformed prefix somehow ends up in the database.

### Stale Sync Status Fix
`last_sync_at` in `integration_sync_state` is now always written at the end of every sync run, including partial syncs and syncs that hit the row budget limit. Previously, a quota-limited sync would leave the timestamp stale, causing the scheduler to re-trigger immediately.

Error backoff: failed integrations now wait `interval × 5` before the next attempt.

### `get_user_total_rows` Returns `None` on Error
Previously returned `0` on DB error, which made the system think the user had used no rows — allowing quota bypass. Now returns `None`; callers treat `None` as "quota check inconclusive" and block the operation.

### `embed.py` — Partner Connection Moved to `try/finally`
`_get_partner()` connection in `embed.py` is now properly closed in a `finally` block, preventing connection leaks on onboarding error paths.

### Orphaned `syncing` Records Reset on Startup
On `app.startup`, `integrations.py` queries for any records stuck in `status='syncing'` and resets them to `status='error'`. This handles crash recovery — without it, a server restart after a mid-sync crash would leave integrations permanently stuck.

---

## M3 — Infrastructure (post-MVP)

### Completed
- MySQL connection pool (`pool.py`): `MySQLConnectionPool` with default size=20, configurable via `DB_POOL_SIZE` env var. All internal operations use `get_internal_conn()`.

### Scheduled

| Task | Target | Notes |
|------|--------|-------|
| SQS queue + replace `threading.Thread` scheduler | 2026-06-13 | Current threading approach doesn't scale horizontally |
| SQS worker consumer | 2026-06-17 | |
| Separate read replica | 2026-06-20 | Analytics queries currently hit the primary |
| Replace scheduler with AWS EventBridge | 2026-06-24 | Removes advisory lock complexity |

---

## Phase 4–6 — QA, UI/UX, Stabilization

### Phase 4 — QA

| Test | Status | Target |
|------|--------|--------|
| Smoke test: connect → sync → analytics → billing end-to-end | ⏳ Pending | 2026-05-29 |
| Validate analytics outputs against source DB | ⏳ Pending | 2026-07-02 |
| Validate forecasting accuracy | ⏳ Pending | 2026-07-03 |
| Verify correct historical data in predictions | ⏳ Pending | 2026-07-03 |
| Validate anomaly detection outputs | ⏳ Pending | 2026-07-04 |

### Phase 5 — UI/UX

| Item | Status | Target |
|------|--------|--------|
| Light mode support | ✅ Completed | |
| Basic responsiveness (tablet + desktop) | ⏳ Pending | 2026-05-28 |
| Full mobile UI | ⏳ Pending | 2026-07-23 |
| UI consistency polish | ⏳ Pending | 2026-07-25 |

### Phase 6 — Stabilization

| Item | Status | Target |
|------|--------|--------|
| MVP blocker bug fixes | 🔄 In Progress | 2026-05-30 |
| Deployment readiness (env vars, secrets, prod config) | 🔄 In Progress | 2026-05-30 |
| Performance testing | ⏳ Pending | 2026-07-30 |
| Final integration testing | ⏳ Pending | 2026-07-31 |

---

## Known Gaps & Assumptions

These are intentional simplifications made for MVP speed, documented here so they don't get forgotten.

### Billing
1. **Sync rows not charged**: Ingesting 500K receipt rows consumes no tokens. Recommendation: add a `charge_tokens(user_email, rows/1000, "sync")` call at the end of `_run_sync_inner()`.
2. **Report section rows not charged**: Each section of a multi-section report charges tokens independently for the LLM summary but not for the data rows fetched.
3. **Charged rows = result rows, not scanned rows**: A query that scans 1M rows but returns 10 costs the same as one that scans 10 rows and returns 10. Acceptable for MVP.
4. **`ai_credit_rate` multiplier scope**: Only scales the LLM token component (`T_llm`), not the DB row or feature components. A future billing revision may want to scale all three.
5. **Cache build LLM cost on failure**: If a cache-building LLM call fails mid-way, the partial tokens consumed are not refunded.

### Architecture
6. **Single-process scheduler**: The advisory lock prevents duplicate runs but still relies on `threading.Thread`. A horizontal scale-out event (multiple Gunicorn workers on separate hosts) would require the SQS migration (M3).
7. **Row budget is soft**: The budget check happens inside `upsert_record()` — there is a small window where a concurrent sync could slightly overshoot the quota before both threads detect the limit.
8. **JWT never revoked**: Tokens are valid for 7 days with no server-side revocation. Logging out only clears the client-side token. A token blocklist (Redis) would be needed for forced logout.
9. **Embed partner_key is not secret**: It's embedded in the `<script>` tag and visible to anyone viewing page source. Security relies on the `allowed_origins` allowlist, not on keeping the key secret.
10. **No payment processor**: Billing plans and token tracking are fully implemented, but there is no Stripe/payment integration. Plan upgrades are currently manual operations (admin sets plan directly in DB).

### Deployment
11. **`FORCE_HTTPS` defaults to false**: HTTPS is not enforced unless explicitly set. Red Hat deployment guide covers nginx SSL termination as the recommended production approach.
12. **`my.cnf` tuning not done**: MariaDB 10.4 performance tuning for production workloads is documented as a task (`docs/deployment/`) but not yet completed (target 2026-05-26).
13. **DeepSeek key in git history**: SEC-01 requires manual key rotation. No automated detection or alerting is in place for leaked secrets.

---

## Outstanding Work

### MVP Critical (before 2026-05-30)

- [ ] Trial status check on every iframe load — user should see expiry warning inline
- [ ] Inline upgrade CTA in iframe
- [ ] Basic tablet/desktop responsiveness
- [ ] Smoke test: full end-to-end (connect → sync → analytics → billing)
- [ ] Deployment readiness: production `.env` review, secrets management
- [ ] `my.cnf` MariaDB tuning for Red Hat deployment (target 2026-05-26)

### Post-MVP (planned)

- [ ] SEC-01: Rotate DeepSeek API key (**do this immediately**)
- [ ] SEC-09: Upgrade KDF to PBKDF2
- [ ] Payment processor integration (Stripe)
- [ ] Full OAuth 2.0 consent flow for embed (2026-06-13)
- [ ] SQS queue + worker (2026-06-13/17)
- [ ] Separate read replica (2026-06-20)
- [ ] EventBridge scheduler replacement (2026-06-24)
- [ ] Silent token refresh in iframe (2026-07-18)
- [ ] Full QA pass on analytics outputs
- [ ] Full mobile UI (2026-07-23)
- [ ] Performance testing (2026-07-30)

---

*Document maintained by: Tharka Karunanayake*  
*Auto-updated from: engineering progress tracker + codebase audit*
