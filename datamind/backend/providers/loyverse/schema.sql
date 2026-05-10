-- Loyverse POS Schema
-- Prefix: {prefix} (substituted at runtime, e.g. dm_a1b2c3_loyverse)
-- All tables use IF NOT EXISTS — safe to run multiple times.

CREATE TABLE IF NOT EXISTS `{prefix}_stores` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    address         TEXT,
    phone_number    VARCHAR(50),
    description     TEXT,
    default_tax_id  VARCHAR(36),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_employees` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    email           VARCHAR(255),
    phone_number    VARCHAR(50),
    role            VARCHAR(50),
    store_id        VARCHAR(36),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_categories` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    color           VARCHAR(20),
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_products` (
    id              VARCHAR(36) PRIMARY KEY,
    handle          VARCHAR(255),
    item_name       VARCHAR(255),
    description     TEXT,
    category_id     VARCHAR(36),
    track_stock     TINYINT(1) DEFAULT 0,
    sold_by_weight  TINYINT(1) DEFAULT 0,
    is_composite    TINYINT(1) DEFAULT 0,
    primary_supplier_id VARCHAR(36),
    created_at      DATETIME,
    updated_at      DATETIME,
    deleted_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_variants` (
    id              VARCHAR(36) PRIMARY KEY,
    item_id         VARCHAR(36),
    sku             VARCHAR(100),
    reference_variant_id VARCHAR(36),
    option1_value   VARCHAR(100),
    option2_value   VARCHAR(100),
    option3_value   VARCHAR(100),
    barcode         VARCHAR(100),
    cost            DECIMAL(15,4) DEFAULT 0,
    default_price   DECIMAL(15,4) DEFAULT 0,
    purchase_cost   DECIMAL(15,4) DEFAULT 0,
    default_pricing_type VARCHAR(20),
    stores_json     TEXT,
    created_at      DATETIME,
    updated_at      DATETIME,
    deleted_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_customers` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    email           VARCHAR(255),
    phone_number    VARCHAR(50),
    address         TEXT,
    city            VARCHAR(100),
    region          VARCHAR(100),
    postal_code     VARCHAR(20),
    country_code    VARCHAR(10),
    customer_code   VARCHAR(100),
    note            TEXT,
    total_visits    INT DEFAULT 0,
    total_spent     DECIMAL(15,4) DEFAULT 0,
    points_balance  DECIMAL(15,4) DEFAULT 0,
    created_at      DATETIME,
    updated_at      DATETIME,
    deleted_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_receipts` (
    receipt_number  VARCHAR(36) PRIMARY KEY,
    receipt_date    DATETIME,
    order_type      VARCHAR(50),
    store_id        VARCHAR(36),
    cashier_id      VARCHAR(36),
    customer_id     VARCHAR(36),
    customer_name   VARCHAR(255),
    total_money     DECIMAL(15,4) DEFAULT 0,
    points_earned   DECIMAL(15,4) DEFAULT 0,
    points_deducted DECIMAL(15,4) DEFAULT 0,
    points_balance  DECIMAL(15,4) DEFAULT 0,
    note            TEXT,
    receipt_type    VARCHAR(20),
    refund_for      VARCHAR(36),
    total_discounts DECIMAL(15,4) DEFAULT 0,
    total_tax       DECIMAL(15,4) DEFAULT 0,
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW(),
    INDEX idx_receipt_date (receipt_date),
    INDEX idx_store (store_id),
    INDEX idx_customer (customer_id),
    INDEX idx_cashier (cashier_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_receipt_line_items` (
    id              VARCHAR(36) PRIMARY KEY,
    receipt_number  VARCHAR(36),
    item_id         VARCHAR(36),
    variant_id      VARCHAR(36),
    item_name       VARCHAR(255),
    variant_name    VARCHAR(255),
    sku             VARCHAR(100),
    quantity        DECIMAL(15,4) DEFAULT 0,
    price           DECIMAL(15,4) DEFAULT 0,
    gross_total_money DECIMAL(15,4) DEFAULT 0,
    discount        DECIMAL(15,4) DEFAULT 0,
    total_money     DECIMAL(15,4) DEFAULT 0,
    cost            DECIMAL(15,4) DEFAULT 0,
    category_id     VARCHAR(36),
    line_note       TEXT,
    synced_at       DATETIME DEFAULT NOW(),
    INDEX idx_receipt (receipt_number),
    INDEX idx_item (item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_payment_line_items` (
    id              VARCHAR(36) PRIMARY KEY,
    receipt_number  VARCHAR(36),
    payment_type_id VARCHAR(36),
    payment_type_name VARCHAR(100),
    money_amount    DECIMAL(15,4) DEFAULT 0,
    synced_at       DATETIME DEFAULT NOW(),
    INDEX idx_receipt (receipt_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_inventory_levels` (
    id              VARCHAR(100) PRIMARY KEY,
    variant_id      VARCHAR(36),
    store_id        VARCHAR(36),
    in_stock        DECIMAL(15,4) DEFAULT 0,
    updated_at      DATETIME,
    synced_at       DATETIME DEFAULT NOW(),
    INDEX idx_variant (variant_id),
    INDEX idx_store (store_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_taxes` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    rate            DECIMAL(8,4) DEFAULT 0,
    type            VARCHAR(20),
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `{prefix}_discounts` (
    id              VARCHAR(36) PRIMARY KEY,
    name            VARCHAR(255),
    type            VARCHAR(20),
    value           DECIMAL(15,4) DEFAULT 0,
    synced_at       DATETIME DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
