"""
billing.py
==========
DataMind subscription engine.

Three plans: Starter ($5), Growth ($10), Pro ($25) — all DB-driven.
Every plan gets a monthly base quota that resets each period.
Add-on credits/rows top-up the pool on-demand and ROLL OVER:
  - Base credits consumed first each period.
  - Remaining add-on balance carries forward to the next period.
  - Unused base credits do NOT carry forward.
"""
import os
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
import mysql.connector
from logger import get_logger

log = get_logger(__name__)

# ── Internal LLM cost table (never exposed to users) ──────────────────────────
_LLM_COST_PER_1K = {
    "gemini":   0.0002,
    "deepseek": 0.001,
}
_DEFAULT_COST_PER_1K = 0.0002
_MARKUP       = 2.0
_CREDIT_VALUE = 0.01   # 1 AI Credit = $0.01 face value

# Add-on pack pricing: 1 pack = $1
_ADDON_PRICE = {
    "ai_credits": {"units": 50,      "price_cents": 100},  # 50 credits  = $1
    "db_rows":    {"units": 100_000, "price_cents": 100},  # 100k rows   = $1
}

# Preset top-up packages shown in the UI
ADDON_PACKAGES = {
    "ai_credits": [
        {"dollars": 5,  "qty": 5,  "units": 250,       "label": "250 credits"},
        {"dollars": 10, "qty": 10, "units": 500,       "label": "500 credits"},
        {"dollars": 25, "qty": 25, "units": 1_250,     "label": "1,250 credits"},
    ],
    "db_rows": [
        {"dollars": 5,  "qty": 5,  "units": 500_000,   "label": "500,000 rows"},
        {"dollars": 10, "qty": 10, "units": 1_000_000, "label": "1,000,000 rows"},
        {"dollars": 25, "qty": 25, "units": 2_500_000, "label": "2,500,000 rows"},
    ],
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
    """
    One-time setup for global billing tables.

    Tables are shared across ALL users — not per-user.
    CREATE TABLE IF NOT EXISTS is a no-op after the first run.
    ALTER TABLE migrations run once then skip silently.
    Plan seeding does explicit SELECT → UPDATE or INSERT per plan name.
    """
    conn = _get_conn()
    cur  = conn.cursor(dictionary=True)
    cur2 = conn.cursor()

    # ── 1. Create tables (no-op if already exist) ──────────────────────────

    cur2.execute("""
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
            sort_order     INT            NOT NULL DEFAULT 0,
            created_at     TIMESTAMP      DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur2.execute("""
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

    cur2.execute("""
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

    cur2.execute("""
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

    cur2.execute("""
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

    conn.commit()

    # ── 2. Schema migrations (one-time, silent if column/key already exists) ─
    for _sql in [
        # subscription_plans columns added in later versions
        "ALTER TABLE subscription_plans ADD COLUMN price_cents   INT     NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_plans ADD COLUMN ai_credits    INT     NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_plans ADD COLUMN db_rows       BIGINT  NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_plans ADD COLUMN trial_days    INT     NOT NULL DEFAULT 14",
        "ALTER TABLE subscription_plans ADD COLUMN validity_days INT     NOT NULL DEFAULT 30",
        "ALTER TABLE subscription_plans ADD COLUMN is_active     TINYINT NOT NULL DEFAULT 1",
        "ALTER TABLE subscription_plans ADD COLUMN sort_order    INT     NOT NULL DEFAULT 0",
        # subscription_usage columns added in later versions
        "ALTER TABLE subscription_usage ADD COLUMN ai_base_used  INT    NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_usage ADD COLUMN ai_addon_used INT    NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_usage ADD COLUMN db_base_used  BIGINT NOT NULL DEFAULT 0",
        "ALTER TABLE subscription_usage ADD COLUMN db_addon_used BIGINT NOT NULL DEFAULT 0",
    ]:
        try:
            cur2.execute(_sql)
            conn.commit()
        except Exception:
            pass  # column / key already exists — safe to ignore

    # ── 3. Seed / sync the 3 plans ─────────────────────────────────────────
    #
    # Does an explicit SELECT per name then UPDATE or INSERT.
    # This avoids any dependency on UNIQUE KEY constraints and is always correct
    # regardless of what the existing DB state looks like.
    #
    # Columns:  name       price  ai_cr    db_rows     trial  validity
    # name, price_usd, price_cents, ai_credits, db_rows, trial_days, validity_days
    PLANS = [
        ("Starter",  "5.00",   500,  300,    500_000,    14, 30),
        ("Growth",  "10.00",  1000,  750,  2_000_000,    14, 30),
        ("Pro",     "25.00",  2500, 2000, 10_000_000,    14, 30),
    ]
    for sort_order, (name, price_usd, price_cents, ai_credits, db_rows, trial_days, validity_days) in enumerate(PLANS, start=1):
        cur.execute("SELECT id FROM subscription_plans WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            cur2.execute("""
                UPDATE subscription_plans
                SET price_usd=%s, price_cents=%s, ai_credits=%s, db_rows=%s,
                    trial_days=%s, validity_days=%s, billing_period='monthly',
                    is_active=1, sort_order=%s
                WHERE id=%s
            """, (price_usd, price_cents, ai_credits, db_rows, trial_days, validity_days, sort_order, row["id"]))
            log.info("Updated plan", name=name, id=row["id"])
        else:
            cur2.execute("""
                INSERT INTO subscription_plans
                    (name, price_usd, billing_period, price_cents, ai_credits, db_rows,
                     trial_days, validity_days, is_active, sort_order)
                VALUES (%s,%s,'monthly',%s,%s,%s,%s,%s,1,%s)
            """, (name, price_usd, price_cents, ai_credits, db_rows, trial_days, validity_days, sort_order))
            log.info("Inserted plan", name=name)

    conn.commit()
    conn.close()
    log.info("Billing tables ready")


# ── Plans ──────────────────────────────────────────────────────────────────────

def get_subscription_plans() -> List[Dict]:
    """Return all active plans ordered by price."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT id, name, price_cents, ai_credits, db_rows, trial_days, validity_days
            FROM subscription_plans
            WHERE is_active = 1
            ORDER BY sort_order ASC
        """)
        return cur.fetchall()
    finally:
        conn.close()


def get_plan_by_id(plan_id: int) -> Optional[Dict]:
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM subscription_plans WHERE id = %s AND is_active = 1", (plan_id,))
        return cur.fetchone()
    finally:
        conn.close()


# ── Trial ──────────────────────────────────────────────────────────────────────

def start_trial(user_email: str, plan_name: str = "Starter"):
    """
    Auto-start a trial on the given plan (default: Starter).
    Idempotent — no-op if any subscription already exists.
    """
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM user_subscriptions WHERE user_email = %s LIMIT 1", (user_email,))
        if cur.fetchone():
            return

        cur.execute("""
            SELECT id, trial_days FROM subscription_plans
            WHERE name = %s AND is_active = 1 LIMIT 1
        """, (plan_name,))
        plan = cur.fetchone()
        if not plan:
            cur.execute("SELECT id, trial_days FROM subscription_plans WHERE is_active = 1 ORDER BY sort_order LIMIT 1")
            plan = cur.fetchone()
        if not plan:
            log.warning("No active plan — cannot start trial", user=user_email)
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
        log.info("Trial started", user=user_email, plan=plan_name, ends=str(period_end))
    finally:
        conn.close()


# ── Auto-expire / auto-renew ───────────────────────────────────────────────────

def _process_subscription(cur, conn, user_email: str):
    """
    - Trial past period_end → expired
    - Active past period_end → create new 30-day period (base resets, add-ons carry over)
    """
    cur.execute("""
        SELECT id, plan_id, status, period_end
        FROM user_subscriptions
        WHERE user_email = %s
        ORDER BY id DESC LIMIT 1
    """, (user_email,))
    sub = cur.fetchone()
    if not sub:
        return

    status = sub["status"]
    period_end = sub["period_end"]
    today = date.today()

    if status == "trial" and period_end < today:
        cur.execute("UPDATE user_subscriptions SET status='expired' WHERE id=%s", (sub["id"],))
        conn.commit()

    elif status == "active" and period_end < today:
        # Auto-renew using the plan's validity_days. Add-ons carry over untouched.
        cur.execute("SELECT validity_days FROM subscription_plans WHERE id = %s", (sub["plan_id"],))
        vrow = cur.fetchone()
        validity_days = (vrow["validity_days"] if vrow else 30) if isinstance(vrow, dict) else 30
        new_start = period_end + timedelta(days=1)
        new_end   = new_start + timedelta(days=validity_days)
        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'active', %s, %s)
        """, (user_email, sub["plan_id"], new_start, new_end))
        cur2.execute("""
            INSERT INTO subscription_usage (user_email, period_start)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE user_email = user_email
        """, (user_email, new_start))
        conn.commit()
        log.info("Subscription auto-renewed", user=user_email, new_end=str(new_end))


# ── Subscription state ─────────────────────────────────────────────────────────

def get_user_subscription(user_email: str) -> Dict:
    """
    Returns full subscription state. Triggers auto-expire / auto-renew first.
    """
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        _process_subscription(cur, conn, user_email)

        cur.execute("""
            SELECT s.id, s.plan_id, s.status, s.period_start, s.period_end,
                   p.name AS plan_name, p.price_cents,
                   p.ai_credits AS plan_ai_credits,
                   p.db_rows    AS plan_db_rows,
                   p.trial_days
            FROM user_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.user_email = %s
            ORDER BY s.id DESC LIMIT 1
        """, (user_email,))
        sub = cur.fetchone()

        _empty = {
            "status": "no_subscription",
            "plan_name": None, "plan_id": None, "price_cents": 0,
            "can_use_ai": False, "can_use_db": False,
            "ai_base_used": 0, "ai_base_limit": 0,
            "ai_addon_balance": 0, "ai_total_available": 0,
            "db_base_used": 0, "db_base_limit": 0,
            "db_addon_balance": 0, "db_total_available": 0,
            "usage_pct_ai": 100, "usage_pct_db": 100,
            "trial_days_remaining": 0,
            "period_start": None, "period_end": None,
        }
        if not sub:
            return _empty

        status       = sub["status"]
        period_start = sub["period_start"]

        # Usage this period
        cur.execute("""
            SELECT ai_base_used, ai_addon_used, db_base_used, db_addon_used
            FROM subscription_usage
            WHERE user_email = %s AND period_start = %s
        """, (user_email, period_start))
        usage = cur.fetchone() or {"ai_base_used": 0, "ai_addon_used": 0, "db_base_used": 0, "db_addon_used": 0}

        # Add-on pool balances
        cur.execute("""
            SELECT addon_type, SUM(units_remaining) AS balance
            FROM addon_purchases
            WHERE user_email = %s AND units_remaining > 0
            GROUP BY addon_type
        """, (user_email,))
        addon_bal = {r["addon_type"]: int(r["balance"]) for r in cur.fetchall()}

        ai_base_used  = usage["ai_base_used"]
        ai_base_limit = sub["plan_ai_credits"]
        ai_addon_bal  = addon_bal.get("ai_credits", 0)

        db_base_used  = usage["db_base_used"]
        db_base_limit = sub["plan_db_rows"]
        db_addon_bal  = addon_bal.get("db_rows", 0)

        ai_base_remaining = max(0, ai_base_limit - ai_base_used)
        db_base_remaining = max(0, db_base_limit - db_base_used)

        ai_total_available = ai_base_remaining + ai_addon_bal
        db_total_available = db_base_remaining + db_addon_bal

        active = status in ("trial", "active")
        can_use_ai = active and ai_total_available > 0
        can_use_db = active and db_total_available > 0

        pct_ai = round((ai_base_used / ai_base_limit) * 100) if ai_base_limit > 0 else 100
        pct_db = round((db_base_used / db_base_limit) * 100) if db_base_limit > 0 else 100

        trial_days_remaining = 0
        if status == "trial":
            delta = sub["period_end"] - today if isinstance(sub["period_end"], date) else date.fromisoformat(str(sub["period_end"])) - date.today()
            trial_days_remaining = max(0, delta.days)

        return {
            "status":             status,
            "plan_name":          sub["plan_name"],
            "plan_id":            sub["plan_id"],
            "price_cents":        sub["price_cents"],
            "can_use_ai":         can_use_ai,
            "can_use_db":         can_use_db,
            # AI
            "ai_base_used":       ai_base_used,
            "ai_base_limit":      ai_base_limit,
            "ai_addon_balance":   ai_addon_bal,
            "ai_total_available": ai_total_available,
            "usage_pct_ai":       pct_ai,
            # DB
            "db_base_used":       db_base_used,
            "db_base_limit":      db_base_limit,
            "db_addon_balance":   db_addon_bal,
            "db_total_available": db_total_available,
            "usage_pct_db":       pct_db,
            # Meta
            "trial_days_remaining": trial_days_remaining,
            "period_start": str(period_start),
            "period_end":   str(sub["period_end"]),
        }
    finally:
        conn.close()


# ── Limit checks ───────────────────────────────────────────────────────────────

def check_ai_limit(user_email: str) -> Tuple[bool, str]:
    try:
        state = get_user_subscription(user_email)
    except Exception as e:
        log.warning("Billing check failed — allowing", user=user_email, error=str(e))
        return True, ""

    status = state["status"]
    if status in ("expired", "cancelled", "no_subscription"):
        return False, "Your subscription has expired. Please subscribe to continue."
    if not state["can_use_ai"]:
        return False, "You have used all your AI credits for this period. Purchase add-ons to continue."
    return True, ""


def check_db_row_limit(user_email: str, rows: int = 1) -> Tuple[bool, str]:
    try:
        state = get_user_subscription(user_email)
    except Exception as e:
        log.warning("Billing DB check failed — allowing", user=user_email, error=str(e))
        return True, ""

    if state["status"] in ("expired", "cancelled", "no_subscription"):
        return False, "Your subscription has expired."
    if state["db_total_available"] < rows:
        return False, "DB row limit reached. Purchase add-ons to continue."
    return True, ""


# ── Subscribe / upgrade ────────────────────────────────────────────────────────

def subscribe_to_plan(user_email: str, plan_id: int):
    """Cancel existing sub and start a new active period on the chosen plan.
    Period length comes from subscription_plans.validity_days (default 30)."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT validity_days FROM subscription_plans WHERE id = %s", (plan_id,))
        row = cur.fetchone()
        validity_days = row["validity_days"] if row else 30

        cur2 = conn.cursor()
        cur2.execute("""
            UPDATE user_subscriptions
            SET status = 'cancelled'
            WHERE user_email = %s AND status IN ('trial', 'active')
        """, (user_email,))

        today      = date.today()
        period_end = today + timedelta(days=validity_days)

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
        log.info("Subscribed to plan", user=user_email, plan_id=plan_id, days=validity_days)
    finally:
        conn.close()


# ── Charge AI usage ────────────────────────────────────────────────────────────

def charge_ai_usage(user_email: str, tokens: int, model: str, endpoint: str = ""):
    """
    Deduct credits using priority order: base quota first, then add-on pool.
    Add-on deductions are recorded in ai_addon_used and deplete addon_purchases FIFO.
    """
    model_key    = (model or "").lower()
    cost_per_1k  = _LLM_COST_PER_1K.get(model_key, _DEFAULT_COST_PER_1K)
    actual_cost  = (tokens / 1000.0) * cost_per_1k
    total_credits = round((actual_cost * _MARKUP) / _CREDIT_VALUE, 2)

    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        # Get current period
        cur.execute("""
            SELECT s.period_start, p.ai_credits AS plan_limit
            FROM user_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.user_email = %s AND s.status IN ('trial','active')
            ORDER BY s.id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        period_start = row["period_start"] if row else date.today()
        plan_limit   = row["plan_limit"]   if row else 0

        # How much base is left?
        cur.execute("""
            SELECT ai_base_used FROM subscription_usage
            WHERE user_email = %s AND period_start = %s
        """, (user_email, period_start))
        u = cur.fetchone()
        base_used      = u["ai_base_used"] if u else 0
        base_remaining = max(0, plan_limit - base_used)

        from_base  = min(total_credits, base_remaining)
        from_addon = max(0, total_credits - from_base)

        cur2 = conn.cursor()

        # Deduct from base
        if from_base > 0:
            cur2.execute("""
                INSERT INTO subscription_usage (user_email, period_start, ai_base_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE ai_base_used = ai_base_used + %s
            """, (user_email, period_start, from_base, from_base))

        # Deduct from add-on FIFO
        if from_addon > 0:
            cur.execute("""
                SELECT id, units_remaining FROM addon_purchases
                WHERE user_email = %s AND addon_type = 'ai_credits' AND units_remaining > 0
                ORDER BY purchased_at ASC
            """, (user_email,))
            remaining = from_addon
            for ap in cur.fetchall():
                if remaining <= 0:
                    break
                deduct = min(remaining, ap["units_remaining"])
                cur2.execute("""
                    UPDATE addon_purchases SET units_remaining = units_remaining - %s WHERE id = %s
                """, (deduct, ap["id"]))
                remaining -= deduct

            cur2.execute("""
                INSERT INTO subscription_usage (user_email, period_start, ai_addon_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE ai_addon_used = ai_addon_used + %s
            """, (user_email, period_start, from_addon, from_addon))

        # Log the call
        cur2.execute("""
            INSERT INTO llm_usage_log (user_email, tokens, model, endpoint, credits_charged, actual_cost)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_email, tokens, model, endpoint, total_credits, actual_cost))

        conn.commit()
        log.info("AI usage charged", user=user_email, tokens=tokens,
                 credits=total_credits, from_base=from_base, from_addon=from_addon)
    finally:
        conn.close()


# ── Charge DB rows ─────────────────────────────────────────────────────────────

def charge_db_rows(user_email: str, rows: int):
    """Same priority: base first, then add-on."""
    conn = _get_conn()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT s.period_start, p.db_rows AS plan_limit
            FROM user_subscriptions s
            JOIN subscription_plans p ON p.id = s.plan_id
            WHERE s.user_email = %s AND s.status IN ('trial','active')
            ORDER BY s.id DESC LIMIT 1
        """, (user_email,))
        row = cur.fetchone()
        period_start = row["period_start"] if row else date.today()
        plan_limit   = row["plan_limit"]   if row else 0

        cur.execute("""
            SELECT db_base_used FROM subscription_usage
            WHERE user_email = %s AND period_start = %s
        """, (user_email, period_start))
        u = cur.fetchone()
        base_used      = u["db_base_used"] if u else 0
        base_remaining = max(0, plan_limit - base_used)

        from_base  = min(rows, base_remaining)
        from_addon = max(0, rows - from_base)

        cur2 = conn.cursor()

        if from_base > 0:
            cur2.execute("""
                INSERT INTO subscription_usage (user_email, period_start, db_base_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE db_base_used = db_base_used + %s
            """, (user_email, period_start, from_base, from_base))

        if from_addon > 0:
            cur.execute("""
                SELECT id, units_remaining FROM addon_purchases
                WHERE user_email = %s AND addon_type = 'db_rows' AND units_remaining > 0
                ORDER BY purchased_at ASC
            """, (user_email,))
            remaining = from_addon
            for ap in cur.fetchall():
                if remaining <= 0:
                    break
                deduct = min(remaining, ap["units_remaining"])
                cur2.execute("""
                    UPDATE addon_purchases SET units_remaining = units_remaining - %s WHERE id = %s
                """, (deduct, ap["id"]))
                remaining -= deduct

            cur2.execute("""
                INSERT INTO subscription_usage (user_email, period_start, db_addon_used)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE db_addon_used = db_addon_used + %s
            """, (user_email, period_start, from_addon, from_addon))

        conn.commit()
    finally:
        conn.close()


# ── Add-ons ────────────────────────────────────────────────────────────────────

def purchase_addon(user_email: str, addon_type: str, qty: int):
    """qty = number of $1 packs. Add-on balance rolls over month to month."""
    if addon_type not in _ADDON_PRICE:
        raise ValueError(f"Unknown addon_type: {addon_type}")
    pack  = _ADDON_PRICE[addon_type]
    units = pack["units"] * qty

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


def get_addon_pricing() -> Dict:
    return {
        "ai_credits": _ADDON_PRICE["ai_credits"],
        "db_rows":    _ADDON_PRICE["db_rows"],
        "packages":   ADDON_PACKAGES,
    }


# ── LLM usage history ──────────────────────────────────────────────────────────

def get_llm_usage_history(user_email: str, limit: int = 100) -> List[Dict]:
    """Return recent calls. Model name is omitted from the response."""
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
            r["actual_cost"]     = float(r["actual_cost"])
            r["credits_charged"] = float(r["credits_charged"])
        return rows
    finally:
        conn.close()
