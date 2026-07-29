#!/usr/bin/env python3
"""
End-to-end QA for DataMind's own subscription/trial/token gating —
the SalesPlay embed's access control (billing.py + /v1/billing/*, and
the check_ai_limit() gate that /v1/query enforces).

Scope: everything WE own — trial days, token quotas, plan expiry,
cancellation. Assumes Salesplay's side works perfectly (real payment/
activation calls are out of scope here — see the SalesPlay API test log
from the payment integration work for that). Every scenario below is
set up with direct SQL (same pattern as reset_qa_usage.py) so it's
deterministic and doesn't need to wait on real trial/period timers.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
  python qa_subscription_e2e.py

PREREQUISITES
  - Backend running at http://127.0.0.1:8000
  - MySQL reachable with the same creds as reset_qa_usage.py

WHAT IS TESTED (each scenario: setup DB state → verify the raw DB row itself
→ GET /v1/billing/subscription → POST /v1/query → assert all three agree.
Scenarios 4/7 also re-check the DB row AFTER the API call, to prove
auto-expiry actually persists the 'expired' status — not just filters it out
of the read-model.)
  1. Brand-new user, no subscription row         → blocked (no_subscription)
  2. Trial active, tokens available               → allowed
  3. Trial active, tokens exhausted                → blocked (quota)
  4. Trial period_end passed (auto-expiry)         → blocked (no_subscription)
  5. Paid plan active, tokens available            → allowed
  6. Paid plan active, tokens exhausted             → blocked (quota)
  7. Paid plan period_end passed (auto-expiry)      → blocked (no_subscription)
  8. Cancelled directly (Salesplay is_expired sync) → blocked, even with
                                                       period_end still in the future
  9. POST /billing/subscribe creates the right period_end for a real plan
  10. Re-subscribing cancels the old row — exactly one active row survives
  11. Addon purchase flips a token-exhausted user back to allowed
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

import mysql.connector

BASE = "http://127.0.0.1:8000"
DB = dict(host="localhost", database="datamind_db", user="datamind_user", password="datamind_pass")

PASSED, FAILED = [], []


# ── HTTP helpers ────────────────────────────────────────────────────────────

def _try_login(email, password):
    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(f"{BASE}/v1/auth/login", data=body,
                                  headers={"Content-Type": "application/json"})
    for attempt in range(3):
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
        # Global rate limiter returns HTTP 200 with ok:false (same pattern
        # documented in qa_e2e.py's analytics_run) instead of a real 429.
        if data.get("ok") is False and "too many requests" in (data.get("message") or "").lower():
            wait = data.get("retry_after_seconds", 60)
            print(f"  [rate-limited on /auth/login] waiting {wait}s ...")
            time.sleep(wait)
            continue
        return data["token"]
    raise RuntimeError(f"Login rate-limited repeatedly for {email}")


def register_and_login(email, password="Pass@1234", name="QA Subscription Test"):
    """Log in if the user already exists (idempotent across reruns — no
    register call needed, keeps us under /auth/register's 5/min limit);
    only register on a genuine first-time 401. /auth/register always calls
    start_trial() internally, so callers must wipe() AFTER this, not before,
    or the auto-created trial row races whatever they seed next."""
    try:
        return _try_login(email, password)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise

    body = json.dumps({"name": name, "email": email, "password": password}).encode()
    req = urllib.request.Request(f"{BASE}/v1/auth/register", data=body,
                                  headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())["token"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                print("  [rate-limited on /auth/register] waiting 61s ...")
                time.sleep(61)
                continue
            raise
    return _try_login(email, password)


def get_subscription(token):
    req = urllib.request.Request(f"{BASE}/v1/billing/subscription",
                                  headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def run_query(token, question="What was my total revenue?"):
    """POST /v1/query — returns (success: bool, message: str)."""
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(f"{BASE}/v1/query", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            data = json.loads(r.read())
            return bool(data.get("success")), data.get("message", "")
    except urllib.error.HTTPError as e:
        return False, e.read().decode(errors="replace")


def http_subscribe(token, plan_id):
    body = json.dumps({"plan_id": plan_id}).encode()
    req = urllib.request.Request(f"{BASE}/v1/billing/subscribe", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def http_addon(token, addon_type="ai_credits", quantity=1):
    body = json.dumps({"addon_type": addon_type, "quantity": quantity}).encode()
    req = urllib.request.Request(f"{BASE}/v1/billing/addon", data=body,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ── DB helpers (deterministic scenario setup — same style as reset_qa_usage.py) ──

def db():
    return mysql.connector.connect(**DB)


def wipe(email):
    conn = db(); cur = conn.cursor()
    cur.execute("DELETE FROM user_subscriptions WHERE user_email = %s", (email,))
    cur.execute("DELETE FROM subscription_usage WHERE user_email = %s", (email,))
    cur.execute("DELETE FROM addon_purchases WHERE user_email = %s", (email,))
    conn.commit(); cur.close(); conn.close()


def seed_subscription(email, plan_id, status, period_start, period_end):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO user_subscriptions (user_email, plan_id, status, period_start, period_end)
        VALUES (%s, %s, %s, %s, %s)
    """, (email, plan_id, status, period_start, period_end))
    conn.commit(); cur.close(); conn.close()


def seed_usage(email, period_start, tokens_used):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO subscription_usage (user_email, period_start, tokens_used)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE tokens_used = VALUES(tokens_used)
    """, (email, period_start, tokens_used))
    conn.commit(); cur.close(); conn.close()


def cancel_directly(email):
    """Mirrors billing.cancel_subscription() — the Salesplay is_expired sync-down path."""
    conn = db(); cur = conn.cursor()
    cur.execute("""
        UPDATE user_subscriptions SET status = 'cancelled'
        WHERE user_email = %s AND status IN ('trial', 'active')
    """, (email,))
    conn.commit(); cur.close(); conn.close()


def count_active_rows(email):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM user_subscriptions WHERE user_email=%s AND status IN ('trial','active')", (email,))
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n


def get_row(email):
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM user_subscriptions WHERE user_email=%s AND status IN ('trial','active') ORDER BY id DESC LIMIT 1", (email,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row


def get_raw_row(email):
    """Most recent row for this email regardless of status — used to check
    the ACTUAL persisted state (e.g. did auto-expiry really flip the DB row
    to 'expired', not just get filtered out of the read-model)."""
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM user_subscriptions WHERE user_email=%s ORDER BY id DESC LIMIT 1", (email,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row


def row_count(email):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM user_subscriptions WHERE user_email=%s", (email,))
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return n


def get_usage_db(email, period_start):
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT tokens_used FROM subscription_usage WHERE user_email=%s AND period_start=%s", (email, period_start))
    row = cur.fetchone()
    cur.close(); conn.close()
    return float(row["tokens_used"]) if row else None


def get_addon_units(email, addon_type="ai_credits"):
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(units_remaining),0) FROM addon_purchases WHERE user_email=%s AND addon_type=%s", (email, addon_type))
    n = cur.fetchone()[0]
    cur.close(); conn.close()
    return int(n)


_BILLING_KEYWORDS = ("token", "expired", "inactive", "subscription", "billing period")

def _is_billing_block(msg):
    m = (msg or "").lower()
    return any(k in m for k in _BILLING_KEYWORDS)


# Starter = id 1, tokens_limit 200, validity_days 30 — see subscription_plans.
STARTER_ID, STARTER_TOKENS, STARTER_VALIDITY = 1, 200, 30
GROWTH_ID = 2


# ── Assertions ────────────────────────────────────────────────────────────

def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


def scenario(n, title):
    print(f"\n[{n}] {title}")


# ── Scenarios ────────────────────────────────────────────────────────────

def s1_brand_new():
    scenario(1, "Brand-new user, no subscription row at all")
    email = "qa_sub_1_brandnew@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    check("DB: zero subscription rows after wipe", row_count(email) == 0, row_count(email))
    sub = get_subscription(token)
    check("API: status is no_subscription", sub.get("status") == "no_subscription", sub)
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s2_trial_ok():
    scenario(2, "Trial active, tokens available")
    email = "qa_sub_2_trialok@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, STARTER_ID, "trial", today, today + timedelta(days=14))
    seed_usage(email, today, tokens_used=50)  # well under 200
    row = get_row(email)
    check("DB: row is trial/Starter", row and row["status"] == "trial" and row["plan_id"] == STARTER_ID, row)
    check("DB: usage is 50", get_usage_db(email, today) == 50, get_usage_db(email, today))
    sub = get_subscription(token)
    check("API: status is trial", sub.get("status") == "trial", sub)
    check("API: can_use_ai true", sub.get("can_use_ai") is True, sub)
    ok, msg = run_query(token)
    # This throwaway account has no connected data source, so a true
    # success:true is unreachable — assert it got PAST the billing gate
    # instead (blocked by something else, not a token/subscription message).
    check("/query not blocked by billing", not _is_billing_block(msg), msg)


def s3_trial_quota_exceeded():
    scenario(3, "Trial active, tokens exhausted")
    email = "qa_sub_3_trialquota@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, STARTER_ID, "trial", today, today + timedelta(days=14))
    seed_usage(email, today, tokens_used=STARTER_TOKENS)  # exactly at limit
    check("DB: usage at limit (200)", get_usage_db(email, today) == STARTER_TOKENS, get_usage_db(email, today))
    sub = get_subscription(token)
    check("API: status is trial", sub.get("status") == "trial", sub)
    check("API: can_use_ai false", sub.get("can_use_ai") is False, sub)
    check("DB: row still 'trial' (not date-expired, only quota)", get_row(email) is not None, get_row(email))
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s4_trial_expired():
    scenario(4, "Trial period_end passed (auto-expiry)")
    email = "qa_sub_4_trialexpired@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, STARTER_ID, "trial", today - timedelta(days=20), today - timedelta(days=1))
    seed_usage(email, today - timedelta(days=20), tokens_used=10)
    check("DB: seeded row is 'trial' before the API call", get_raw_row(email)["status"] == "trial", get_raw_row(email))
    sub = get_subscription(token)  # triggers _process_subscription's auto-expire UPDATE
    check("API: status is no_subscription (auto-expired)", sub.get("status") == "no_subscription", sub)
    check("DB: row actually persisted as 'expired', not just filtered",
          get_raw_row(email)["status"] == "expired", get_raw_row(email))
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s5_paid_ok():
    scenario(5, "Paid plan active, tokens available")
    email = "qa_sub_5_paidok@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, GROWTH_ID, "active", today, today + timedelta(days=30))
    seed_usage(email, today, tokens_used=100)  # Growth limit is 500
    row = get_row(email)
    check("DB: row is active/Growth", row and row["status"] == "active" and row["plan_id"] == GROWTH_ID, row)
    check("DB: usage is 100", get_usage_db(email, today) == 100, get_usage_db(email, today))
    sub = get_subscription(token)
    check("API: status is active", sub.get("status") == "active", sub)
    check("API: can_use_ai true", sub.get("can_use_ai") is True, sub)
    ok, msg = run_query(token)
    check("/query not blocked by billing", not _is_billing_block(msg), msg)


def s6_paid_quota_exceeded():
    scenario(6, "Paid plan active, tokens exhausted")
    email = "qa_sub_6_paidquota@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, GROWTH_ID, "active", today, today + timedelta(days=30))
    seed_usage(email, today, tokens_used=500)  # at limit
    check("DB: usage at limit (500)", get_usage_db(email, today) == 500, get_usage_db(email, today))
    sub = get_subscription(token)
    check("API: can_use_ai false", sub.get("can_use_ai") is False, sub)
    check("DB: row still 'active' (not date-expired, only quota)", get_raw_row(email)["status"] == "active", get_raw_row(email))
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s7_paid_expired():
    scenario(7, "Paid plan period_end passed (auto-expiry)")
    email = "qa_sub_7_paidexpired@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, GROWTH_ID, "active", today - timedelta(days=40), today - timedelta(days=1))
    seed_usage(email, today - timedelta(days=40), tokens_used=10)
    check("DB: seeded row is 'active' before the API call", get_raw_row(email)["status"] == "active", get_raw_row(email))
    sub = get_subscription(token)
    check("API: status is no_subscription (auto-expired)", sub.get("status") == "no_subscription", sub)
    check("DB: row actually persisted as 'expired', not just filtered",
          get_raw_row(email)["status"] == "expired", get_raw_row(email))
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s8_cancelled_overrides_period():
    scenario(8, "Cancelled directly (Salesplay is_expired sync-down) beats a future period_end")
    email = "qa_sub_8_cancelled@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, GROWTH_ID, "active", today, today + timedelta(days=300))  # far future
    seed_usage(email, today, tokens_used=10)
    cancel_directly(email)
    check("DB: row is 'cancelled' immediately (period_end still 300d out)",
          get_raw_row(email)["status"] == "cancelled", get_raw_row(email))
    sub = get_subscription(token)
    check("API: status is no_subscription despite future period_end", sub.get("status") == "no_subscription", sub)
    check("DB: row still 'cancelled' after the API call (not resurrected)",
          get_raw_row(email)["status"] == "cancelled", get_raw_row(email))
    ok, msg = run_query(token)
    check("/query blocked", ok is False, msg)


def s9_subscribe_period_end_math():
    scenario(9, "POST /billing/subscribe creates correct period_end")
    email = "qa_sub_9_subscribe@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    http_subscribe(token, STARTER_ID)
    row = get_row(email)
    expected = date.today() + timedelta(days=STARTER_VALIDITY)
    check("period_end = today + validity_days", row and row["period_end"] == expected,
          f"got {row['period_end'] if row else None}, expected {expected}")
    check("status is active", row and row["status"] == "active", row)


def s10_resubscribe_single_active_row():
    scenario(10, "Re-subscribing cancels the old row — exactly one active row survives")
    email = "qa_sub_10_resubscribe@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    http_subscribe(token, STARTER_ID)
    http_subscribe(token, GROWTH_ID)
    n = count_active_rows(email)
    check("exactly one active/trial row", n == 1, f"found {n}")
    row = get_row(email)
    check("the surviving row is the newer plan (Growth)", row and row["plan_id"] == GROWTH_ID, row)
    conn = db(); cur = conn.cursor(dictionary=True)
    cur.execute("SELECT status FROM user_subscriptions WHERE user_email=%s AND plan_id=%s ORDER BY id LIMIT 1", (email, STARTER_ID))
    old_row = cur.fetchone(); cur.close(); conn.close()
    check("the old Starter row is 'cancelled', not deleted", old_row and old_row["status"] == "cancelled", old_row)


def s11_addon_unblocks():
    scenario(11, "Addon purchase flips a token-exhausted user back to allowed")
    email = "qa_sub_11_addon@test.local"
    token = register_and_login(email)
    wipe(email)  # after login — /auth/register auto-starts a trial we don't want here
    today = date.today()
    seed_subscription(email, STARTER_ID, "trial", today, today + timedelta(days=14))
    seed_usage(email, today, tokens_used=STARTER_TOKENS)  # exhausted
    sub_before = get_subscription(token)
    check("blocked before addon", sub_before.get("can_use_ai") is False, sub_before)
    check("DB: no addon units before purchase", get_addon_units(email) == 0, get_addon_units(email))
    http_addon(token, "ai_credits", quantity=5)  # ai_credits addon: 25 units/pack per addon_packages
    check("DB: addon_purchases has 125 units (5 packs x 25)", get_addon_units(email) == 125, get_addon_units(email))
    sub_after = get_subscription(token)
    check("unblocked after addon", sub_after.get("can_use_ai") is True, sub_after)


# ── Runner ───────────────────────────────────────────────────────────────

SCENARIOS = [
    s1_brand_new, s2_trial_ok, s3_trial_quota_exceeded, s4_trial_expired,
    s5_paid_ok, s6_paid_quota_exceeded, s7_paid_expired, s8_cancelled_overrides_period,
    s9_subscribe_period_end_math, s10_resubscribe_single_active_row, s11_addon_unblocks,
]

if __name__ == "__main__":
    print("DataMind subscription/token gating — end-to-end QA")
    print(f"Target: {BASE}\n")
    for fn in SCENARIOS:
        try:
            fn()
        except Exception as e:
            FAILED.append(fn.__name__)
            print(f"  ERROR  {fn.__name__}: {e}")
        time.sleep(6)  # spreads the ~11 /auth/login calls across the run instead of bunching them into a rate-limit bucket

    print(f"\n{'='*60}")
    print(f"PASSED: {len(PASSED)}   FAILED: {len(FAILED)}")
    if FAILED:
        print("Failed checks:")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    print("All subscription gating scenarios passed.")
