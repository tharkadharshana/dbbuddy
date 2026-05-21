# DataMind AI — Database Schema Reference

> **Last updated:** 2026-05-21  
> All tables live in **DataMind's internal MySQL database** (configured via `DATAMIND_DB_*` env vars) unless explicitly noted. This is completely separate from a user's "bring your own database" connection configured in Settings.  
> Every table is created idempotently at server startup — `CREATE TABLE IF NOT EXISTS` — so it is safe to restart without running a separate migration script.

---

## Table of Contents

1. [Database Contexts](#database-contexts)
2. [Core Tables](#core-tables)
   - [users](#users)
3. [Billing & Subscription Tables](#billing--subscription-tables)
   - [subscription_plans](#subscription_plans)
   - [user_subscriptions](#user_subscriptions)
   - [subscription_usage](#subscription_usage)
   - [usage_log](#usage_log)
   - [llm_usage_log](#llm_usage_log)
   - [billing_config](#billing_config)
   - [addon_purchases](#addon_purchases)
4. [Integration Tables](#integration-tables)
   - [user_integrations](#user_integrations)
   - [integration_records](#integration_records)
   - [integration_sync_state](#integration_sync_state)
   - [sync_logs](#sync_logs)
5. [Embed / Partner Tables](#embed--partner-tables)
   - [embed_partners](#embed_partners)
6. [Virtual Tables (SQL Views)](#virtual-tables-sql-views)
   - [SalesPlay Views (7)](#salesplay-views)
   - [Loyverse Views (9)](#loyverse-views)
7. [Relationships Diagram](#relationships-diagram)
8. [Index Reference](#index-reference)

---

## Database Contexts

There are **two completely separate database contexts** in DataMind:

| Context | Env vars | What lives here |
|---------|----------|-----------------|
| **DataMind internal DB** | `DATAMIND_DB_*` | All tables in this document |
| **User's own DB** | Saved in `users.settings` → `db_configs` | Customer's business data — queried via NL→SQL but never written to by DataMind |

Every table documented below is in the **DataMind internal DB**. The user's own DB is only ever read; DataMind never creates tables in it.

---

## Core Tables

### `users`

**Defined in:** `auth.py`  
**Purpose:** The master user registry. Every DataMind account — whether registered through the main app or through the embed onboarding wizard — has exactly one row here.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `email` | `VARCHAR(255)` | NO — PRIMARY KEY | The user's email address, always stored lowercase. Used as the universal identifier across every other table. Every other table references users by `user_email` (this value), never by a numeric ID. |
| `name` | `VARCHAR(255)` | NO | Full display name. Collected on registration or embed onboarding. Shown in the top-right of the UI. |
| `password_hash` | `VARCHAR(255)` | NO | bcrypt hash of the user's password. The plaintext password is never stored anywhere. Verified by `verify_password()` in `auth.py` using the `passlib` library. |
| `settings` | `JSON` | NO | A JSON object containing all per-user configuration. This avoids a wide settings table and allows adding new settings without schema migrations. **Structure of the JSON object:** `gemini_api_key` (user's own Gemini key, used instead of the server fallback), `deepseek_api_key` (user's own DeepSeek key), `db_configs` (array of user's saved DB connection configs — each has host, port, name, user, and an Fernet-encrypted password), `active_db_index` (which db_config is currently selected), `default_llm` ("gemini" or "deepseek"), `theme` ("dark" or "light"). |
| `created_at` | `DATETIME` | NO | UTC timestamp when the account was created. Used for cohort reporting and audit purposes. |

**Notes:**
- Email is used as the primary key instead of a numeric ID because it is already globally unique, human-readable, and used as the join key everywhere. There is no auto-increment ID.
- `db_configs` inside `settings` stores DB passwords encrypted with Fernet (SEC-06). The encryption key is derived from `ENCRYPTION_KEY` env var.
- Default settings (applied when a key is missing from the stored JSON): `gemini_api_key=""`, `deepseek_api_key=""`, `db_configs=[]`, `active_db_index=0`, `default_llm="gemini"`, `theme="dark"`.

---

## Billing & Subscription Tables

### `subscription_plans`

**Defined in:** `billing.py`  
**Purpose:** The product catalogue. Defines the three subscription tiers available to users. Seeded and kept up to date on every server startup — you never need to manually INSERT or UPDATE plan data; change the seed values in `billing.py` and restart.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal plan ID. Referenced by `user_subscriptions.plan_id`. |
| `name` | `VARCHAR(50)` | NO | Human-readable plan name. One of: `"Starter"`, `"Growth"`, `"Pro"`. Used in feature gate lookups (`_PLAN_FEATURE_GATE` dict in `billing.py`), history limit lookups, and displayed in the UI billing page. |
| `price_usd` | `DECIMAL(10,2)` | NO | Monthly price in USD. Display only — DataMind has no payment processor; upgrades are currently manual. |
| `price_cents` | `INT` | NO | Monthly price in cents (e.g. 500, 1000, 2500). Stored separately for payment processor integration when Stripe is added. |
| `ai_credits` | `INT` | NO | Legacy column — kept for backwards compatibility. No longer used in quota enforcement; `tokens_limit` is the authoritative limit. |
| `db_rows` | `BIGINT` | NO | Maximum number of synced integration records the user's account can hold. Enforced during sync by the `RowBudget` system in `integrations.py`. Values: Starter=2,000,000 · Growth=5,000,000 · Pro=20,000,000. |
| `tokens_limit` | `DECIMAL(12,4)` | NO | Maximum tokens the user can consume per billing period. Enforced by `check_ai_limit()` before every chargeable operation. Values: Starter=500 · Growth=1,500 · Pro=10,000. |
| `trial_days` | `INT` | NO | How many days a new user gets as a free trial. Currently `14` for all plans. `start_trial()` reads this value — change it here to adjust trial length globally. |
| `validity_days` | `INT` | NO | How long a paid subscription lasts (days). Currently `30` for all plans (monthly billing). Used by `subscribe_to_plan()` to compute `period_end`. |
| `is_active` | `TINYINT(1)` | NO | `1` = plan is available for new subscriptions, `0` = retired/hidden. Only plans with `is_active=1` are returned to the billing UI. Allows retiring a plan without deleting it. |
| `sort_order` | `INT` | NO | Display order in the pricing UI. Starter=1, Growth=2, Pro=3. |

---

### `user_subscriptions`

**Defined in:** `billing.py`  
**Purpose:** Records every subscription period a user has ever had. Each row represents one billing period (trial, active, expired, or cancelled). A user can have multiple rows here over time — one per plan change or renewal. Only the most recent `trial` or `active` row is "live".

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `user_email` | `VARCHAR(255)` | NO | The user this subscription belongs to. Matches `users.email`. Indexed via `idx_sub_email`. |
| `plan_id` | `INT` | NO | Which plan this row is for. Foreign key to `subscription_plans.id`. |
| `status` | `ENUM` | NO | Lifecycle state of this subscription row. `trial` = 14-day free trial in progress. `active` = paid subscription in progress. `expired` = period_end has passed and the user has not renewed. `cancelled` = explicitly cancelled (e.g. when the user upgrades — the old row is set to `cancelled` and a new row is inserted). |
| `period_start` | `DATE` | NO | The first day of this billing period. Used as the key into `subscription_usage` to look up how many tokens have been consumed this period. |
| `period_end` | `DATE` | NO | The last day of this billing period. `_process_subscription()` compares this against `date.today()` on every billing check. If `period_end < today` and status is `trial` or `active`, the row is flipped to `expired`. There is no background job — expiry is lazy. |
| `created_at` | `TIMESTAMP` | YES | When this subscription row was created. Useful for audit and support. |

**Key behaviour:**
- When a user upgrades or downgrades, `subscribe_to_plan()` sets the old row to `status='cancelled'` then inserts a fresh `status='active'` row. Both rows are preserved for history.
- Querying for the "current" subscription always uses `ORDER BY id DESC LIMIT 1` with `status IN ('trial','active')`.

---

### `subscription_usage`

**Defined in:** `billing.py`  
**Purpose:** The rolling token counter for each billing period. One row per user per billing period. `tokens_used` increments with every chargeable operation. This is what `check_ai_limit()` reads to decide whether to block or allow the next operation.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `user_email` | `VARCHAR(255)` | NO | The user this usage row belongs to. Part of the unique key. |
| `period_start` | `DATE` | NO | The billing period this usage row belongs to. Matches `user_subscriptions.period_start` for the active subscription. Part of the unique key — one row per user per period. |
| `tokens_used` | `DECIMAL(12,4)` | NO | Running total of tokens consumed this period. Incremented atomically via `ON DUPLICATE KEY UPDATE tokens_used = tokens_used + ?` in `charge_tokens()`. Four decimal places because the token formula produces fractional values (e.g. 0.8 tokens for 800 LLM tokens). |

**Key behaviour:**
- The `UNIQUE KEY uq_usage (user_email, period_start)` means the first `charge_tokens()` call of a new period does an INSERT; all subsequent calls do an UPDATE. There is no explicit "reset" when a new period starts — the new period gets a new `period_start` key, so the old row is naturally ignored.
- When a user upgrades mid-period, their token usage resets because the new subscription has a different `period_start`.

---

### `usage_log`

**Defined in:** `billing.py`  
**Purpose:** Append-only audit trail of every single chargeable operation. One row per operation. Unlike `subscription_usage` (which only tracks the running total), this table records the individual events — what operation was performed, how many LLM tokens were used, and how many rows were returned. Used for the "Usage History" tab in the billing UI and for debugging billing discrepancies.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `user_email` | `VARCHAR(255)` | NO | The user who triggered the operation. Indexed via `idx_ulog_email`. |
| `tokens` | `DECIMAL(12,4)` | NO | Total tokens charged for this operation, computed by `calculate_tokens()`. Formula: `(llm_tokens/1000) + (rows_returned/1000) + FEATURE_COST[operation_type]`. Minimum 0.1. |
| `operation_type` | `VARCHAR(50)` | NO | What kind of operation was charged. Values: `nl_query_rows`, `prebuilt_template`, `forecast`, `anomaly_detection`, `rfm_analysis`, `cohort_analysis`, `basket_analysis`, `growth_metrics`, `employee_performance`, `product_velocity`, `payment_breakdown`, `location_comparison`, `llm`. Used to break down usage by feature in the UI. |
| `llm_tokens` | `INT` | NO | Raw token count returned by the LLM API (Gemini or DeepSeek) for this call. `0` for non-LLM operations like prebuilt template runs. |
| `rows_charged` | `INT` | NO | Number of rows returned to the client for this operation. Used in the `T_db = rows/1000` component of the token formula. |
| `created_at` | `TIMESTAMP` | YES | When the operation happened. Indexed via `idx_ulog_created` for time-range queries. |

---

### `llm_usage_log`

**Defined in:** `billing.py`  
**Purpose:** LLM-specific audit trail — one row per LLM API call. Stores more granular LLM metadata (model name, endpoint called) than `usage_log`. Both tables are written to on every LLM call. `llm_usage_log` is for LLM-level debugging and cost analysis; `usage_log` is for the user-facing token ledger.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `user_email` | `VARCHAR(255)` | NO | The user whose operation triggered this LLM call. Indexed. |
| `tokens` | `INT` | NO | Raw token count returned by the LLM API — the number of tokens the LLM billed, before any DataMind rate multiplier is applied. |
| `model` | `VARCHAR(50)` | YES | Which model was used. E.g. `"gemini-2.0-flash"`, `"deepseek-chat"`. Useful for cost attribution if using multiple models. |
| `endpoint` | `VARCHAR(255)` | YES | Which DataMind endpoint triggered the LLM call. E.g. `"/v1/query"`, `"/v1/analytics/run"`. Helps diagnose which feature is consuming the most LLM tokens. |
| `credits_charged` | `DECIMAL(10,4)` | NO | The DataMind token amount charged for this LLM call, after applying `ai_credit_rate`: `(tokens / 1000) × ai_credit_rate`. |
| `created_at` | `TIMESTAMP` | YES | When the LLM call was made. |

---

### `billing_config`

**Defined in:** `billing.py`  
**Purpose:** Key-value store for system-wide billing configuration that needs to be changeable at runtime without a code deploy. Currently holds one entry: the `ai_credit_rate` multiplier. Admin can update it via `set_ai_credit_rate()`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `config_key` | `VARCHAR(100)` | NO — PRIMARY KEY | The setting name. Currently only `"ai_credit_rate"` is used. |
| `config_value` | `VARCHAR(255)` | NO | The setting value as a string. `ai_credit_rate` is read as `float`. Default: `"1.0"` — meaning 1 DataMind token per 1000 LLM tokens. Increase to make LLM ops more expensive; decrease to make them cheaper. |
| `updated_at` | `TIMESTAMP` | YES | Auto-updated whenever the value changes. |

---

### `addon_purchases`

**Defined in:** `billing.py`  
**Purpose:** Tracks purchased add-on packs. Each row represents one add-on purchase. The balance on a row decreases as the units are consumed. A user can have multiple rows of the same type — balances from all unconsumed rows are summed by `_get_addon_balance()`.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `user_email` | `VARCHAR(255)` | NO | The user who purchased the add-on. Indexed via `idx_addon_email`. |
| `addon_type` | `ENUM` | NO | What was purchased. `ai_credits` = extra tokens (50 units for $1). `db_rows` = extra row quota (100,000 rows for $1). |
| `units_remaining` | `INT` | NO | How many units are left in this purchase pack. Set to the purchased quantity on INSERT. **Note:** Add-on units are currently not being decremented — the deduction logic is tracked as a known gap. The balance correctly adds to the token limit but is not reduced as tokens are consumed. |
| `purchased_at` | `TIMESTAMP` | YES | When the purchase was made. |

**Key behaviour:**
- Add-on balance never resets with the billing period. A user who buys extra credits on day 5 of their trial still has them after they upgrade to a paid plan.
- `_get_addon_balance(user_email, addon_type)` queries `SUM(units_remaining)` across all rows with `units_remaining > 0`.

---

## Integration Tables

### `user_integrations`

**Defined in:** `integrations.py`  
**Purpose:** The integration registry. One row per connected external provider per user. This is the control record — it holds credentials, sync state, and the table prefix that namespaces all of the user's synced data in `integration_records`. A user can only have one active integration per provider (enforced by `UNIQUE KEY uq_user_provider`).

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal integration ID. Used as the key in `_sync_active` (the double-sync guard set) and as the foreign key in `sync_logs`. |
| `user_email` | `VARCHAR(255)` | NO | The user who owns this integration. Part of the unique constraint. |
| `provider_id` | `VARCHAR(50)` | NO | Which external system is connected. Values: `"salesplay"`, `"loyverse"`. Part of the unique constraint — one integration per provider per user. |
| `display_label` | `VARCHAR(100)` | YES | Optional human-readable label for this integration shown in the UI. Usually set to the provider name or the user's business name. |
| `table_prefix` | `VARCHAR(100)` | NO | A unique 16-hex-character prefix (SHA-256 derived from email + timestamp) that namespaces this user's records in `integration_records` and identifies them as `tenant_id`. Example: `"a3f9c2b1d4e87561"`. This is the same value used as `tenant_id` throughout the system. All SQL views for this user are named `{table_prefix}receipts`, `{table_prefix}products`, etc. |
| `credentials_enc` | `TEXT` | NO | The user's provider API credentials (e.g. their SalesPlay API token), encrypted with Fernet using a key derived from `ENCRYPTION_KEY`. Never stored in plaintext. Decrypted only at sync time inside `_run_sync()`. |
| `status` | `ENUM` | NO | Current state of this integration. `active` = connected and syncing normally. `syncing` = a sync is currently in progress. `paused` = user has manually paused syncing. `error` = last sync failed; `last_error` contains the message. On server startup, any rows stuck in `syncing` (from a crash) are reset to `error`. |
| `last_sync_at` | `DATETIME` | YES | When the most recent sync completed (or failed). Always updated at the end of every sync run including partial syncs and budget-limited syncs. The scheduler reads this to decide whether a sync is due. `NULL` means never synced. |
| `last_sync_rows` | `INT` | NO | Total number of records upserted during the last sync run. Used for informational display in the integrations UI ("Last sync: 1,234 rows"). |
| `last_error` | `TEXT` | YES | The error message from the most recent failed sync. Shown in the UI to help the user diagnose connection problems. `NULL` when status is `active`. |
| `created_at` | `DATETIME` | YES | When this integration was first connected. |

---

### `integration_records`

**Defined in:** `integrations.py`  
**Purpose:** The central multi-tenant data store. Every synced record from every provider for every user lives in this one table. This replaced the old architecture of per-user, per-provider physical tables (e.g. `a3f9_receipts`, `a3f9_products`). A single `(tenant_id, provider_id, record_type, external_id)` uniquely identifies any record across all users and providers.

**Engine:** `InnoDB`, `ROW_FORMAT=COMPRESSED` — approximately 30–50% disk savings on JSON-heavy rows.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `BIGINT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. `BIGINT` because this table accumulates records from all users and all providers — row counts can be in the hundreds of millions at scale. |
| `tenant_id` | `VARCHAR(64)` | NO | The user's `table_prefix` from `user_integrations`. Acts as the tenant namespace. All queries to this table always filter by `tenant_id` first, making it the most selective prefix of every index. |
| `user_email` | `VARCHAR(255)` | NO | The user who owns this record. Redundant with `tenant_id` but stored for convenience — allows lookups by email without joining `user_integrations`. |
| `provider_id` | `VARCHAR(50)` | NO | Which external system this record came from. `"salesplay"` or `"loyverse"`. Part of the unique key and all indexes. |
| `record_type` | `VARCHAR(50)` | NO | What kind of entity this is. Examples: `"receipt"`, `"product"`, `"customer"`, `"shop"`, `"category"`, `"payment_type"`, `"receipt_line_item"` (SalesPlay); `"shop"`, `"employee"`, `"category"`, `"product"`, `"variant"`, `"customer"`, `"receipt"`, `"receipt_line_item"`, `"payment_line_item"` (Loyverse). This combined with `provider_id` determines which SQL view exposes the data. |
| `external_id` | `VARCHAR(255)` | NO | The record's primary key from the external provider's API. For example, a SalesPlay receipt ID or a Loyverse product UUID. Combined with `tenant_id + provider_id + record_type`, this forms the unique key used by `ON DUPLICATE KEY UPDATE` in `upsert_record()` to perform upserts rather than blind inserts. |
| `data` | `JSON` | NO | The full normalized record as a JSON object. Each record type has a defined field structure (see SQL views). Fields are extracted using MariaDB 10.4 compatible syntax: `JSON_UNQUOTE(JSON_EXTRACT(data, '$.field'))` for strings and `CAST(JSON_EXTRACT(data, '$.field') AS DECIMAL)` for numbers. Sensitive fields (passwords, tokens) are never stored here. |
| `external_created_at` | `DATETIME` | YES | The creation timestamp from the external provider's API. Used for delta sync — on subsequent syncs, only records with `external_created_at > last_synced_at` are fetched. `NULL` for providers that don't expose a creation timestamp. |
| `external_updated_at` | `DATETIME` | YES | The last-updated timestamp from the external provider's API. Updated by `ON DUPLICATE KEY UPDATE external_updated_at = VALUES(external_updated_at)` when a record changes in the source system. Used to detect records that were modified since the last sync. |
| `synced_at` | `DATETIME(3)` | NO | When DataMind last successfully synced this record. Millisecond precision. Updated on every upsert. |
| `created_at` | `DATETIME(3)` | NO | When this row was first inserted into DataMind. Millisecond precision. Not updated on subsequent syncs. |
| `updated_at` | `DATETIME(3)` | NO | When this row was last modified by DataMind. Millisecond precision. Auto-updated via `ON UPDATE NOW(3)`. |

**Indexes:**
- `UNIQUE KEY uq_record (tenant_id, provider_id, record_type, external_id)` — enforces one row per external entity per user. Drives the `ON DUPLICATE KEY UPDATE` upsert in `upsert_record()`.
- `INDEX idx_query (tenant_id, provider_id, record_type, synced_at DESC)` — used by all SQL views, which filter by all three prefix columns. The `synced_at DESC` ordering supports "most recently synced" queries.
- `INDEX idx_user (user_email, provider_id)` — used for lookups by email when `tenant_id` is not known (e.g. in admin or billing contexts).

---

### `integration_sync_state`

**Defined in:** `integrations.py`  
**Purpose:** Tracks the sync cursor for each record type within each integration. One row per `(tenant_id, provider_id, record_type)` combination. The `last_synced_at` timestamp is the "watermark" for delta syncs — on the next sync, only records with `external_created_at > last_synced_at` are fetched from the provider API.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `tenant_id` | `VARCHAR(64)` | NO | The user's table prefix. Part of the unique key. |
| `user_email` | `VARCHAR(255)` | NO | The user who owns this sync state. |
| `provider_id` | `VARCHAR(50)` | NO | Which provider this state row tracks. Part of the unique key. |
| `record_type` | `VARCHAR(50)` | NO | Which entity type this state row tracks (e.g. `"receipt"`, `"product"`). Part of the unique key. Reference types (shops, categories, products, payment_types) always do full syncs so their `last_synced_at` is used differently. |
| `last_synced_at` | `DATETIME(3)` | YES | The timestamp of the most recent successful sync for this record type. On the next sync, the provider's API is called with `since=last_synced_at`. `NULL` on first sync, which triggers a full pull from the beginning of the plan's history window. |
| `status` | `ENUM` | NO | Current sync status for this record type. `ok` = last sync succeeded. `error` = last sync failed (see `error_message`). `syncing` = currently in progress. |
| `error_message` | `TEXT` | YES | Error detail if `status='error'`. `NULL` when healthy. |
| `updated_at` | `DATETIME(3)` | NO | Auto-updated whenever this row changes. Millisecond precision. |

**Key behaviour:**
- When a sync completes successfully, `last_synced_at` is set to the timestamp of the last record fetched. The next sync uses this as the `since` parameter to the external API.
- Reference tables (shops, categories, products, payment_types) are always fully re-synced regardless of `last_synced_at` — their cursor is updated but not used to filter.
- There is one row per `(tenant_id, provider_id, record_type)` — a user with SalesPlay connected has 7 rows (one per SalesPlay record type); with Loyverse connected, 9 rows.

---

### `sync_logs`

**Defined in:** `integrations.py`  
**Purpose:** Append-only history of every sync run attempted for every integration. One row per sync attempt (both successful and failed). Used for debugging, audit, and potentially future UI features showing sync history.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `id` | `INT AUTO_INCREMENT` | NO — PRIMARY KEY | Internal row ID. |
| `integration_id` | `INT` | NO | Which integration this sync was for. Foreign key to `user_integrations.id`. Indexed via `idx_integration`. |
| `sync_type` | `ENUM` | YES | `full` = first sync or manual full re-sync (fetches all history). `delta` = incremental sync (fetches only records changed since `last_synced_at`). |
| `started_at` | `DATETIME` | YES | When the sync thread began executing. |
| `finished_at` | `DATETIME` | YES | When the sync thread finished (success or failure). `NULL` if still running. |
| `rows_fetched` | `INT` | NO | How many records were fetched from the external provider API. |
| `rows_inserted` | `INT` | NO | How many new records were inserted into `integration_records`. |
| `rows_updated` | `INT` | NO | How many existing records were updated (i.e. ON DUPLICATE KEY UPDATE was triggered). |
| `status` | `ENUM` | NO | `running` = sync is in progress. `success` = completed without errors. `error` = completed with errors (see `error_message`). |
| `error_message` | `TEXT` | YES | The error detail if `status='error'`. `NULL` on success. |

---

## Embed / Partner Tables

### `embed_partners`

**Defined in:** `embed.py`  
**Purpose:** Registry of third-party platforms that have been granted permission to embed DataMind as an iframe widget. Each row represents one partner integration (e.g. SalesPlay's web portal). There is one row per partner, not per user — it's a platform-level configuration table managed by DataMind admins, not by users.

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `partner_key` | `VARCHAR(64)` | NO — PRIMARY KEY | The public identifier for this partner. Embedded in the partner's `<script>` tag as a query parameter (`?pk=...`). Not a secret — it is visible to anyone viewing the partner's page source. Security relies on `allowed_origins`, not on keeping this key secret. |
| `partner_name` | `VARCHAR(128)` | NO | Human-readable name for this partner. E.g. `"SalesPlay"`. Displayed in the iframe's onboarding screen and logged in access logs. |
| `provider_id` | `VARCHAR(50)` | NO | Which DataMind provider (data source) this partner's users will connect. E.g. `"salesplay"` or `"loyverse"`. Used by `embed_init()` to call `connect_provider(provider_id=...)` automatically during onboarding — the user never has to choose a provider in the embed flow. |
| `allowed_origins` | `TEXT` | NO | Comma-separated list of domains allowed to host this embed. E.g. `"https://app.salesplay.io,https://backoffice.salesplay.io"`. Returned to the frontend on `GET /embed/context` and used for `postMessage` origin validation in `EmbedApp.jsx`. Requests from unlisted origins are rejected at the JS layer. |
| `branding` | `JSON` | YES | Optional customisation for the embedded widget's appearance. JSON object — can contain `primary_color`, `logo_url`, `app_name`, etc. Passed to the React frontend and applied to the iframe's theme. `NULL` means use DataMind's default branding. |
| `active` | `TINYINT(1)` | YES | `1` = this partner is live and can receive embed traffic. `0` = deactivated — `GET /embed/context` returns 404 for this partner key, blocking all embed usage instantly without deleting the row. |
| `created_at` | `DATETIME` | YES | When this partner was registered. |

---

## Virtual Tables (SQL Views)

These are **not physical tables** — they are MySQL/MariaDB views that sit on top of `integration_records`. They are created once per user integration at connect time by `_create_views_for_integration()` in `integrations.py`. They are named identically to the old per-user physical tables so all analytics SQL works unchanged.

Views are prefixed with the user's `table_prefix` from `user_integrations`. For a user with prefix `a3f9c2b1d4e87561`, the receipts view is named `a3f9c2b1d4e87561receipts` (SalesPlay) or `a3f9c2b1d4e87561_receipts` (Loyverse — note the underscore).

All views use **MariaDB 10.4 compatible JSON extraction**:
- Strings: `JSON_UNQUOTE(JSON_EXTRACT(data, '$.field'))`
- Numbers: `CAST(JSON_EXTRACT(data, '$.field') AS DECIMAL(12,4))`

The newer `->>'$.field'` shorthand is intentionally not used because it requires MariaDB 10.5+.

---

### SalesPlay Views

Seven views created per SalesPlay integration.

#### `{prefix}shops`
Physical store locations belonging to the SalesPlay account. Every receipt is linked to a shop via `shop_id`.

| Exposed column | Source in `data` JSON | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay shop ID |
| `shop_name` | `$.shop_name` | Display name of the shop |
| `address` | `$.address` | Physical street address |
| `phone` | `$.phone` | Shop phone number |
| `email` | `$.email` | Shop contact email |
| `status` | `$.status` | Whether the shop is active in SalesPlay |
| `updated_at` | `$.updated_at` | Last update timestamp from SalesPlay |
| `synced_at` | (column) | When DataMind last synced this record |

#### `{prefix}categories`
Product categories used to group products. Receipts and products reference categories by `category_id`.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay category ID |
| `category_name` | `$.category_name` | Display name |
| `created_at` | `$.created_at` | When created in SalesPlay |
| `updated_at` | `$.updated_at` | Last updated in SalesPlay |
| `synced_at` | (column) | Last DataMind sync timestamp |

#### `{prefix}payment_types`
Available payment methods configured in SalesPlay (Cash, Card, etc.). Receipts reference payment types.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay payment type ID |
| `payment_name` | `$.payment_name` | Display name (e.g. "Cash", "Visa") |
| `created_at` | `$.created_at` | When created |
| `updated_at` | `$.updated_at` | Last updated |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}products`
Product catalogue. Used in analytics for product velocity, basket analysis, and revenue breakdown.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay product ID |
| `product_name` | `$.product_name` | Product display name |
| `category_id` | `$.category_id` | Links to `{prefix}categories.id` |
| `sku` | `$.sku` | Stock keeping unit code |
| `price` | `$.price` | Selling price (DECIMAL 12,4) |
| `cost` | `$.cost` | Cost price — used for margin analytics (DECIMAL 12,4) |
| `created_at` | `$.created_at` | When added to SalesPlay |
| `updated_at` | `$.updated_at` | Last updated |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}customers`
Customer profiles with loyalty programme data. Used for RFM analysis, cohort analysis, and customer segmentation.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay customer ID |
| `customer_name` | `$.customer_name` | Full name |
| `email` | `$.email` | Customer email (may be empty) |
| `phone_number` | `$.phone_number` | Customer phone |
| `total_spent` | `$.total_spent` | Lifetime spend (DECIMAL 14,4) |
| `total_visits` | `$.total_visits` | Number of transactions (UNSIGNED) |
| `points_balance` | `$.points_balance` | Current loyalty points (DECIMAL 12,2) |
| `created_at` | `$.created_at` | When the customer first registered |
| `updated_at` | `$.updated_at` | Last updated |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}receipts`
Transaction records — the most analytically important table. Every sale is a receipt. Used in revenue analysis, growth metrics, forecasting, anomaly detection, and most other analytics.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | SalesPlay receipt ID |
| `receipt_number` | `$.receipt_number` | Human-readable receipt number |
| `shop_id` | `$.shop_id` | Which shop processed this sale |
| `shop_name` | `$.shop_name` | Denormalised shop name (enriched at sync time via `lookup_map()`) |
| `customer_id` | `$.customer_id` | Which customer (NULL for walk-in) |
| `customer_name` | `$.customer_name` | Denormalised customer name (enriched at sync) |
| `created_at` | `external_created_at` | Transaction timestamp — pulled from `external_created_at` column, not `data` JSON, for index efficiency |
| `updated_at` | `$.updated_at` | Last modified |
| `total_money` | `$.total_money` | Total sale amount (DECIMAL 14,4) |
| `total_discount` | `$.total_discount` | Total discount applied (DECIMAL 14,4) |
| `total_tax` | `$.total_tax` | Tax collected (DECIMAL 14,4) |
| `receipt_type` | `$.receipt_type` | Sale type (e.g. "sale", "refund") |
| `status` | `$.status` | Receipt status |
| `payment_type_id` | `$.payment_type_id` | Links to `{prefix}payment_types.id` |
| `payment_type_name` | `$.payment_type_name` | Denormalised payment method name |
| `payment_amount` | `$.payment_amount` | Amount paid (DECIMAL 14,4) |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}receipt_line_items`
Individual line items within a receipt. Each receipt has one or more line items, one per product. Used for basket analysis, product velocity, and margin analytics.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Line item ID |
| `receipt_id` | `$.receipt_id` | Links to `{prefix}receipts.id` |
| `product_id` | `$.product_id` | Links to `{prefix}products.id` |
| `variant_id` | `$.variant_id` | Product variant (if applicable) |
| `product_name` | `$.product_name` | Denormalised product name |
| `sku` | `$.sku` | Product SKU at time of sale |
| `quantity` | `$.quantity` | Units sold (DECIMAL 12,4) |
| `price` | `$.price` | Unit selling price (DECIMAL 12,4) |
| `gross_total_money` | `$.gross_total_money` | Gross line total before discounts (DECIMAL 14,4) |
| `total_discount` | `$.total_discount` | Discount on this line (DECIMAL 14,4) |
| `total_money` | `$.total_money` | Net line total after discounts (DECIMAL 14,4) |
| `cost` | `$.cost` | Unit cost at time of sale — used for margin (DECIMAL 12,4) |
| `created_at` | `$.created_at` | When this line item was created |
| `synced_at` | (column) | Last DataMind sync |

---

### Loyverse Views

Nine views created per Loyverse integration.

#### `{prefix}_stores`
Physical store locations. Equivalent to SalesPlay's `shops`.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Loyverse store UUID |
| `name` | `$.name` | Store display name |
| `address` | `$.address` | Physical address |
| `phone_number` | `$.phone_number` | Store phone |
| `description` | `$.description` | Optional store description |
| `created_at` | `$.created_at` | When created in Loyverse |
| `updated_at` | `$.updated_at` | Last modified |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_employees`
Staff members linked to stores. Used for cashier performance analytics.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Employee UUID |
| `name` | `$.name` | Full name |
| `email` | `$.email` | Employee email |
| `role` | `$.role` | Role in Loyverse (e.g. "cashier", "manager") |
| `store_id` | `$.store_id` | Primary store assignment |
| `created_at` | `$.created_at` | When added |
| `updated_at` | `$.updated_at` | Last modified |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_categories`
Product categories used in Loyverse.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Category UUID |
| `name` | `$.name` | Category display name |
| `color` | `$.color` | Category colour (hex code) — used in Loyverse UI |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_products`
Top-level product items. Loyverse uses a two-level structure: a product (`item`) has one or more variants. Price and cost live on the variant, not the product.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Product (item) UUID |
| `handle` | `$.handle` | URL-safe identifier |
| `item_name` | `$.item_name` | Product display name |
| `description` | `$.description` | Product description |
| `category_id` | `$.category_id` | Links to `{prefix}_categories.id` |
| `track_stock` | `$.track_stock` | Whether inventory tracking is enabled (UNSIGNED) |
| `created_at` | `$.created_at` | When created |
| `updated_at` | `$.updated_at` | Last modified |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_variants`
Product variants (sizes, colours, etc.). Loyverse pricing lives here, not on the parent product.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Variant UUID |
| `item_id` | `$.item_id` | Links to `{prefix}_products.id` |
| `sku` | `$.sku` | Stock keeping unit |
| `barcode` | `$.barcode` | Barcode string |
| `cost` | `$.cost` | Unit cost price (DECIMAL 12,4) |
| `default_price` | `$.default_price` | Default selling price (DECIMAL 12,4) |
| `stores_json` | `$.stores_json` | JSON array of per-store pricing and stock levels |
| `created_at` | `$.created_at` | When created |
| `updated_at` | `$.updated_at` | Last modified |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_customers`
Customer profiles with loyalty data. Used for the same analytics as SalesPlay customers.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Customer UUID |
| `name` | `$.name` | Full name |
| `email` | `$.email` | Customer email |
| `phone_number` | `$.phone_number` | Customer phone |
| `total_visits` | `$.total_visits` | Total transaction count (UNSIGNED) |
| `total_spent` | `$.total_spent` | Lifetime spend (DECIMAL 14,4) |
| `points_balance` | `$.points_balance` | Current loyalty points (DECIMAL 12,2) |
| `created_at` | `$.created_at` | First registered |
| `updated_at` | `$.updated_at` | Last modified |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_receipts`
Transaction records. Loyverse equivalent of SalesPlay receipts.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Receipt UUID |
| `receipt_number` | `$.receipt_number` | Human-readable number |
| `store_id` | `$.store_id` | Which store processed the sale |
| `store_name` | `$.store_name` | Denormalised store name |
| `employee_id` | `$.employee_id` | Cashier who processed the sale |
| `employee_name` | `$.employee_name` | Denormalised cashier name |
| `customer_id` | `$.customer_id` | Which customer (NULL for walk-in) |
| `customer_name` | `$.customer_name` | Denormalised customer name |
| `created_at` | `external_created_at` | Transaction timestamp (from column, not JSON) |
| `updated_at` | `$.updated_at` | Last modified |
| `total_money` | `$.total_money` | Total sale amount (DECIMAL 14,4) |
| `total_discount` | `$.total_discount` | Total discount (DECIMAL 14,4) |
| `total_tax` | `$.total_tax` | Total tax (DECIMAL 14,4) |
| `receipt_type` | `$.receipt_type` | "sale" or "refund" |
| `cancelled_at` | `$.cancelled_at` | If voided, when it was cancelled |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_receipt_line_items`
Individual line items per Loyverse receipt.

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Line item UUID |
| `receipt_id` | `$.receipt_id` | Links to `{prefix}_receipts.id` |
| `item_id` | `$.item_id` | Links to `{prefix}_products.id` |
| `variant_id` | `$.variant_id` | Links to `{prefix}_variants.id` |
| `item_name` | `$.item_name` | Denormalised product name |
| `sku` | `$.sku` | SKU at time of sale |
| `quantity` | `$.quantity` | Units sold (DECIMAL 12,4) |
| `price` | `$.price` | Unit price (DECIMAL 12,4) |
| `gross_total_money` | `$.gross_total_money` | Before discounts (DECIMAL 14,4) |
| `total_discount` | `$.total_discount` | Discount on this line (DECIMAL 14,4) |
| `total_money` | `$.total_money` | Net line total (DECIMAL 14,4) |
| `cost` | `$.cost` | Unit cost at time of sale (DECIMAL 12,4) |
| `synced_at` | (column) | Last DataMind sync |

#### `{prefix}_payment_line_items`
Individual payment entries per receipt. A receipt can have multiple payment entries (split payments — e.g. part cash, part card).

| Exposed column | Source | Description |
|---|---|---|
| `id` | `$.id` | Payment line UUID |
| `receipt_id` | `$.receipt_id` | Links to `{prefix}_receipts.id` |
| `payment_type_id` | `$.payment_type_id` | Payment method used |
| `payment_type_name` | `$.payment_type_name` | Denormalised method name |
| `amount` | `$.amount` | Amount paid via this method (DECIMAL 14,4) |
| `synced_at` | (column) | Last DataMind sync |

---

## Relationships Diagram

```
users
  │  email (PK)
  │
  ├─── user_subscriptions
  │       user_email → users.email
  │       plan_id    → subscription_plans.id
  │
  ├─── subscription_usage
  │       user_email + period_start (composite unique)
  │
  ├─── usage_log
  │       user_email
  │
  ├─── llm_usage_log
  │       user_email
  │
  ├─── addon_purchases
  │       user_email
  │
  └─── user_integrations
            user_email + provider_id (composite unique)
            id (PK)
              │
              ├─── sync_logs
              │       integration_id → user_integrations.id
              │
              └─── integration_records  (via tenant_id = table_prefix)
                       tenant_id = user_integrations.table_prefix
                         │
                         └─── integration_sync_state
                                  tenant_id + provider_id + record_type (composite unique)

embed_partners (standalone — no FK to users; users are linked at onboarding time via embed_init)
```

---

## Index Reference

| Table | Index name | Columns | Purpose |
|-------|-----------|---------|---------|
| `users` | PRIMARY | `email` | Direct user lookup by email |
| `user_subscriptions` | `idx_sub_email` | `user_email` | All subscriptions for a user |
| `subscription_usage` | `uq_usage` (UNIQUE) | `user_email, period_start` | One usage row per period per user; drives ON DUPLICATE KEY UPDATE |
| `usage_log` | `idx_ulog_email` | `user_email` | History per user |
| `usage_log` | `idx_ulog_created` | `created_at` | Time-range queries |
| `llm_usage_log` | `idx_llm_email` | `user_email` | LLM history per user |
| `llm_usage_log` | `idx_llm_created` | `created_at` | Time-range queries |
| `addon_purchases` | `idx_addon_email` | `user_email` | Add-on balance lookups |
| `user_integrations` | `uq_user_provider` (UNIQUE) | `user_email, provider_id` | One integration per provider per user |
| `integration_records` | PRIMARY | `id` | Row access |
| `integration_records` | `uq_record` (UNIQUE) | `tenant_id, provider_id, record_type, external_id` | Upsert deduplication |
| `integration_records` | `idx_query` | `tenant_id, provider_id, record_type, synced_at DESC` | All SQL view queries |
| `integration_records` | `idx_user` | `user_email, provider_id` | Email-based lookups |
| `integration_sync_state` | `uq_state` (UNIQUE) | `tenant_id, provider_id, record_type` | One state row per type per integration |
| `sync_logs` | `idx_integration` | `integration_id` | All sync logs for an integration |
| `embed_partners` | PRIMARY | `partner_key` | Direct lookup by partner key |
