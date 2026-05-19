"""
billing.py
==========
Subscription plans, usage tracking, and add-on purchases for DataMind AI.
All tables live in DataMind's internal DB (same env vars as integrations.py).
"""
import os
from typing import Optional, Dict, List, Tuple
from datetime import date, timedelta
import mysql.connector
from logger import get_logger

log = get_logger(__name__)

ADDON_PACKAGES = {
    "ai_credits": {"units_per_pack": 50,       "price_cents": 100, "label": "50 AI Credits"},
    "db_rows":    {"units_per_pack": 100_000,   "price_cents": 100, "label": "100K DB Rows"},
}

# Flat Token cost per operation type (feature compute component).
# T_total = (llm_tokens / 1000) + (rows_returned / 1000) + FEATURE_COST[op]
# Minimum charge per operation: 0.1 Tokens.
FEATURE_COST: dict = {
    "nl_query_rows":        0.0,   # data component only; LLM charged separately
    "prebuilt_template":    1.0,   # SQL template run — no LLM, flat compute cost
    "forecast":             2.0,   # Prophet ML fit + predict
    "anomaly_detection":    2.0,   # IsolationForest on full dataset
    "rfm_analysis":         1.5,
    "cohort_analysis":      1.5,
    "basket_analysis":      2.0,
    "growth_metrics":       1.0,
    "employee_performance": 1.0,
    "product_velocity":     1.0,
    "payment_breakdown":    0.5,
    "location_comparison":  0.5,
    "llm":                  0.0,   # LLM operations — token cost is T_llm only
}


def calculate_tokens(operation_type: str, llm_tokens: int = 0, rows_returned: int = 0) -> float:
    """Unified Token cost formula.

    T = (llm_tokens / 1000) + (rows_returned / 1000) + FEATURE_COST[operation_type]

    Examples:
      NL query returning 1 200 rows after 800 LLM tokens:
        T = 0.8 + 1.2 + 0.0 = 2.0 Tokens

      Prebuilt template returning 50 000 rows (no LLM):
        T = 0.0 + 50.0 + 1.0 = 51.0 Tokens

      Forecast on 10 000 rows:
        T = 0.0 + 10.0 + 2.0 = 12.0 Tokens
    """
    T_llm  = llm_tokens  / 1000
    T_db   = rows_returned / 1000
    T_feat = FEATURE_COST.get(operation_type, 0.5)
    return max(round(T_llm + T_db + T_feat, 4), 0.1)


# ── DB connection ─────────────────────────────────────────────────────────────

def _get_conn():
    return mysql.connector.connect(
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap_billing_tables():
    """Create billing tables and seed plan data. Safe to run on every startup."""
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                name           VARCHAR(50)    NOT NULL,
                price_usd      DECIMAL(10,2)  NOT NULL DEFAULT 0.00,
                price_cents    INT            NOT NULL DEFAULT 0,
                ai_credits     INT            NOT NULL DEFAULT 0,
                db_rows        BIGINT         NOT NULL DEFAULT 0,
                trial_days     INT            NOT NULL DEFAULT 14,
                validity_days  INT            NOT NULL DEFAULT 30,
                is_active      TINYINT        NOT NULL DEFAULT 1,
                sort_order     INT            NOT NULL DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                user_email   VARCHAR(255) NOT NULL,
                plan_id      INT          NOT NULL,
                status       ENUM('trial','active','expired','cancelled') NOT NULL,
                period_start DATE         NOT NULL,
                period_end   DATE         NOT NULL,
                created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_sub_email (user_email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscription_usage (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                user_email    VARCHAR(255) NOT NULL,
                period_start  DATE         NOT NULL,
                ai_base_used  INT          NOT NULL DEFAULT 0,
                ai_addon_used INT          NOT NULL DEFAULT 0,
                db_base_used  BIGINT       NOT NULL DEFAULT 0,
                db_addon_used BIGINT       NOT NULL DEFAULT 0,
                UNIQUE KEY uq_usage (user_email, period_start)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS addon_purchases (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                user_email      VARCHAR(255) NOT NULL,
                addon_type      ENUM('ai_credits','db_rows') NOT NULL,
                units           INT          NOT NULL,
                units_remaining INT          NOT NULL,
                purchased_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_addon_email (user_email)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage_log (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                user_email      VARCHAR(255)  NOT NULL,
                tokens          INT           NOT NULL DEFAULT 0,
                model           VARCHAR(50),
                endpoint        VARCHAR(255),
                credits_charged DECIMAL(10,4) NOT NULL DEFAULT 0,
                actual_cost     DECIMAL(10,6) NOT NULL DEFAULT 0,
                created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_llm_email   (user_email),
                INDEX idx_llm_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Widen column type — wrapped in try/except because ai_base_used
        # is dropped later in this same bootstrap; on second startup the
        # column is already gone and this MODIFY would throw 1054.
        try:
            cur.execute("""
                ALTER TABLE subscription_usage
                MODIFY COLUMN ai_base_used DECIMAL(12,4) NOT NULL DEFAULT 0
            """)
        except Exception:
            pass
        cur.execute("""
            ALTER TABLE llm_usage_log
            MODIFY COLUMN credits_charged DECIMAL(10,4) NOT NULL DEFAULT 0
        """)

        # ── Unified token columns (idempotent — silently skipped if present) ──
        for _stmt in [
            "ALTER TABLE subscription_usage ADD COLUMN tokens_used DECIMAL(12,4) NOT NULL DEFAULT 0",
            "ALTER TABLE subscription_plans  ADD COLUMN tokens_limit DECIMAL(12,4) NOT NULL DEFAULT 0",
        ]:
            try:
                cur.execute(_stmt)
            except Exception:
                pass  # column already exists

        # ── Drop stale columns (idempotent — already-gone columns are silently skipped) ──
        for _stmt in [
            # subscription_usage: only tokens_used is meaningful now
            "ALTER TABLE subscription_usage DROP COLUMN db_base_used",
            "ALTER TABLE subscription_usage DROP COLUMN db_addon_used",
            "ALTER TABLE subscription_usage DROP COLUMN ai_base_used",
            "ALTER TABLE subscription_usage DROP COLUMN ai_addon_used",
            # subscription_plans: billing_period is always 'monthly', never queried
            "ALTER TABLE subscription_plans  DROP COLUMN billing_period",
            # addon_purchases: units is written but never read; units_remaining is the live balance
            "ALTER TABLE addon_purchases      DROP COLUMN units",
            # billing_config: description is seeded but never read
            "ALTER TABLE billing_config       DROP COLUMN description",
            # llm_usage_log: actual_cost was always 0
            "ALTER TABLE llm_usage_log        DROP COLUMN actual_cost",
        ]:
            try:
                cur.execute(_stmt)
            except Exception:
                pass  # already removed or column never existed

        # ── Drop legacy credits tables (superseded by the billing system) ─────
        for _stmt in [
            "DROP TABLE IF EXISTS pricing_config",
            "DROP TABLE IF EXISTS credit_usage_log",
            "DROP TABLE IF EXISTS user_credits",
        ]:
            try:
                cur.execute(_stmt)
            except Exception:
                pass

        # ── Unified usage_log — one row per chargeable operation ──────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usage_log (
                id             INT AUTO_INCREMENT PRIMARY KEY,
                user_email     VARCHAR(255)  NOT NULL,
                tokens         DECIMAL(12,4) NOT NULL DEFAULT 0,
                operation_type VARCHAR(50)   NOT NULL,
                llm_tokens     INT           NOT NULL DEFAULT 0,
                rows_charged   INT           NOT NULL DEFAULT 0,
                created_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_ulog_email   (user_email),
                INDEX idx_ulog_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Config table — stores system-wide settings like the AI credit rate
        cur.execute("""
            CREATE TABLE IF NOT EXISTS billing_config (
                config_key   VARCHAR(100) PRIMARY KEY,
                config_value VARCHAR(255) NOT NULL,
                description  TEXT,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        cur.execute("""
            INSERT IGNORE INTO billing_config (config_key, config_value)
            VALUES ('ai_credit_rate', '1.0')
        """)

        # Seed plans  (name, price_usd, price_cents, ai_credits, db_rows, sort_order)
        plans = [
            ("Starter", "5.00",   500,   500,  2_000_000,    1),
            ("Growth",  "10.00", 1000,  1500,  5_000_000,    2),
            ("Pro",     "25.00", 2500, 10000, 20_000_000,    3),
        ]
        for name, price_usd, price_cents, ai_credits, db_rows, sort_order in plans:
            cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE subscription_plans
                    SET price_usd=%s, price_cents=%s, ai_credits=%s, db_rows=%s,
                        trial_days=14, validity_days=30, is_active=1, sort_order=%s
                    WHERE id=%s
                """, (price_usd, price_cents, ai_credits, db_rows, sort_order, row["id"]))
            else:
                cur.execute("""
                    INSERT INTO subscription_plans
                        (name, price_usd, price_cents, ai_credits,
                         db_rows, trial_days, validity_days, is_active, sort_order)
                    VALUES (%s,%s,%s,%s,%s,14,30,1,%s)
                """, (name, price_usd, price_cents, ai_credits, db_rows, sort_order))

        # Seed unified token limits
        _TOKEN_LIMITS = {"Starter": 500.0, "Growth": 1500.0, "Pro": 10000.0}
        for _pname, _tlimit in _TOKEN_LIMITS.items():
            cur.execute(
                "UPDATE subscription_plans SET tokens_limit=%s WHERE name=%s",
                (_tlimit, _pname),
            )

        conn.commit()
        log.info("Billing tables bootstrapped")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _process_subscription(cur, conn, user_email: str):
    """Auto-expire subscriptions whose period_end has passed."""
    today = date.today()
    cur.execute("""
        UPDATE user_subscriptions
        SET status = 'expired'
        WHERE user_email = %s
          AND status IN ('trial', 'active')
          AND period_end < %s
    """, (user_email, today))
    conn.commit()


def _get_addon_balance(cur, user_email: str, addon_type: str) -> int:
    """Sum remaining units across all unconsumed add-on packs."""
    cur.execute("""
        SELECT COALESCE(SUM(units_remaining), 0) AS balance
        FROM addon_purchases
        WHERE user_email = %s AND addon_type = %s AND units_remaining > 0
    """, (user_email, addon_type))
    row = cur.fetchone()
    return int(row["balance"]) if row else 0


def _get_period_usage(cur, user_email: str, period_start) -> dict:
    cur.execute("""
        SELECT tokens_used
        FROM subscription_usage
        WHERE user_email = %s AND period_start = %s
    """, (user_email, period_start))
    row = cur.fetchone()
    if row:
        return row
    return {"tokens_used": 0}


# ── Public functions ──────────────────────────────────────────────────────────

def start_trial(user_email: str, plan_name: str = "Starter"):
    """Start a 14-day trial for a newly registered user. No-op if subscription exists."""
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM user_subscriptions WHERE user_email = %s LIMIT 1", (user_email,))
        if cur.fetchone():
            return  # already has a subscription

        cur.execute("SELECT id, trial_days FROM subscription_plans WHERE name = %s AND is_active = 1", (plan_name,))
        plan = cur.fetchone()
        if not plan:
            log.warning("Trial plan not found", plan=plan_name)
            return

        today = date.today()
        period_end = today + timedelta(days=plan["trial_days"])
        cur.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'trial', %s, %s)
        """, (user_email, plan["id"], today, period_end))
        conn.commit()
        log.info("Trial started", email=user_email, plan=plan_name)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_subscription_plans() -> List[Dict]:
    """Return all active plans ordered by sort_order."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM subscription_plans WHERE is_active = 1 ORDER BY sort_order")
        rows = cur.fetchall()
        return [{k: (float(v) if hasattr(v, '__float__') and not isinstance(v, int) else v)
                 for k, v in row.items()} for row in rows]
    finally:
        cur.close()
        conn.close()


def get_plan_by_id(plan_id) -> Optional[Dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM subscription_plans WHERE id = %s", (plan_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {k: (float(v) if hasattr(v, '__float__') and not isinstance(v, int) else v)
                for k, v in row.items()}
    finally:
        cur.close()
        conn.close()


def subscribe_to_plan(user_email: str, plan_id: int):
    """Cancel existing subscription and start a new active one."""
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            UPDATE user_subscriptions
            SET status = 'cancelled'
            WHERE user_email = %s AND status IN ('trial', 'active')
        """, (user_email,))

        cur.execute("SELECT validity_days FROM subscription_plans WHERE id = %s", (plan_id,))
        plan = cur.fetchone()
        if not plan:
            raise ValueError(f"Plan {plan_id} not found")

        today = date.today()
        period_end = today + timedelta(days=plan["validity_days"])
        cur.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'active', %s, %s)
        """, (user_email, plan_id, today, period_end))
        conn.commit()
        log.info("Subscription activated", email=user_email, plan_id=plan_id)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_user_subscription(user_email: str) -> Dict:
    """Return full subscription state including usage and add-on balances."""
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor(dictionary=True)
    try:
        _process_subscription(cur, conn, user_email)

        cur.execute("""
            SELECT us.id, us.status, us.period_start, us.period_end,
                   sp.name AS plan_name, sp.id AS plan_id, sp.price_cents,
                   sp.ai_credits AS ai_base_limit, sp.db_rows AS db_base_limit,
                   sp.tokens_limit, sp.trial_days
            FROM user_subscriptions us
            JOIN subscription_plans sp ON sp.id = us.plan_id
            WHERE us.user_email = %s AND us.status IN ('trial', 'active')
            ORDER BY us.id DESC
            LIMIT 1
        """, (user_email,))
        sub = cur.fetchone()

        if not sub:
            return {
                "status": "no_subscription",
                "plan_name": None, "plan_id": None, "price_cents": 0,
                "ai_base_used": 0, "ai_base_limit": 0, "ai_addon_balance": 0, "ai_total_available": 0,
                "db_base_used": 0, "db_base_limit": 0, "db_addon_balance": 0, "db_total_available": 0,
                "usage_pct_ai": 0, "usage_pct_db": 0,
                "tokens_used": 0, "tokens_limit": 0, "tokens_pct": 0,
                "tokens_addon_balance": 0, "tokens_total_available": 0,
                "trial_days_remaining": 0, "period_start": None, "period_end": None,
                "can_use_ai": False, "can_use_db": False,
            }

        usage = _get_period_usage(cur, user_email, sub["period_start"])
        ai_addon_bal = _get_addon_balance(cur, user_email, "ai_credits")
        db_addon_bal = _get_addon_balance(cur, user_email, "db_rows")

        ai_base_limit = sub["ai_base_limit"]
        db_base_limit = int(sub["db_base_limit"])
        tokens_limit  = float(sub.get("tokens_limit") or 0)

        # DB rows: count actual synced rows live
        try:
            from integrations import get_user_total_rows
            db_base_used = get_user_total_rows(user_email)
        except Exception:
            db_base_used = 0

        ai_total = ai_base_limit + ai_addon_bal
        db_total = db_base_limit + db_addon_bal

        # Unified token accounting
        tokens_used          = float(usage.get("tokens_used") or 0)
        tokens_addon_balance = ai_addon_bal + (db_addon_bal / 1000)
        tokens_total         = tokens_limit + tokens_addon_balance

        usage_pct_db = round((db_base_used / db_total * 100) if db_total > 0 else 0, 1)
        tokens_pct   = round((tokens_used / tokens_total * 100) if tokens_total > 0 else 0, 1)

        today = date.today()
        trial_days_remaining = max(0, (sub["period_end"] - today).days) if sub["status"] == "trial" else 0

        return {
            "status":                sub["status"],
            "plan_name":             sub["plan_name"],
            "plan_id":               sub["plan_id"],
            "price_cents":           sub["price_cents"],
            "ai_base_limit":         ai_base_limit,
            "ai_addon_balance":      ai_addon_bal,
            "ai_total_available":    ai_total,
            "db_base_used":          db_base_used,
            "db_base_limit":         db_base_limit,
            "db_addon_balance":      db_addon_bal,
            "db_total_available":    db_total,
            "usage_pct_db":          usage_pct_db,
            "tokens_used":           round(tokens_used, 2),
            "tokens_limit":          tokens_limit,
            "tokens_addon_balance":  round(tokens_addon_balance, 2),
            "tokens_total_available": round(tokens_total, 2),
            "tokens_pct":            tokens_pct,
            "trial_days_remaining":  trial_days_remaining,
            "period_start":          str(sub["period_start"]),
            "period_end":            str(sub["period_end"]),
            "can_use_ai":            tokens_total == 0 or tokens_used < tokens_total,
            "can_use_db":            db_base_used < db_total,
        }
    finally:
        cur.close()
        conn.close()


# Plans that include each gated feature.
_PLAN_FEATURE_GATE: dict = {
    "forecast":          {"Growth", "Pro"},
    "anomaly_detection": {"Growth", "Pro"},
    "external_api":      {"Pro"},
}

# Data-history window per plan: months to look back, and row fallback when no date column.
_PLAN_HISTORY: dict = {
    "Starter": {"months": 1,  "row_limit": 1000},
    "Growth":  {"months": 3,  "row_limit": 3000},
    "Pro":     {"months": 12, "row_limit": 12000},
}


def check_plan_feature(user_email: str, feature: str) -> Tuple[bool, str]:
    """Return (True,'') if the user's plan includes *feature*, (False, reason) otherwise.
    Fails open on DB errors so a billing outage never hard-blocks the app."""
    try:
        conn = _get_conn()
        cur  = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT sp.name AS plan_name
            FROM user_subscriptions us
            JOIN subscription_plans sp ON sp.id = us.plan_id
            WHERE us.user_email = %s AND us.status IN ('trial','active')
            ORDER BY us.id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if not row:
            return False, "No active subscription."
        plan_name   = row["plan_name"]
        allowed     = _PLAN_FEATURE_GATE.get(feature)
        if allowed and plan_name not in allowed:
            upgrade_to = sorted(allowed)[0]
            return False, f"Upgrade to {upgrade_to} or above to use this feature."
        return True, ""
    except Exception as e:
        log.warning("check_plan_feature failed open", feature=feature, email=user_email, error=str(e))
        return True, ""


def get_plan_history_limit(user_email: str) -> dict:
    """Return the data-history window for the user's current plan.

    Returns a dict with keys:
      months      — how far back to filter date columns
      row_limit   — max rows to return when no date column is available
      cutoff_date — concrete date object (today minus months*30 days)
    """
    from datetime import date, timedelta
    try:
        conn = _get_conn()
        cur  = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT sp.name AS plan_name
            FROM user_subscriptions us
            JOIN subscription_plans sp ON sp.id = us.plan_id
            WHERE us.user_email = %s AND us.status IN ('trial','active')
            ORDER BY us.id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        cur.close(); conn.close()
        plan_name = (row or {}).get("plan_name", "Starter")
    except Exception:
        plan_name = "Starter"
    limits = _PLAN_HISTORY.get(plan_name, _PLAN_HISTORY["Starter"])
    cutoff = date.today() - timedelta(days=limits["months"] * 30)
    return {**limits, "plan_name": plan_name, "cutoff_date": cutoff}


def check_ai_limit(user_email: str) -> Tuple[bool, str]:
    """Returns (True, '') if user has tokens remaining, (False, reason) otherwise. Fails open on errors."""
    try:
        sub = get_user_subscription(user_email)
        status = sub.get("status")
        if status in ("expired", "cancelled", "no_subscription"):
            return False, "Your subscription has expired or is inactive."
        tokens_limit = sub.get("tokens_limit", 0)
        tokens_used  = sub.get("tokens_used",  0)
        if tokens_limit > 0 and tokens_used >= tokens_limit:
            return False, "You've used all your tokens for this billing period."
        return True, ""
    except Exception as e:
        log.warning("check_ai_limit failed open", email=user_email, error=str(e))
        return True, ""


def check_db_limit(user_email: str, rows: int) -> Tuple[bool, str]:
    """Returns (True, '') if user can process rows, (False, reason) otherwise. Fails open on errors."""
    try:
        sub = get_user_subscription(user_email)
        status = sub.get("status")
        if status in ("expired", "cancelled", "no_subscription"):
            return False, "Your subscription has expired or is inactive."
        if not sub.get("can_use_db", True):
            return False, "You've reached your DB row limit for this billing period."
        return True, ""
    except Exception as e:
        log.warning("check_db_limit failed open", email=user_email, error=str(e))
        return True, ""


def get_ai_credit_rate() -> float:
    """Return the credits-per-1000-tokens multiplier from billing_config."""
    try:
        conn = _get_conn()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT config_value FROM billing_config WHERE config_key = 'ai_credit_rate'")
        row = cur.fetchone()
        cur.close(); conn.close()
        return float(row["config_value"]) if row else 1.0
    except Exception:
        return 1.0


def set_ai_credit_rate(rate: float):
    """Update the credits-per-1000-tokens multiplier."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO billing_config (config_key, config_value)
        VALUES ('ai_credit_rate', %s)
        ON DUPLICATE KEY UPDATE config_value = VALUES(config_value)
    """, (str(rate),))
    conn.commit()
    cur.close(); conn.close()


def charge_tokens(user_email: str, tokens: float, operation_type: str,
                  llm_tokens: int = 0, rows_returned: int = 0):
    """Write one row to usage_log and increment tokens_used for the active billing period.

    Called by charge_ai_usage (for LLM ops) and directly by each analytics endpoint
    (for non-LLM ops).  Both paths funnel here so tokens_used is always the
    single source of truth for the unified Token balance.
    """
    # 1. Append to unified audit log
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO usage_log (user_email, tokens, operation_type, llm_tokens, rows_charged)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_email, tokens, operation_type, llm_tokens, rows_returned))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        log.warning("charge_tokens: log insert failed", email=user_email, error=str(e))

    # 2. Increment tokens_used for the active subscription period
    try:
        conn = _get_conn()
        cur  = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT period_start FROM user_subscriptions
            WHERE user_email = %s AND status IN ('trial', 'active')
            ORDER BY id DESC LIMIT 1
        """, (user_email,))
        sub = cur.fetchone()
        if sub:
            cur.execute("""
                INSERT INTO subscription_usage (user_email, period_start, tokens_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE tokens_used = tokens_used + %s
            """, (user_email, sub["period_start"], tokens, tokens))
            conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        log.warning("charge_tokens: usage update failed", email=user_email, error=str(e))


def charge_ai_usage(user_email: str, tokens: int, model: str, endpoint: str, api_key_cost: float = 0.0):
    """Log an LLM call to llm_usage_log (historical) and forward to charge_tokens for unified tracking."""
    rate = get_ai_credit_rate()
    credits_charged = round(tokens / 1000 * rate, 4)

    # 1. LLM-specific audit log (historical — kept for LLM call detail)
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO llm_usage_log (user_email, tokens, model, endpoint, credits_charged)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_email, tokens, model, endpoint, credits_charged))
        conn.commit()
        cur.close(); conn.close()
    except Exception as e:
        log.warning("charge_ai_usage: log insert failed", email=user_email, error=str(e))

    # 2. Unified token tracking (tokens_used counter + usage_log)
    try:
        charge_tokens(user_email, credits_charged, "llm", llm_tokens=tokens)
    except Exception as e:
        log.warning("charge_ai_usage: unified charge failed", email=user_email, error=str(e))


def purchase_addon(user_email: str, addon_type: str, quantity: int):
    """Purchase add-on packs (balance rolls over)."""
    if addon_type not in ADDON_PACKAGES:
        raise ValueError(f"Unknown addon_type: {addon_type}")

    units = ADDON_PACKAGES[addon_type]["units_per_pack"] * quantity
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO addon_purchases (user_email, addon_type, units_remaining)
            VALUES (%s, %s, %s)
        """, (user_email, addon_type, units))
        conn.commit()
        log.info("Addon purchased", email=user_email, type=addon_type, units=units)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_addon_pricing() -> Dict:
    return ADDON_PACKAGES


def get_llm_usage_history(user_email: str, limit: int = 50) -> List[Dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, tokens, model, endpoint, credits_charged,
                   DATE_FORMAT(created_at, '%%Y-%%m-%%dT%%H:%%i:%%s') AS created_at
            FROM llm_usage_log
            WHERE user_email = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_email, limit))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def get_token_usage_history(user_email: str, limit: int = 50) -> List[Dict]:
    """Return recent entries from the unified usage_log table."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, tokens, operation_type, llm_tokens, rows_charged,
                   DATE_FORMAT(created_at, '%%Y-%%m-%%dT%%H:%%i:%%s') AS created_at
            FROM usage_log
            WHERE user_email = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_email, limit))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()
