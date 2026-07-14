-- report_cache foundations: fact tables + tenant profile tables.
-- See docs/plan/PLAN_01_Foundations_DataModel_And_ReportClient.md Step 2.
-- Idempotent (IF NOT EXISTS) — safe to re-run.

CREATE TABLE IF NOT EXISTS report_daily_fact (
  tenant_id     VARCHAR(64)  NOT NULL,
  report_id     VARCHAR(64)  NOT NULL,
  shop_id       VARCHAR(64)  NOT NULL DEFAULT 'all',
  business_date DATE         NOT NULL,
  metrics       JSON         NOT NULL,
  status        ENUM('open','closed','finalized') NOT NULL DEFAULT 'open',
  fetched_at    DATETIME     NOT NULL,
  PRIMARY KEY (tenant_id, report_id, shop_id, business_date),
  KEY idx_daily_lookup (tenant_id, report_id, business_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS report_dim_fact (
  tenant_id     VARCHAR(64)  NOT NULL,
  report_id     VARCHAR(64)  NOT NULL,
  shop_id       VARCHAR(64)  NOT NULL DEFAULT 'all',
  period_month  DATE         NOT NULL,          -- first day of month
  dim_type      VARCHAR(32)  NOT NULL,          -- 'product' | 'category'
  dim_key       VARCHAR(128) NOT NULL,
  dim_name      VARCHAR(255),
  metrics       JSON         NOT NULL,
  status        ENUM('open','closed','finalized') NOT NULL DEFAULT 'open',
  fetched_at    DATETIME     NOT NULL,
  PRIMARY KEY (tenant_id, report_id, shop_id, period_month, dim_type, dim_key),
  KEY idx_dim_lookup (tenant_id, report_id, period_month)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS report_sync_state (
  tenant_id  VARCHAR(64) NOT NULL,
  report_id  VARCHAR(64) NOT NULL,
  shop_id    VARCHAR(64) NOT NULL DEFAULT 'all',
  period     DATE        NOT NULL,
  grain      ENUM('day','month') NOT NULL,
  status     ENUM('pending','open','closed','finalized','error') NOT NULL DEFAULT 'pending',
  fetched_at DATETIME,
  attempts   INT NOT NULL DEFAULT 0,
  last_error VARCHAR(512),
  PRIMARY KEY (tenant_id, report_id, shop_id, period, grain)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tenant_profile (
  tenant_id      VARCHAR(64) NOT NULL,
  master_username VARCHAR(128),
  currency       VARCHAR(8),
  currency_symbol VARCHAR(8),
  number_format  JSON,
  ui_language    VARCHAR(8),
  timezone       VARCHAR(64),
  subscription_tier ENUM('basic','standard','unlimited') DEFAULT 'basic', -- AI plan, from DataMind billing (NOT the POS profile) — see PLAN_02
  history_months INT DEFAULT 3,             -- derived from tier: 3 / 12 / NULL(unlimited)
  profile_json   JSON,                      -- raw profile payload
  refreshed_at   DATETIME,
  PRIMARY KEY (tenant_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tenant_shop (
  tenant_id VARCHAR(64) NOT NULL,
  shop_id   VARCHAR(64) NOT NULL,
  shop_name VARCHAR(255),
  PRIMARY KEY (tenant_id, shop_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tenant_cashier (
  tenant_id   VARCHAR(64) NOT NULL,
  cashier_id  VARCHAR(64) NOT NULL,
  cashier_name VARCHAR(255),
  PRIMARY KEY (tenant_id, cashier_id)
) ENGINE=InnoDB;
