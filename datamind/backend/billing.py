"""
billing.py
==========
DataMind Pro subscription engine.

Single plan: Pro ($25/mo, 1500 AI credits, 2M DB rows, 14-day trial).
Trial auto-starts on user registration. After trial, user must subscribe.
"""
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import mysql.connector
from logger import get_logger

log = get_logger(__name__)

# ── Internal LLM cost table (never exposed to users) ──────────────────────────
_LLM_COST_PER_1K = {
    "gemini": 0.0002,
    "deepseek": 0.001,
}
_DEFAULT_COST_PER_1K = 0.0002
_MARKUP = 2.0
_CREDIT_VALUE = 0.01  # 1 credit = $0.01 face value

# Add-on pack pricing
_ADDON_PRICE = {
    "ai_credits": {"units": 100, "price_cents": 200},    # 100 credits = $2
    "db_rows":    {"units": 100_000, "price_cents": 100}, # 100k rows = $1
}


def _get_conn():
    return mysql.connector.connect(
        host=os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port=int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database=os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user=os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password=os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


# ── Bootstrap ──────────────────────────────────────────────────────────────────

def bootstrap_billing_tables():
    """Create all billing tables and seed the Pro plan. Safe on every startup."""
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            name        VARCHAR(50)  NOT NULL,
            price_cents INT          NOT NULL,
            ai_credits  INT          NOT NULL,
            db_rows     BIGINT       NOT NULL,
            trial_days  INT          NOT NULL DEFAULT 14,
            is_active   TINYINT      NOT NULL DEFAULT 1
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
            id               INT AUTO_INCREMENT PRIMARY KEY,
            user_email       VARCHAR(255) NOT NULL,
            period_start     DATE         NOT NULL,
            ai_credits_used  INT          NOT NULL DEFAULT 0,
            db_rows_used     BIGINT       NOT NULL DEFAULT 0,
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
            user_email      VARCHAR(255)    NOT NULL,
            tokens          INT             NOT NULL DEFAULT 0,
            model           VARCHAR(50),
            endpoint        VARCHAR(255),
            credits_charged DECIMAL(8,2)    NOT NULL DEFAULT 0,
            actual_cost     DECIMAL(10,6)   NOT NULL DEFAULT 0,
            created_at      TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_llm_email   (user_email),
            INDEX idx_llm_created (created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    # Seed Pro plan once
    cur.execute("SELECT COUNT(*) FROM subscription_plans")
    if cur.fetchone()[0] == 0:
        cur.execute("""
            INSERT INTO subscription_plans (name, price_cents, ai_credits, db_rows, trial_days, is_active)
            VALUES ('Pro', 2500, 1500, 2000000, 14, 1)
        """)
        log.info("Seeded Pro subscription plan")

    conn.commit()
    conn.close()
    log.info("Billing tables bootstrapped")


# ── Plan ───────────────────────────────────────────────────────────────────────

def get_subscription_plan() -> Optional[Dict]:
    """Return the active Pro plan row."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM subscription_plans WHERE is_active = 1 LIMIT 1")
        row = cur.fetchone()
        return row
    finally:
        conn.close()


# ── Trial ──────────────────────────────────────────────────────────────────────

def start_trial(user_email: str):
    """
    Idempotent. Creates a trial subscription for the user if none exists.
    Trial length is read from the Pro plan row in the DB.
    """
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        # No-op if any subscription already exists
        cur.execute("SELECT id FROM user_subscriptions WHERE user_email = %s LIMIT 1", (user_email,))
        if cur.fetchone():
            return

        cur.execute("SELECT id, trial_days FROM subscription_plans WHERE is_active = 1 LIMIT 1")
        plan = cur.fetchone()
        if not plan:
            log.warning("No active plan found — cannot start trial", user=user_email)
            return

        today = date.today()
        period_end = today + timedelta(days=plan["trial_days"])

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'trial', %s, %s)
        """, (user_email, plan["id"], today, period_end))

        cur2.execute("""
            INSERT INTO subscription_usage (user_email, period_start)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE user_email = user_email
        """, (user_email, today))

        conn.commit()
        log.info("Trial started", user=user_email, ends=str(period_end))
    finally:
        conn.close()


# ── Subscription state ─────────────────────────────────────────────────────────

def _auto_expire(cur, user_email: str):
    """Mark overdue trial/active subscriptions as expired."""
    cur.execute("""
        UPDATE user_subscriptions
        SET status = 'expired'
        WHERE user_email = %s
          AND status IN ('trial', 'active')
          AND period_end < CURDATE()
    """, (user_email,))


def get_user_subscription(user_email: str) -> Dict:
    """
    Returns the full subscription state for a user.
    Auto-expires overdue subscriptions before returning.
    """
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        _auto_expire(cur, user_email)
        conn.commit()

        cur.execute("""
            SELECT s.*, p.name AS plan_name, p.price_cents, p.ai_credits AS plan_ai_credits,
                   p.db_rows AS plan_db_rows, p.trial_days
            FROM user_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.user_email = %s
            ORDER BY s.id DESC
            LIMIT 1
        """, (user_email,))
        sub = cur.fetchone()

        if not sub:
            return {
                "status": "no_subscription",
                "plan_name": "DataMind Pro",
                "can_use_ai": False,
                "can_use_db": False,
                "ai_credits_used": 0,
                "ai_credits_limit": 0,
                "db_rows_used": 0,
                "db_rows_limit": 0,
                "addon_ai_balance": 0,
                "addon_db_balance": 0,
                "usage_pct_ai": 100,
                "usage_pct_db": 100,
                "trial_days_remaining": 0,
                "period_end": None,
            }

        status = sub["status"]
        period_start = sub["period_start"]

        # Usage this period
        cur.execute("""
            SELECT ai_credits_used, db_rows_used
            FROM subscription_usage
            WHERE user_email = %s AND period_start = %s
        """, (user_email, period_start))
        usage_row = cur.fetchone() or {"ai_credits_used": 0, "db_rows_used": 0}

        # Add-on balances (sum of remaining units by type)
        cur.execute("""
            SELECT addon_type, SUM(units_remaining) AS balance
            FROM addon_purchases
            WHERE user_email = %s AND units_remaining > 0
            GROUP BY addon_type
        """, (user_email,))
        addon_rows = {r["addon_type"]: int(r["balance"]) for r in cur.fetchall()}

        ai_used = usage_row["ai_credits_used"]
        ai_limit = sub["plan_ai_credits"]
        db_used = usage_row["db_rows_used"]
        db_limit = sub["plan_db_rows"]
        addon_ai = addon_rows.get("ai_credits", 0)
        addon_db = addon_rows.get("db_rows", 0)

        effective_ai_limit = ai_limit + addon_ai
        effective_db_limit = db_limit + addon_db

        can_use_ai = status in ("trial", "active") and ai_used < effective_ai_limit
        can_use_db = status in ("trial", "active") and db_used < effective_db_limit

        pct_ai = round((ai_used / effective_ai_limit) * 100) if effective_ai_limit > 0 else 100
        pct_db = round((db_used / effective_db_limit) * 100) if effective_db_limit > 0 else 100

        trial_days_remaining = 0
        if status == "trial":
            delta = sub["period_end"] - date.today()
            trial_days_remaining = max(0, delta.days)

        return {
            "status": status,
            "plan_name": sub["plan_name"],
            "plan_id": sub["plan_id"],
            "price_cents": sub["price_cents"],
            "can_use_ai": can_use_ai,
            "can_use_db": can_use_db,
            "ai_credits_used": ai_used,
            "ai_credits_limit": effective_ai_limit,
            "db_rows_used": db_used,
            "db_rows_limit": effective_db_limit,
            "addon_ai_balance": addon_ai,
            "addon_db_balance": addon_db,
            "usage_pct_ai": pct_ai,
            "usage_pct_db": pct_db,
            "trial_days_remaining": trial_days_remaining,
            "period_start": str(period_start) if period_start else None,
            "period_end": str(sub["period_end"]) if sub["period_end"] else None,
        }
    finally:
        conn.close()


# ── Limit checks ───────────────────────────────────────────────────────────────

def check_ai_limit(user_email: str) -> Tuple[bool, str]:
    """Return (allowed, reason). Called before every LLM call."""
    try:
        state = get_user_subscription(user_email)
    except Exception as e:
        log.warning("Billing check failed — allowing request", user=user_email, error=str(e))
        return True, ""

    status = state["status"]
    if status in ("expired", "cancelled", "no_subscription"):
        return False, "Your subscription has expired. Please subscribe to DataMind Pro to continue."
    if not state["can_use_ai"]:
        return False, "You have used all your AI credits for this period. Purchase add-ons or upgrade your plan."
    return True, ""


def check_db_row_limit(user_email: str, rows: int = 1) -> Tuple[bool, str]:
    """Return (allowed, reason). Called before DB row operations."""
    try:
        state = get_user_subscription(user_email)
    except Exception as e:
        log.warning("Billing DB check failed — allowing", user=user_email, error=str(e))
        return True, ""

    status = state["status"]
    if status in ("expired", "cancelled", "no_subscription"):
        return False, "Your subscription has expired."
    remaining = state["db_rows_limit"] - state["db_rows_used"]
    if remaining < rows:
        return False, "DB row limit reached. Purchase add-on rows to continue syncing."
    return True, ""


# ── Subscribe ──────────────────────────────────────────────────────────────────

def subscribe_to_plan(user_email: str, plan_id: int):
    """Cancel existing subscription and create a new active one."""
    conn = _get_conn()
    cur = conn.cursor()
    try:
        # Cancel existing
        cur.execute("""
            UPDATE user_subscriptions
            SET status = 'cancelled'
            WHERE user_email = %s AND status IN ('trial', 'active')
        """, (user_email,))

        today = date.today()
        period_end = today + timedelta(days=30)

        cur.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'active', %s, %s)
        """, (user_email, plan_id, today, period_end))

        cur.execute("""
            INSERT INTO subscription_usage (user_email, period_start)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE user_email = user_email
        """, (user_email, today))

        conn.commit()
        log.info("User subscribed to plan", user=user_email, plan_id=plan_id)
    finally:
        conn.close()


# ── Charge AI usage ────────────────────────────────────────────────────────────

def charge_ai_usage(user_email: str, tokens: int, model: str, endpoint: str = ""):
    """
    Deduct credits from add-on packs (FIFO) then plan quota.
    Logs every call to llm_usage_log.
    """
    model_key = (model or "").lower()
    cost_per_1k = _LLM_COST_PER_1K.get(model_key, _DEFAULT_COST_PER_1K)
    actual_cost = (tokens / 1000.0) * cost_per_1k
    credits = round((actual_cost * _MARKUP) / _CREDIT_VALUE, 2)

    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        # Determine current period_start
        cur.execute("""
            SELECT period_start FROM user_subscriptions
            WHERE user_email = %s AND status IN ('trial','active')
            ORDER BY id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        period_start = row["period_start"] if row else date.today()

        # Deplete add-on packs FIFO
        remaining_credits = credits
        cur2 = conn.cursor()
        if remaining_credits > 0:
            cur.execute("""
                SELECT id, units_remaining FROM addon_purchases
                WHERE user_email = %s AND addon_type = 'ai_credits' AND units_remaining > 0
                ORDER BY purchased_at ASC
            """, (user_email,))
            addons = cur.fetchall()
            for addon in addons:
                if remaining_credits <= 0:
                    break
                deduct = min(remaining_credits, addon["units_remaining"])
                cur2.execute("""
                    UPDATE addon_purchases SET units_remaining = units_remaining - %s WHERE id = %s
                """, (deduct, addon["id"]))
                remaining_credits -= deduct

        # Remaining credits come from plan quota
        if remaining_credits > 0:
            cur2.execute("""
                INSERT INTO subscription_usage (user_email, period_start, ai_credits_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE ai_credits_used = ai_credits_used + %s
            """, (user_email, period_start, remaining_credits, remaining_credits))

        # Log the call
        cur2.execute("""
            INSERT INTO llm_usage_log (user_email, tokens, model, endpoint, credits_charged, actual_cost)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_email, tokens, model, endpoint, credits, actual_cost))

        conn.commit()
        log.info("AI usage charged", user=user_email, tokens=tokens, credits=credits)
    finally:
        conn.close()


# ── Charge DB rows ─────────────────────────────────────────────────────────────

def charge_db_rows(user_email: str, rows: int):
    """Record DB row consumption for the current period."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT period_start FROM user_subscriptions
            WHERE user_email = %s AND status IN ('trial','active')
            ORDER BY id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        period_start = row["period_start"] if row else date.today()

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO subscription_usage (user_email, period_start, db_rows_used)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE db_rows_used = db_rows_used + %s
        """, (user_email, period_start, rows, rows))
        conn.commit()
    finally:
        conn.close()


# ── Add-ons ────────────────────────────────────────────────────────────────────

def purchase_addon(user_email: str, addon_type: str, quantity: int):
    """Insert an add-on purchase. quantity = number of packs."""
    if addon_type not in _ADDON_PRICE:
        raise ValueError(f"Unknown addon_type: {addon_type}")
    pack = _ADDON_PRICE[addon_type]
    units = pack["units"] * quantity

    conn = _get_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO addon_purchases (user_email, addon_type, units, units_remaining)
            VALUES (%s, %s, %s, %s)
        """, (user_email, addon_type, units, units))
        conn.commit()
        log.info("Add-on purchased", user=user_email, type=addon_type, units=units)
    finally:
        conn.close()


def get_addon_history(user_email: str) -> List[Dict]:
    """Return all add-on purchases for the user."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT addon_type, units, units_remaining, purchased_at
            FROM addon_purchases
            WHERE user_email = %s
            ORDER BY purchased_at DESC
        """, (user_email,))
        rows = cur.fetchall()
        for r in rows:
            if r.get("purchased_at"):
                r["purchased_at"] = r["purchased_at"].isoformat()
        return rows
    finally:
        conn.close()


# ── LLM usage history ──────────────────────────────────────────────────────────

def get_llm_usage_history(user_email: str, limit: int = 100) -> List[Dict]:
    """Return recent LLM calls. Model field is stripped from the response."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT tokens, endpoint, credits_charged, actual_cost, created_at
            FROM llm_usage_log
            WHERE user_email = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (user_email, limit))
        rows = cur.fetchall()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
            r["actual_cost"] = float(r["actual_cost"])
            r["credits_charged"] = float(r["credits_charged"])
        return rows
    finally:
        conn.close()


# ── Add-on pricing config (for UI) ─────────────────────────────────────────────

def get_addon_pricing() -> Dict:
    return {
        "ai_credits": _ADDON_PRICE["ai_credits"],
        "db_rows":    _ADDON_PRICE["db_rows"],
    }
