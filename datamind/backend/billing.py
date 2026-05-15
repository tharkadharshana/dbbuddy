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
                billing_period VARCHAR(20)    NOT NULL DEFAULT 'monthly',
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
                credits_charged DECIMAL(8,2)  NOT NULL DEFAULT 0,
                actual_cost     DECIMAL(10,6) NOT NULL DEFAULT 0,
                created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_llm_email   (user_email),
                INDEX idx_llm_created (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Seed plans
        plans = [
            ("Starter", "5.00",  500,  300,    500_000,    1),
            ("Growth",  "10.00", 1000, 750,  2_000_000,    2),
            ("Pro",     "25.00", 2500, 2000, 10_000_000,   3),
        ]
        for name, price_usd, price_cents, ai_credits, db_rows, sort_order in plans:
            cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                cur.execute("""
                    UPDATE subscription_plans
                    SET price_usd=%s, price_cents=%s, ai_credits=%s, db_rows=%s,
                        billing_period='monthly', trial_days=14, validity_days=30,
                        is_active=1, sort_order=%s
                    WHERE id=%s
                """, (price_usd, price_cents, ai_credits, db_rows, sort_order, row["id"]))
            else:
                cur.execute("""
                    INSERT INTO subscription_plans
                        (name, price_usd, billing_period, price_cents, ai_credits,
                         db_rows, trial_days, validity_days, is_active, sort_order)
                    VALUES (%s,%s,'monthly',%s,%s,%s,14,30,1,%s)
                """, (name, price_usd, price_cents, ai_credits, db_rows, sort_order))

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
        SELECT ai_base_used, ai_addon_used, db_base_used, db_addon_used
        FROM subscription_usage
        WHERE user_email = %s AND period_start = %s
    """, (user_email, period_start))
    row = cur.fetchone()
    if row:
        return row
    return {"ai_base_used": 0, "ai_addon_used": 0, "db_base_used": 0, "db_addon_used": 0}


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
                   sp.trial_days
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
                "trial_days_remaining": 0, "period_start": None, "period_end": None,
                "can_use_ai": False, "can_use_db": False,
            }

        usage = _get_period_usage(cur, user_email, sub["period_start"])
        ai_addon_bal = _get_addon_balance(cur, user_email, "ai_credits")
        db_addon_bal = _get_addon_balance(cur, user_email, "db_rows")

        ai_base_used  = usage["ai_base_used"]
        db_base_used  = usage["db_base_used"]
        ai_base_limit = sub["ai_base_limit"]
        db_base_limit = int(sub["db_base_limit"])

        ai_total = ai_base_limit + ai_addon_bal
        db_total = db_base_limit + db_addon_bal

        usage_pct_ai = round((ai_base_used / ai_total * 100) if ai_total > 0 else 0, 1)
        usage_pct_db = round((db_base_used / db_total * 100) if db_total > 0 else 0, 1)

        today = date.today()
        trial_days_remaining = max(0, (sub["period_end"] - today).days) if sub["status"] == "trial" else 0

        return {
            "status":              sub["status"],
            "plan_name":           sub["plan_name"],
            "plan_id":             sub["plan_id"],
            "price_cents":         sub["price_cents"],
            "ai_base_used":        ai_base_used,
            "ai_base_limit":       ai_base_limit,
            "ai_addon_balance":    ai_addon_bal,
            "ai_total_available":  ai_total,
            "db_base_used":        db_base_used,
            "db_base_limit":       db_base_limit,
            "db_addon_balance":    db_addon_bal,
            "db_total_available":  db_total,
            "usage_pct_ai":        usage_pct_ai,
            "usage_pct_db":        usage_pct_db,
            "trial_days_remaining": trial_days_remaining,
            "period_start":        str(sub["period_start"]),
            "period_end":          str(sub["period_end"]),
            "can_use_ai":          ai_base_used < ai_total,
            "can_use_db":          db_base_used < db_total,
        }
    finally:
        cur.close()
        conn.close()


def check_ai_limit(user_email: str) -> Tuple[bool, str]:
    """Returns (True, '') if user can use AI, (False, reason) otherwise. Fails open on errors."""
    try:
        sub = get_user_subscription(user_email)
        status = sub.get("status")
        if status in ("expired", "cancelled", "no_subscription"):
            return False, "Your subscription has expired or is inactive."
        if not sub.get("can_use_ai", True):
            return False, "You've used all your AI credits for this billing period."
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


def charge_ai_usage(user_email: str, tokens: int, model: str, endpoint: str, api_key_cost: float = 0.0):
    """Log an AI call and increment usage counters."""
    try:
        credits_per_1k = 0.03  # default
        model_lower = (model or "").lower()
        if "gemini" in model_lower:
            credits_per_1k = 0.001
        elif "deepseek" in model_lower:
            credits_per_1k = 0.0003

        credits_charged = round(tokens / 1000 * credits_per_1k, 6)

        conn = _get_conn()
        conn.autocommit = False
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                INSERT INTO llm_usage_log (user_email, tokens, model, endpoint, credits_charged, actual_cost)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (user_email, tokens, model, endpoint, credits_charged, api_key_cost))

            # Get current subscription period
            cur.execute("""
                SELECT period_start, ai_credits AS ai_base_limit
                FROM user_subscriptions us
                JOIN subscription_plans sp ON sp.id = us.plan_id
                WHERE us.user_email = %s AND us.status IN ('trial', 'active')
                ORDER BY us.id DESC LIMIT 1
            """, (user_email,))
            sub = cur.fetchone()
            if sub:
                cur.execute("""
                    INSERT INTO subscription_usage (user_email, period_start, ai_base_used)
                    VALUES (%s, %s, 1)
                    ON DUPLICATE KEY UPDATE ai_base_used = ai_base_used + 1
                """, (user_email, sub["period_start"]))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        log.warning("charge_ai_usage failed", email=user_email, error=str(e))


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
            INSERT INTO addon_purchases (user_email, addon_type, units, units_remaining)
            VALUES (%s, %s, %s, %s)
        """, (user_email, addon_type, units, units))
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
            SELECT id, tokens, model, endpoint, credits_charged, actual_cost,
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
