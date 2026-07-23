-- Report cache step 2: tenant profile, shops, cashiers (+ session-token stash).
-- Synced from the SalesPlay /app/profile API at widget onboarding (live `aat`).
--
-- NOTE: this feature has never shipped; the DROPs only clear leftovers of an
-- abandoned dev-branch schema from local dev databases. Safe to re-run.

DROP TABLE IF EXISTS tenant_profile;
DROP TABLE IF EXISTS tenant_shop;
DROP TABLE IF EXISTS tenant_cashier;

CREATE TABLE tenant_profile (
    tenant_id         VARCHAR(100) NOT NULL PRIMARY KEY,  -- user_integrations.table_prefix
    currency          VARCHAR(20)  NULL,                  -- display symbol, e.g. 'Rs.', '$' (no ISO code in the API)
    ui_language       VARCHAR(10)  NULL,
    timezone          VARCHAR(64)  NULL,
    number_format     TEXT         NULL,                  -- raw user.number_format JSON
    date_format       VARCHAR(32)  NULL,
    time_format       VARCHAR(32)  NULL,
    -- Encrypted short-lived app_access_token from the latest widget session.
    -- Refreshed on every widget open; lets chat-time report fetches reuse the
    -- live session's auth (stored integration tokens do NOT work on /app/*).
    session_token_enc TEXT         NULL,
    session_token_at  DATETIME     NULL,
    synced_at         DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tenant_shop (
    tenant_id  VARCHAR(100) NOT NULL,
    shop_id    VARCHAR(64)  NOT NULL,   -- profile shop_list[].location_id (report APIs' shop_id param)
    shop_name  VARCHAR(255) NOT NULL,
    is_enabled TINYINT(1)   NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, shop_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE tenant_cashier (
    tenant_id    VARCHAR(100) NOT NULL,
    user_name    VARCHAR(150) NOT NULL,  -- cashier_list[].user_name (unique per tenant in the source query)
    cashier_id   VARCHAR(64)  NULL,      -- cashier_list[].user_id (stable id, informational)
    -- cashier_list[].name — the report APIs' `cashier_id` filter actually matches
    -- invoice_cashier_name (a display name), so THIS is the value to send.
    cashier_name VARCHAR(255) NOT NULL,
    PRIMARY KEY (tenant_id, user_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
