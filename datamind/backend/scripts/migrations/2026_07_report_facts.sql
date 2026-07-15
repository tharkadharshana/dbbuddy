-- Report cache step 3: cached report summaries.
-- Three tables cover all 35 logical reports (scalar + dimensional shapes),
-- instead of one table per report.
--
-- NOTE: the DROPs only clear leftovers of an abandoned dev-branch schema from
-- local dev databases — this feature has never shipped. Safe to re-run.

DROP TABLE IF EXISTS report_daily_fact;
DROP TABLE IF EXISTS report_dim_fact;
DROP TABLE IF EXISTS report_sync_state;
DROP TABLE IF EXISTS report_cache_job;
DROP TABLE IF EXISTS report_cache_state;
DROP TABLE IF EXISTS report_fact;

-- Scalar report facts. grain='day' rows come from sales_summary's table_data
-- (already GROUP BY DATE server-side); grain='month' rows are a report's
-- data.summary block fetched for exactly one calendar month.
CREATE TABLE report_fact (
    tenant_id    VARCHAR(100) NOT NULL,
    report_id    VARCHAR(50)  NOT NULL,     -- registry key, e.g. 'sales_summary'
    shop_id      VARCHAR(64)  NOT NULL DEFAULT 'all',
    grain        ENUM('day','month') NOT NULL,
    period_start DATE         NOT NULL,     -- the day, or the 1st of the month
    metrics      TEXT         NOT NULL,     -- JSON {metric_key: number|null}
    fetched_at   DATETIME     NOT NULL,
    PRIMARY KEY (tenant_id, report_id, shop_id, grain, period_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Dimensional report facts (sales_by_products / sales_by_category / ...):
-- one row per dimension value per calendar month.
CREATE TABLE report_dim_fact (
    tenant_id  VARCHAR(100) NOT NULL,
    report_id  VARCHAR(50)  NOT NULL,
    shop_id    VARCHAR(64)  NOT NULL DEFAULT 'all',
    month      DATE         NOT NULL,       -- 1st of the month
    dim_key    VARCHAR(180) NOT NULL,       -- e.g. product_name or category/subcategory
    dim_label  VARCHAR(255) NOT NULL,
    metrics    TEXT         NOT NULL,       -- JSON {metric_key: number|null}
    fetched_at DATETIME     NOT NULL,
    PRIMARY KEY (tenant_id, report_id, shop_id, month, dim_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Coverage source of truth: which (report, shop, month) combinations are
-- cached. Needed because "no fact rows" can also mean "month had no sales".
CREATE TABLE report_sync_state (
    tenant_id  VARCHAR(100) NOT NULL,
    report_id  VARCHAR(50)  NOT NULL,
    shop_id    VARCHAR(64)  NOT NULL DEFAULT 'all',
    month      DATE         NOT NULL,       -- 1st of the month
    status     VARCHAR(16)  NOT NULL,       -- 'final' (closed month, immutable)
    fetched_at DATETIME     NOT NULL,
    PRIMARY KEY (tenant_id, report_id, shop_id, month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
