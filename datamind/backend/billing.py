"""
billing.py
==========
Subscription plans, usage tracking, and add-on purchases for DataMind AI.
All tables live in DataMind's internal DB (same env vars as integrations.py).
"""
import os
import time as _time
import threading as _threading
from typing import Optional, Dict, List, Tuple
from calendar import monthrange as _monthrange
from datetime import date, timedelta
import mysql.connector
from logger import get_logger
from pool import get_internal_conn as _get_conn

log = get_logger(__name__)

# ── Per-request billing cache ──────────────────────────────────────────────────
# get_user_subscription() is called on every compute request (check_ai_limit +
# charge_tokens). It runs 5+ DB queries including a COUNT(*) on integration_records.
# At 1,000 concurrent users that's 5,000+ DB queries just for billing on every tick.
#
# Cache subscription state for 60s per user. Acceptable lag: a user who hits their
# token limit continues for at most 60s before being blocked. Tokens are still written
# correctly (charge_tokens always writes to DB); only the read-side check is cached.
# Cache is busted immediately on subscribe/cancel so plan changes take effect instantly.

_sub_cache: dict = {}
_sub_cache_lock = _threading.Lock()
_SUB_CACHE_TTL = int(os.getenv("SUB_CACHE_TTL", "60"))  # seconds, configurable via .env

def _sub_cache_get(email: str):
    with _sub_cache_lock:
        entry = _sub_cache.get(email)
    if entry:
        result, exp = entry
        if _time.monotonic() < exp:
            return result
        with _sub_cache_lock:
            _sub_cache.pop(email, None)
    return None

def _sub_cache_set(email: str, result: dict):
    with _sub_cache_lock:
        _sub_cache[email] = (result, _time.monotonic() + _SUB_CACHE_TTL)

def invalidate_sub_cache(email: str):
    """Call after subscribe/cancel/plan-change so next request reads fresh data."""
    with _sub_cache_lock:
        _sub_cache.pop(email, None)

# Track consecutive billing-check failures so we can alert when the DB is
# persistently unavailable (fail-open is intentional but should not be silent).
_billing_fail_count = 0
_billing_fail_lock  = _threading.Lock()
_BILLING_FAIL_WARN_EVERY = 10  # log an escalated warning every N consecutive failures

def _record_billing_fail(context: str):
    global _billing_fail_count
    with _billing_fail_lock:
        _billing_fail_count += 1
        count = _billing_fail_count
    if count == 1 or count % _BILLING_FAIL_WARN_EVERY == 0:
        log.error(
            "Billing check FAILING OPEN — quota enforcement is bypassed",
            context=context,
            consecutive_failures=count,
        )

def _reset_billing_fail():
    global _billing_fail_count
    with _billing_fail_lock:
        _billing_fail_count = 0

# NOTE: ADDON_PACKAGES, FEATURE_COST, _PLAN_FEATURE_GATE, and _PLAN_HISTORY below
# are SEED DEFAULTS only — bootstrap_billing_tables() copies them into the
# addon_packages / feature_costs / plan_feature_gates / plan_history_limits
# tables (INSERT IGNORE, so it only happens once). At runtime, _load_billing_config()
# reads the live values from those tables (cached for _BILLING_CONFIG_CACHE_TTL
# seconds), so editing the DB tables directly changes pricing/gating without a
# code change or restart. These dicts remain as the fail-open fallback if the
# DB read ever errors.
ADDON_PACKAGES = {
    "ai_credits": {"units_per_pack": 25,       "price_cents": 100, "label": "25 AI Credits"},
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
    T_feat = _load_billing_config()["feature_costs"].get(operation_type, 0.5)
    return max(round(T_llm + T_db + T_feat, 4), 0.1)


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
                id           INT AUTO_INCREMENT PRIMARY KEY,
                user_email   VARCHAR(255)  NOT NULL,
                period_start DATE          NOT NULL,
                tokens_used  DECIMAL(12,4) NOT NULL DEFAULT 0,
                UNIQUE KEY uq_usage (user_email, period_start)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS addon_purchases (
                id              INT AUTO_INCREMENT PRIMARY KEY,
                user_email      VARCHAR(255) NOT NULL,
                addon_type      ENUM('ai_credits','db_rows') NOT NULL,
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
                provider        VARCHAR(30)   DEFAULT NULL,
                model           VARCHAR(100)  DEFAULT NULL,
                endpoint        VARCHAR(100)  DEFAULT NULL,
                credits_charged DECIMAL(10,4) NOT NULL DEFAULT 0,
                created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_llm_email    (user_email),
                INDEX idx_llm_created  (created_at),
                INDEX idx_llm_provider (provider),
                INDEX idx_llm_model    (model)
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

        # ── Add new columns to existing tables (idempotent — skipped if already present) ──
        for _stmt in [
            "ALTER TABLE llm_usage_log ADD COLUMN provider VARCHAR(30) DEFAULT NULL AFTER tokens",
            "ALTER TABLE llm_usage_log MODIFY COLUMN model VARCHAR(100) DEFAULT NULL",
            "ALTER TABLE llm_usage_log MODIFY COLUMN endpoint VARCHAR(100) DEFAULT NULL",
            "ALTER TABLE llm_usage_log ADD INDEX idx_llm_provider (provider)",
            "ALTER TABLE llm_usage_log ADD INDEX idx_llm_model (model)",
        ]:
            try:
                cur.execute(_stmt)
            except Exception:
                pass  # column/index already exists

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

        # ── Admin-editable pricing/feature config ──────────────────────────────
        # These tables let an admin tune costs, plan gating, history windows, and
        # add-on pricing directly in the DB (see _load_billing_config below).
        # Seeded once from the hardcoded defaults below; INSERT IGNORE means
        # manual edits survive restarts.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feature_costs (
                operation_type VARCHAR(50) PRIMARY KEY,
                token_cost     DECIMAL(10,4) NOT NULL,
                description    TEXT,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        for _op, _cost in FEATURE_COST.items():
            cur.execute("""
                INSERT IGNORE INTO feature_costs (operation_type, token_cost)
                VALUES (%s, %s)
            """, (_op, _cost))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_feature_gates (
                feature   VARCHAR(50) NOT NULL,
                plan_name VARCHAR(50) NOT NULL,
                PRIMARY KEY (feature, plan_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        for _feature, _plans in _PLAN_FEATURE_GATE.items():
            for _plan_name in _plans:
                cur.execute("""
                    INSERT IGNORE INTO plan_feature_gates (feature, plan_name)
                    VALUES (%s, %s)
                """, (_feature, _plan_name))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS plan_history_limits (
                plan_name      VARCHAR(50) PRIMARY KEY,
                history_months INT NOT NULL,
                row_limit      INT NOT NULL,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        for _plan_name, _limits in _PLAN_HISTORY.items():
            cur.execute("""
                INSERT IGNORE INTO plan_history_limits (plan_name, history_months, row_limit)
                VALUES (%s, %s, %s)
            """, (_plan_name, _limits["months"], _limits["row_limit"]))

        cur.execute("""
            CREATE TABLE IF NOT EXISTS addon_packages (
                addon_type     VARCHAR(50) PRIMARY KEY,
                units_per_pack INT NOT NULL,
                price_cents    INT NOT NULL,
                label          VARCHAR(100) NOT NULL,
                updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        for _addon_type, _pkg in ADDON_PACKAGES.items():
            cur.execute("""
                INSERT IGNORE INTO addon_packages (addon_type, units_per_pack, price_cents, label)
                VALUES (%s, %s, %s, %s)
            """, (_addon_type, _pkg["units_per_pack"], _pkg["price_cents"], _pkg["label"]))

        # Personal API keys for Pro users — programmatic access to /v1/* endpoints.
        # Keys are stored in plaintext (prefix dm_live_ makes them identifiable).
        # Each user can have at most one active key at a time; regenerating
        # deactivates the old one (active=0) and inserts a new row.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id           INT           NOT NULL AUTO_INCREMENT,
                user_email   VARCHAR(255)  NOT NULL,
                api_key      VARCHAR(128)  NOT NULL,
                name         VARCHAR(100)  NOT NULL DEFAULT 'Default',
                active       TINYINT(1)   NOT NULL DEFAULT 1,
                created_at   DATETIME     NOT NULL DEFAULT NOW(),
                last_used_at DATETIME,
                PRIMARY KEY (id),
                UNIQUE KEY uq_key (api_key),
                INDEX idx_uak_email  (user_email),
                INDEX idx_uak_active (user_email, active)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # Seed plans  (name, price_usd, price_cents, ai_credits, db_rows, sort_order)
        plans = [
            ("Starter", "5.00",   500,   200,  2_000_000,    1),
            ("Growth",  "10.00", 1000,   500,  5_000_000,    2),
            ("Pro",     "25.00", 2500,  2000, 20_000_000,    3),
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

        # Seed unified token limits — must match _TOKEN_LIMITS dict below
        _TOKEN_LIMITS_SEED = {"Starter": 200.0, "Growth": 500.0, "Pro": 2000.0}
        for _pname, _tlimit in _TOKEN_LIMITS_SEED.items():
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
        invalidate_sub_cache(user_email)
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


def subscribe_to_plan(user_email: str, plan_id: int, period_days: Optional[int] = None):
    """Cancel existing subscription and start a new active one.

    period_days overrides the plan's default validity_days — needed for
    external payment gateways (e.g. Salesplay) that sell the same plan on
    different billing cycles (monthly vs yearly) at different prices; the
    period we grant here must match what was actually paid for there.
    """
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
        days = period_days if period_days else plan["validity_days"]
        period_end = today + timedelta(days=days)
        cur.execute("""
            INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
            VALUES (%s, %s, 'active', %s, %s)
        """, (user_email, plan_id, today, period_end))
        conn.commit()
        invalidate_sub_cache(user_email)  # force fresh read on next request
        log.info("Subscription activated", email=user_email, plan_id=plan_id, period_days=days)
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def cancel_subscription(user_email: str):
    """Immediately cancel any active/trial subscription — used to sync down
    when an external payment gateway (Salesplay) reports the subscription is
    no longer valid there (failed renewal, refund, chargeback), regardless of
    what our own period_end says."""
    conn = _get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE user_subscriptions
            SET status = 'cancelled'
            WHERE user_email = %s AND status IN ('trial', 'active')
        """, (user_email,))
        conn.commit()
        if cur.rowcount:
            invalidate_sub_cache(user_email)
            log.info("Subscription cancelled (external sync)", email=user_email)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_user_subscription(user_email: str) -> Dict:
    """Return full subscription state including usage and add-on balances.

    Result is cached for _SUB_CACHE_TTL seconds to avoid hammering the DB
    on every compute request. Cache is busted by invalidate_sub_cache().
    """
    cached = _sub_cache_get(user_email)
    if cached is not None:
        return cached

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
        _sub_cache_set(user_email, result)
        return result
    finally:
        cur.close()
        conn.close()


# In-memory token limit enforcement — must stay in sync with the bootstrap seed above.
_TOKEN_LIMITS: dict = {"Starter": 200.0, "Growth": 500.0, "Pro": 2000.0}

# Plans that include each gated feature.
_PLAN_FEATURE_GATE: dict = {
    "forecast":          {"Growth", "Pro"},
    "anomaly_detection": {"Growth", "Pro"},
    "external_api":      {"Pro"},           # Partner / External API — Pro only
    "partner_api":       {"Pro"},           # Partner API endpoints — Pro only
    "web_widget":        {"Pro"},           # Embed iframe widget — Pro only
}

# Data-history window per plan: months to look back, and row fallback when no date column.
# Pro's 200 months (~16.7yr) is a practical stand-in for "all historical data since 2010" —
# get_plan_history_limit() only ever produces a concrete cutoff_date, there's no "unlimited" sentinel.
_PLAN_HISTORY: dict = {
    "Starter": {"months": 3,   "row_limit": 3000},
    "Growth":  {"months": 12,  "row_limit": 12000},
    "Pro":     {"months": 200, "row_limit": 50000},
}


# ── Admin-editable pricing/feature config (DB-backed, cached) ─────────────────
# Loaded from feature_costs / plan_feature_gates / plan_history_limits /
# addon_packages. Cached for _BILLING_CONFIG_CACHE_TTL seconds so admins can
# edit these tables directly in the DB and see the change take effect within
# that window without restarting the server.

_billing_config_cache: dict = {}
_billing_config_cache_lock = _threading.Lock()
_BILLING_CONFIG_CACHE_TTL = int(os.getenv("BILLING_CONFIG_CACHE_TTL", "60"))


def invalidate_billing_config_cache():
    """Force the next _load_billing_config() call to re-read from the DB."""
    with _billing_config_cache_lock:
        _billing_config_cache.pop("data", None)


def _load_billing_config() -> dict:
    """Return {feature_costs, plan_feature_gates, plan_history, addon_packages}
    read live from the DB (cached). Falls back to the hardcoded defaults above
    if the DB read fails, so a billing outage never hard-blocks the app."""
    with _billing_config_cache_lock:
        entry = _billing_config_cache.get("data")
    if entry:
        data, exp = entry
        if _time.monotonic() < exp:
            return data

    try:
        conn = _get_conn()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT operation_type, token_cost FROM feature_costs")
            feature_costs = {r["operation_type"]: float(r["token_cost"]) for r in cur.fetchall()}

            cur.execute("SELECT feature, plan_name FROM plan_feature_gates")
            plan_feature_gates: dict = {}
            for r in cur.fetchall():
                plan_feature_gates.setdefault(r["feature"], set()).add(r["plan_name"])

            cur.execute("SELECT plan_name, history_months, row_limit FROM plan_history_limits")
            plan_history = {
                r["plan_name"]: {"months": r["history_months"], "row_limit": r["row_limit"]}
                for r in cur.fetchall()
            }

            cur.execute("SELECT addon_type, units_per_pack, price_cents, label FROM addon_packages")
            addon_packages = {
                r["addon_type"]: {
                    "units_per_pack": r["units_per_pack"],
                    "price_cents": r["price_cents"],
                    "label": r["label"],
                }
                for r in cur.fetchall()
            }
        finally:
            cur.close(); conn.close()

        data = {
            "feature_costs": feature_costs or FEATURE_COST,
            "plan_feature_gates": plan_feature_gates or _PLAN_FEATURE_GATE,
            "plan_history": plan_history or _PLAN_HISTORY,
            "addon_packages": addon_packages or ADDON_PACKAGES,
        }
        with _billing_config_cache_lock:
            _billing_config_cache["data"] = (data, _time.monotonic() + _BILLING_CONFIG_CACHE_TTL)
        return data
    except Exception as e:
        log.warning("_load_billing_config failed, using hardcoded defaults", error=str(e))
        return {
            "feature_costs": FEATURE_COST,
            "plan_feature_gates": _PLAN_FEATURE_GATE,
            "plan_history": _PLAN_HISTORY,
            "addon_packages": ADDON_PACKAGES,
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
            _reset_billing_fail()
            return False, "No active subscription."
        plan_name   = row["plan_name"]
        allowed     = _load_billing_config()["plan_feature_gates"].get(feature)
        if allowed and plan_name not in allowed:
            upgrade_to = sorted(allowed)[0]
            _reset_billing_fail()
            return False, f"Upgrade to {upgrade_to} or above to use this feature."
        _reset_billing_fail()
        return True, ""
    except Exception as e:
        log.warning("check_plan_feature failed open", feature=feature, email=user_email, error=str(e))
        _record_billing_fail(f"check_plan_feature:{feature}")
        return True, ""


def window_start(months: int, today=None):
    """First date covered by a `months`-long history window, counted in CALENDAR
    months rather than 30-day blocks.

    The single source of truth for "how far back can this user see". The old
    `months * 30` approximation refused ~5 days of data a Growth merchant had
    paid for ("same month last year" is 365 days back, not 360), and it drifted
    from the SQL path, which uses MySQL's calendar-correct INTERVAL n MONTH —
    so the same question got a different answer depending on which tool the
    model happened to pick.
    """
    from datetime import date as _date
    today = today or _date.today()
    y, m = today.year, today.month - int(months)
    while m <= 0:
        y, m = y - 1, m + 12
    return today.replace(year=y, month=m,
                         day=min(today.day, _monthrange(y, m)[1]))


def get_plan_history_limit(user_email: str) -> dict:
    """Return the data-history window for the user's current plan.

    Returns a dict with keys:
      months      — how far back to filter date columns
      row_limit   — max rows to return when no date column is available
      cutoff_date — concrete date object (today minus `months` calendar months)
    """
    # Re-use the cached subscription rather than a separate DB query.
    # get_user_subscription() is already called by check_ai_limit() on the same request,
    # so this hits the in-process cache (no DB round-trip).
    try:
        sub = get_user_subscription(user_email)
        plan_name = sub.get("plan_name") or "Starter"
    except Exception:
        plan_name = "Starter"
    plan_history = _load_billing_config()["plan_history"]
    limits = plan_history.get(plan_name)
    if limits is None:
        # A plan name with no configured window is a config bug, not a reason to
        # silently bill someone for Pro and serve them Starter's 3 months — the
        # ONE fallback, and it is logged. Callers must not add their own `or 3`.
        log.warning("No history limits configured for plan, falling back to Starter",
                    email=user_email, plan_name=plan_name)
        limits = plan_history.get("Starter", _PLAN_HISTORY["Starter"])
    return {**limits, "plan_name": plan_name,
            "cutoff_date": window_start(limits["months"])}


def plan_window_start(user_email: str):
    """First date this user's plan covers (calendar-correct). Thin alias over
    get_plan_history_limit for callers that only need the boundary."""
    return get_plan_history_limit(user_email)["cutoff_date"]


def check_ai_limit(user_email: str) -> Tuple[bool, str]:
    """Returns (True, '') if user has tokens remaining, (False, reason) otherwise. Fails open on errors."""
    try:
        sub = get_user_subscription(user_email)
        status = sub.get("status")
        if status in ("expired", "cancelled", "no_subscription"):
            return False, "Your subscription has expired or is inactive."
        tokens_total = sub.get("tokens_total_available", sub.get("tokens_limit", 0))
        tokens_used  = sub.get("tokens_used",  0)
        if tokens_total > 0 and tokens_used >= tokens_total:
            return False, "You've used all your tokens for this billing period."
        return True, ""
    except Exception as e:
        log.warning("check_ai_limit failed open", email=user_email, error=str(e))
        _record_billing_fail("check_ai_limit")
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
        _record_billing_fail("check_db_limit")
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

    Both writes happen in a single connection and transaction — previously this
    used two separate pool borrows which doubled connection pressure per request.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor(dictionary=True)
        # 1. Audit log
        cur.execute("""
            INSERT INTO usage_log (user_email, tokens, operation_type, llm_tokens, rows_charged)
            VALUES (%s, %s, %s, %s, %s)
        """, (user_email, tokens, operation_type, llm_tokens, rows_returned))
        # 2. Increment token balance for active subscription period
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
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        log.error("charge_tokens failed", email=user_email, error=str(e))
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def charge_ai_usage(user_email: str, tokens: int, provider: str,
                    model: str, operation: str = "llm_call"):
    """Log an LLM call to llm_usage_log and forward to charge_tokens for unified tracking.

    provider  — LLM vendor: "openai" | "gemini" | "deepseek"
    model     — specific model ID returned by the API: "gpt-4o-mini", "gemini-2.0-flash", etc.
    operation — logical call type: "classify" | "sql_gen" | "synthesize" | "think" | "report" | ...
                stored in endpoint for cost-per-operation breakdown queries.
    """
    rate = get_ai_credit_rate()
    credits_charged = round(tokens / 1000 * rate, 4)

    # 1. LLM-specific audit log
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO llm_usage_log (user_email, tokens, provider, model, endpoint, credits_charged)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_email, tokens, provider, model, operation, credits_charged))
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
    addon_packages = _load_billing_config()["addon_packages"]
    if addon_type not in addon_packages:
        raise ValueError(f"Unknown addon_type: {addon_type}")

    units = addon_packages[addon_type]["units_per_pack"] * quantity
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
    return _load_billing_config()["addon_packages"]


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
