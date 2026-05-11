-- SalesPlay POS — DataMind table schema
-- All tables are prefixed with {prefix} (e.g. sp_user42_)
-- Run via get_schema_sql() which substitutes {prefix}.

DROP TABLE IF EXISTS `{prefix}shops`;
CREATE TABLE IF NOT EXISTS `{prefix}shops` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    shop_name       VARCHAR(255),
    address         VARCHAR(500),
    phone           VARCHAR(50),
    email           VARCHAR(255),
    currency        VARCHAR(10),
    country         VARCHAR(10),
    timezone        VARCHAR(100),
    status          VARCHAR(20),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}categories`;
CREATE TABLE IF NOT EXISTS `{prefix}categories` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    category_name   VARCHAR(255),
    color           VARCHAR(20),
    shop_id         VARCHAR(64),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}sub_categories`;
CREATE TABLE IF NOT EXISTS `{prefix}sub_categories` (
    id                  VARCHAR(64)     NOT NULL PRIMARY KEY,
    sub_category_name   VARCHAR(255),
    category_id         VARCHAR(64),
    category_name       VARCHAR(255),
    shop_id             VARCHAR(64),
    created_at          DATETIME,
    updated_at          DATETIME,
    synced_at           DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}measurements`;
CREATE TABLE IF NOT EXISTS `{prefix}measurements` (
    id                  VARCHAR(64)     NOT NULL PRIMARY KEY,
    measurement_name    VARCHAR(255),
    weight_scale_enable TINYINT(1)      DEFAULT 0,
    created_at          DATETIME,
    updated_at          DATETIME,
    synced_at           DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}suppliers`;
CREATE TABLE IF NOT EXISTS `{prefix}suppliers` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    supplier_name   VARCHAR(255),
    phone_number    VARCHAR(50),
    email           VARCHAR(255),
    address         TEXT,
    description     TEXT,
    status          VARCHAR(20),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}taxes`;
CREATE TABLE IF NOT EXISTS `{prefix}taxes` (
    id                  VARCHAR(64)     NOT NULL PRIMARY KEY,
    tax_name            VARCHAR(255),
    tax_type            VARCHAR(50),    -- TAX | CHARGE
    calculation_method  VARCHAR(50),    -- ADDED | INCLUDED
    rate                DECIMAL(10,4),
    apply_after_taxes   TINYINT(1)      DEFAULT 0,
    created_at          DATETIME,
    updated_at          DATETIME,
    synced_at           DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}payment_types`;
CREATE TABLE IF NOT EXISTS `{prefix}payment_types` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    payment_name    VARCHAR(255),
    payment_type    VARCHAR(50),
    is_active       TINYINT(1)      DEFAULT 1,
    shop_id         VARCHAR(64),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}products`;
CREATE TABLE IF NOT EXISTS `{prefix}products` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    product_name    VARCHAR(500),
    description     TEXT,
    category_id     VARCHAR(64),
    category_name   VARCHAR(255),
    sub_category_id VARCHAR(64),
    sub_category_name VARCHAR(255),
    measurement_id  VARCHAR(64),
    measurement_name VARCHAR(255),
    reference_id    VARCHAR(100),
    sold_by_weight  TINYINT(1)      DEFAULT 0,
    is_active       TINYINT(1)      DEFAULT 1,
    primary_supplier_id VARCHAR(64),
    track_stock     TINYINT(1)      DEFAULT 0,
    -- First variant flattened for convenience
    variant_id      VARCHAR(64),
    sku             VARCHAR(100),
    barcode         VARCHAR(100),
    cost            DECIMAL(12,4),
    price           DECIMAL(12,4),
    purchase_cost   DECIMAL(12,4),
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}customers`;
CREATE TABLE IF NOT EXISTS `{prefix}customers` (
    id              VARCHAR(64)     NOT NULL PRIMARY KEY,
    customer_name   VARCHAR(255),
    email           VARCHAR(255),
    phone_number    VARCHAR(50),
    customer_code   VARCHAR(100),
    note            TEXT,
    total_visits    INT             DEFAULT 0,
    total_spent     DECIMAL(14,4)   DEFAULT 0,
    points_balance  DECIMAL(12,2)   DEFAULT 0,
    created_at      DATETIME,
    updated_at      DATETIME,
    synced_at       DATETIME        DEFAULT NOW()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}receipts`;
CREATE TABLE IF NOT EXISTS `{prefix}receipts` (
    id                  VARCHAR(64)     NOT NULL PRIMARY KEY,
    receipt_number      VARCHAR(100),
    shop_id             VARCHAR(64),
    shop_name           VARCHAR(255),
    customer_id         VARCHAR(64),
    customer_name       VARCHAR(255),
    created_at          DATETIME,
    updated_at          DATETIME,
    total_money         DECIMAL(14,4),
    total_discount      DECIMAL(14,4)   DEFAULT 0,
    total_tax           DECIMAL(14,4)   DEFAULT 0,
    points_earned       DECIMAL(12,2)   DEFAULT 0,
    points_deducted     DECIMAL(12,2)   DEFAULT 0,
    note                TEXT,
    receipt_type        VARCHAR(30),    -- SALE | REFUND
    status              VARCHAR(30),
    -- Payment flattened (first payment only for simplicity)
    payment_type_id     VARCHAR(64),
    payment_type_name   VARCHAR(255),
    payment_amount      DECIMAL(14,4),
    synced_at           DATETIME        DEFAULT NOW(),
    INDEX idx_shop       (shop_id),
    INDEX idx_customer   (customer_id),
    INDEX idx_created    (created_at),
    INDEX idx_type       (receipt_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


DROP TABLE IF EXISTS `{prefix}receipt_line_items`;
CREATE TABLE IF NOT EXISTS `{prefix}receipt_line_items` (
    id                  VARCHAR(64)     NOT NULL PRIMARY KEY,
    receipt_id          VARCHAR(64)     NOT NULL,
    product_id          VARCHAR(64),
    variant_id          VARCHAR(64),
    product_name        VARCHAR(500),
    sku                 VARCHAR(100),
    quantity            DECIMAL(12,4),
    price               DECIMAL(12,4),
    gross_total_money   DECIMAL(14,4),
    total_discount      DECIMAL(14,4)   DEFAULT 0,
    total_money         DECIMAL(14,4),
    cost                DECIMAL(12,4),
    created_at          DATETIME,
    synced_at           DATETIME        DEFAULT NOW(),
    INDEX idx_receipt    (receipt_id),
    INDEX idx_product    (product_id),
    INDEX idx_created    (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


