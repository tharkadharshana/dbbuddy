#!/usr/bin/env python3
"""
qa_salesplay.py - end-to-end QA for the Salesplay web-embed across N backend
instances that share ONE database.

The thing this exists to prove: you can run a paid instance
(SUBSCRIPTION_FREE=false) and a beta instance (SUBSCRIPTION_FREE=true) side by
side against the same database, with disjoint merchant accounts, and later
collapse them onto a single mode without breaking anyone.

It does that for real - it starts the backends, drives the actual HTTP
endpoints the widget calls, and asserts against the actual database rows.
Nothing is mocked.

  Instances are defined in the config file, not hardcoded. Two is the case we
  ship; three or more works the same way - add an entry and give it a port.

Configuration lives in a gitignored JSON file (it holds live Salesplay app
access tokens). Copy qa_salesplay.example.json, fill in your own aats, and
this suite is re-runnable forever with zero code changes.

Usage:
  python scripts/qa_salesplay.py check                 # config + aats + DB, changes nothing
  python scripts/qa_salesplay.py up                    # start every configured instance
  python scripts/qa_salesplay.py status
  python scripts/qa_salesplay.py run                   # full suite (up -> phases -> report)
  python scripts/qa_salesplay.py run --payment         # ALSO do the manual card step
  python scripts/qa_salesplay.py reset                 # delete DataMind rows for the test aats
  python scripts/qa_salesplay.py down

Exit code is 0 only if every check passed.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "scripts" / "qa_salesplay.local.json"

# Salesplay's embed endpoints rate-limit per IP (SALESPLAY_EMBED_RATE_LIMIT,
# default 60/min). The suite makes well under that, but pace anyway so a rerun
# straight after a previous run doesn't trip it.
PACE_SECONDS = 0.25

STANDARD_PLAN = "Standard"


# --------------------------------------------------------------------- results

class Results:
    """Collects PASS/FAIL/SKIP lines and prints one table at the end.

    Checks never raise - a failed assertion is data, not a crash, because the
    whole point is to see every failure in one run rather than the first one.
    """

    def __init__(self):
        self.rows = []

    def record(self, status, scenario, name, detail=""):
        self.rows.append((status, scenario, name, detail))
        icon = {"PASS": "  ok  ", " FAIL ": "FAIL", "SKIP": " skip "}.get(status, status)
        print(f"    [{status:^4}] {name}" + (f"  -- {detail}" if detail else ""))

    def check(self, scenario, name, ok, detail=""):
        self.record("PASS" if ok else "FAIL", scenario, name, detail)
        return ok

    def skip(self, scenario, name, why):
        self.record("SKIP", scenario, name, why)

    def failed(self):
        return [r for r in self.rows if r[0] == "FAIL"]

    def report(self):
        print("\n" + "=" * 78)
        print("QA SUMMARY")
        print("=" * 78)
        width = max((len(r[1]) for r in self.rows), default=10)
        for status, scenario, name, detail in self.rows:
            line = f"  {status:<4}  {scenario:<{width}}  {name}"
            if detail:
                line += f"  ({detail})"
            print(line)
        n_pass = sum(1 for r in self.rows if r[0] == "PASS")
        n_fail = len(self.failed())
        n_skip = sum(1 for r in self.rows if r[0] == "SKIP")
        print("-" * 78)
        print(f"  {n_pass} passed, {n_fail} failed, {n_skip} skipped")
        print("=" * 78)
        return n_fail == 0


# ------------------------------------------------------------------------ http

def http(method, url, body=None, token=None, timeout=60):
    """Returns (status_code, parsed_json_or_text). Never raises on HTTP errors -
    a 403 is frequently the expected result here, not a failure."""
    time.sleep(PACE_SECONDS)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    # Salesplay sits behind Cloudflare, which rejects urllib's default
    # "Python-urllib/x.y" agent with `error code: 1010` before the request ever
    # reaches their app. Match the backend's own client so the direct
    # profile lookups behave identically to what the server sends.
    req.add_header("User-Agent", "python-requests/2.32.3")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:
        return 0, str(e)


# -------------------------------------------------------------------- instance

class Instance:
    """One backend process. Its SUBSCRIPTION_FREE comes from the environment,
    which beats the value in .env because start.py calls load_dotenv() with the
    default override=False - so the repo's .env is never edited by this suite."""

    def __init__(self, spec, cfg):
        self.name = spec["name"]
        self.port = int(spec["port"])
        self.subscription_free = bool(spec["subscription_free"])
        self.label = spec.get("label", self.name)
        self.backend_dir = ROOT / cfg.get("backend_dir", "datamind/backend")
        self.python = cfg.get("python", sys.executable)
        self.proc = None
        self.log_path = ROOT / "scripts" / f".qa_instance_{self.name}.log"

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def is_up(self):
        code, _ = http("GET", f"{self.base}/docs", timeout=3)
        return code == 200

    def start(self, subscription_free=None):
        if subscription_free is not None:
            self.subscription_free = subscription_free
        # Never adopt a backend we did not launch. Its SUBSCRIPTION_FREE was set
        # by whoever started it, and every assertion in this suite depends on
        # knowing that value - so clear the port and start our own.
        if self.is_up():
            print(f"  [{self.name}] port {self.port} busy - stopping the "
                  f"existing backend so the flag is known")
            self.stop()
        env = dict(os.environ)
        env["SUBSCRIPTION_FREE"] = "true" if self.subscription_free else "false"
        env["UVICORN_PORT"] = str(self.port)
        env["UVICORN_HOST"] = "127.0.0.1"
        log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [self.python, "start.py"], cwd=self.backend_dir, env=env,
            stdout=log, stderr=subprocess.STDOUT,
        )
        print(f"  [{self.name}] starting on {self.port} "
              f"(SUBSCRIPTION_FREE={env['SUBSCRIPTION_FREE']}) pid={self.proc.pid}")
        self.wait_up()

    def wait_up(self, timeout=90):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_up():
                print(f"  [{self.name}] up")
                return True
            time.sleep(1)
        raise SystemExit(f"ERROR: instance {self.name} did not come up on {self.port}. "
                         f"See {self.log_path}")

    def stop(self):
        """Stop the instance and make sure the port is actually free.

        uvicorn spawns a worker child. Killing the parent alone orphans that
        child, which keeps holding the port - so a following start() sees the
        port busy, silently 'reuses' a backend running the OLD flag value, and
        every flip assertion after that is a lie. Kill the tree, then verify.
        """
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True)
            self.proc = None
        # Belt and braces: anything still listening on our port dies too,
        # including an orphan from an earlier run this object never owned.
        deadline = time.time() + 15
        while time.time() < deadline:
            if not self.is_up():
                print(f"  [{self.name}] stopped")
                return
            self._kill_port_owner()
            time.sleep(1)
        raise SystemExit(f"ERROR: port {self.port} still in use after stopping "
                         f"instance {self.name}; refusing to continue with a "
                         f"backend whose SUBSCRIPTION_FREE is unknown.")

    def _kill_port_owner(self):
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {self.port} -State Listen "
             f"-ErrorAction SilentlyContinue | ForEach-Object "
             f"{{ taskkill /PID $_.OwningProcess /T /F }}"],
            capture_output=True,
        )

    def restart(self, subscription_free=None):
        self.stop()
        time.sleep(2)
        self.start(subscription_free)

    def context(self, partner_key):
        # The query parameter is `pk`, not `partner_key` - see get_embed_context.
        return http("GET", f"{self.base}/embed/context?pk={partner_key}")


# -------------------------------------------------------------------------- db

def db_connect(backend_dir):
    """Connect with the backend's own .env credentials. Imported lazily so
    `check` can report a helpful error instead of an ImportError traceback."""
    try:
        import mysql.connector
        from dotenv import dotenv_values
    except ImportError as e:
        raise SystemExit(f"ERROR: missing dependency ({e}). "
                         f"Run this with the backend's interpreter.")
    env = dotenv_values(backend_dir / ".env")
    return mysql.connector.connect(
        host=env.get("DB_HOST"), port=int(env.get("DB_PORT") or 3306),
        database=env.get("DB_NAME"), user=env.get("DB_USER"),
        password=env.get("DB_PASSWORD"),
    )


def db_all(conn, sql, params=()):
    cur = conn.cursor(dictionary=True)
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def db_one(conn, sql, params=()):
    rows = db_all(conn, sql, params)
    return rows[0] if rows else None


def db_exec(conn, sql, params=()):
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    n = cur.rowcount
    cur.close()
    return n


def subscription_row(conn, email):
    return db_one(conn, """
        SELECT us.status, us.period_start, us.period_end,
               sp.name AS plan_name, sp.tokens_limit
        FROM user_subscriptions us
        JOIN subscription_plans sp ON sp.id = us.plan_id
        WHERE us.user_email = %s
        ORDER BY us.id DESC LIMIT 1
    """, (email,))


def force_expire(conn, email):
    """Move period_end into the past, then report whether the row IS expired.

    Returns the resulting period_end rather than a rowcount: re-running the
    suite is normal, and a row that already expired updates zero rows even
    though it is in exactly the state we wanted. Asserting on rowcount made a
    clean rerun look like a failure.

    _process_subscription relabels the row 'expired' on the next read, so
    there is nothing to wait for and no restart needed.
    """
    yesterday = date.today() - timedelta(days=1)
    db_exec(conn, """
        UPDATE user_subscriptions SET period_end = %s
        WHERE user_email = %s AND status IN ('trial', 'active', 'expired')
          AND period_end > %s
    """, (yesterday, email, yesterday))
    row = subscription_row(conn, email)
    return row["period_end"] if row else None


# Order matters: children before parents.
RESET_TABLES = [
    ("subscription_usage", "user_email"),
    ("usage_log", "user_email"),
    ("llm_usage_log", "user_email"),
    ("addon_purchases", "user_email"),
    ("user_subscriptions", "user_email"),
    ("user_api_keys", "user_email"),
    ("integration_records", "user_email"),
    ("user_integrations", "user_email"),
    ("conversations", "user_email"),
    ("widget_feedback", "user_email"),
    ("users", "email"),
]


def reset_accounts(conn, emails):
    """Delete every DataMind row for these merchants so the same aats can be
    reused for a fresh run. The Salesplay accounts are untouched.

    QA-ONLY. This is not, and must never become, an operational procedure.
    Production never deletes a merchant: a trial user moves into the
    subscription flow and their history stays intact - which is the path the
    FLIP scenarios exercise. This exists solely because a fresh-onboarding
    test needs a merchant who has never onboarded, and Salesplay aats are
    scarce. It only ever touches emails resolved from the configured aats.

    The rest of the suite is idempotent, so a rerun does NOT need this.
    """
    total = {}
    for table, col in RESET_TABLES:
        try:
            n = db_exec(conn, f"DELETE FROM {table} WHERE {col} IN "
                              f"({','.join(['%s'] * len(emails))})", emails)
            if n:
                total[table] = n
        except Exception as e:
            # A table that doesn't exist in this schema is not fatal - the
            # backend creates tables lazily and older DBs lag the code.
            print(f"    (skip {table}: {e})")
    return total


# ------------------------------------------------------------------- scenarios

def preview_body(pk, aat):
    """Minimum body that passes SalesplaySubscriptionPreviewRequest validation.

    It has to validate: FastAPI returns 422 before the endpoint runs, so an
    incomplete body never reaches _reject_if_subscription_free and the 403 we
    are testing for would be invisible.
    """
    return {"partner_key": pk, "aat": aat, "subscription_type": 1,
            "product_code": "QA_PROBE"}


def payment_body(pk, aat):
    """Same reasoning as preview_body. Only ever sent where a 403 is expected -
    on a paid instance this would attempt a real charge."""
    return {"partner_key": pk, "aat": aat, "subscription_type": "1",
            "subscription_product_code": "QA_PROBE", "internal_period_days": 30}


def onboard(inst, partner_key, aat, attempts=3):
    """Returns (status, token, email). Onboarding deliberately does NOT start a
    trial - see salesplay_onboard's docstring - so the account lands with no
    subscription row at all, which is exactly the state most scenarios need.

    Retries on 502/connection errors. The backend proxies to Salesplay over the
    public internet and kicks off a data sync during boot, so the first calls
    after a start can lose a DNS lookup. A transient network blip is not a
    finding about this branch, and failing the whole suite on one would bury
    the findings that matter.
    """
    for i in range(attempts):
        code, body = http("POST", f"{inst.base}/embed/salesplay/onboard",
                          {"partner_key": partner_key, "aat": aat})
        if code == 200 and isinstance(body, dict):
            user = body.get("user") or {}
            return code, body.get("token"), (user.get("email") or "").strip().lower()
        if code not in (0, 502, 504) or i == attempts - 1:
            return code, None, None
        print(f"       (transient HTTP {code} from Salesplay, retrying {i + 2}/{attempts})")
        time.sleep(5)
    return code, None, None


def profile_email(cfg, aat):
    """Ask Salesplay who this aat belongs to, without touching DataMind."""
    from dotenv import dotenv_values
    env = dotenv_values(ROOT / cfg.get("backend_dir", "datamind/backend") / ".env")
    base = env.get("SALESPLAY_EMBED_PROXY_BASE") or env.get("SALESPLAY_BASE_URL")
    code, body = http("GET", f"{base}/profile", token=aat)
    if code != 200 or not isinstance(body, dict):
        return None
    raw = body.get("user") or body.get("data") or body
    return (raw.get("email") or "").strip().lower() or None


def scenario_beta_fresh(r, inst, cfg, aat, tag):
    """Fresh merchant on a beta instance: onboards fine, but every route that
    could take money must refuse."""
    print(f"\n  -- {tag}: fresh onboard on instance {inst.name} (beta)")
    pk = cfg["partner_key"]

    code, body = inst.context(pk)
    r.check(tag, "context reports subscription_free=true",
            code == 200 and body.get("subscription_free") is True,
            f"got {body.get('subscription_free') if isinstance(body, dict) else code}")

    code, token, email = onboard(inst, pk, aat)
    if not r.check(tag, "onboard succeeds", code == 200 and bool(token), f"HTTP {code}"):
        return None
    print(f"       merchant = {email}")

    # Stale-iframe simulation: the widget hides these, so a direct call is the
    # only way they can be reached in free mode. They must still refuse.
    code, _ = http("POST", f"{inst.base}/v1/billing/subscribe", {"plan_id": 1}, token=token)
    r.check(tag, "POST /v1/billing/subscribe is refused", code == 403, f"HTTP {code}")

    code, _ = http("POST", f"{inst.base}/embed/salesplay/subscription/preview",
                   preview_body(pk, aat), token=token)
    r.check(tag, "Salesplay preview is refused", code == 403, f"HTTP {code}")

    code, _ = http("POST", f"{inst.base}/embed/salesplay/subscription/payment",
                   payment_body(pk, aat), token=token)
    r.check(tag, "Salesplay payment is refused", code == 403, f"HTTP {code}")
    return email, token


def scenario_trial(r, inst, cfg, conn, aat, tag, expect_free,
                   accept_states=("trial",)):
    """Merchant who starts a trial. Used on both instance types."""
    mode = "beta" if expect_free else "paid"
    print(f"\n  -- {tag}: onboard + start trial on instance {inst.name} ({mode})")
    pk = cfg["partner_key"]

    code, token, email = onboard(inst, pk, aat)
    if not r.check(tag, "onboard succeeds", code == 200 and bool(token), f"HTTP {code}"):
        return None
    print(f"       merchant = {email}")

    code, _ = http("POST", f"{inst.base}/embed/salesplay/start-trial", {}, token=token)
    r.check(tag, "start-trial succeeds", code == 200, f"HTTP {code}")

    row = subscription_row(conn, email)
    # 'expired' is accepted because a rerun finds the row this suite expired on
    # a previous pass; start_trial is a no-op once any subscription row exists.
    r.check(tag, "DB row is on Standard, in a trial-derived state",
            bool(row) and row["status"] in accept_states
            and row["plan_name"] == STANDARD_PLAN,
            f"{row['status']}/{row['plan_name']}" if row else "no row")

    code, sub = http("GET", f"{inst.base}/v1/billing/subscription", token=token)
    r.check(tag, "API reports subscription_free correctly",
            code == 200 and sub.get("subscription_free") is expect_free,
            f"got {sub.get('subscription_free') if isinstance(sub, dict) else code}")
    return email, token


def scenario_expired(r, inst, cfg, conn, aat, tag, expect_free):
    """The day-14 case: a trial that has run out. On a beta instance this is the
    dead end (no payment route exists); on a paid instance it must be able to pay."""
    got = scenario_trial(r, inst, cfg, conn, aat, tag, expect_free,
                         accept_states=("trial", "expired"))
    if not got:
        return None
    email, token = got

    period_end = force_expire(conn, email)
    r.check(tag, "period_end is in the past",
            bool(period_end) and period_end < date.today(), f"period_end={period_end}")

    code, sub = http("GET", f"{inst.base}/v1/billing/subscription", token=token)
    r.check(tag, "subscription reads back as expired",
            code == 200 and sub.get("status") == "expired",
            f"status={sub.get('status') if isinstance(sub, dict) else code}")
    r.check(tag, "access is revoked",
            code == 200 and sub.get("can_use_ai") is False,
            f"can_use_ai={sub.get('can_use_ai') if isinstance(sub, dict) else '?'}")
    return email, token


def scenario_paid_fresh(r, inst, cfg, aat, tag):
    """Fresh merchant on the paid instance. Also covers the 401 fix: the
    consent screen's 'Explore plans' fetches Salesplay pricing with only
    partner_key + aat, before any DataMind account exists."""
    print(f"\n  -- {tag}: pre-account plan preview + fresh onboard on instance {inst.name} (paid)")
    pk = cfg["partner_key"]

    code, body = inst.context(pk)
    r.check(tag, "context reports subscription_free=false",
            code == 200 and body.get("subscription_free") is False,
            f"got {body.get('subscription_free') if isinstance(body, dict) else code}")

    # THE 401 REGRESSION TEST. No Authorization header on purpose.
    #
    # Asserting "not 401" rather than "200" on purpose. The fix is about auth:
    # before it, this returned 401 without ever reaching Salesplay. Anything
    # other than 401 proves the dependency no longer blocks an anonymous
    # caller. Whether Salesplay itself answers is a separate matter - on a
    # predev environment that route can be absent, which surfaces as 502, and
    # that must not be reported as an auth regression.
    code, _ = http("GET",
                   f"{inst.base}/embed/salesplay/subscription/info"
                   f"?partner_key={pk}&aat={aat}")
    r.check(tag, "subscription/info is NOT 401 without auth (the 401 fix)",
            code != 401,
            f"HTTP {code}" + (" - reached Salesplay, upstream unavailable"
                              if code == 502 else ""))

    code, token, email = onboard(inst, pk, aat)
    if not r.check(tag, "onboard succeeds", code == 200 and bool(token), f"HTTP {code}"):
        return None
    print(f"       merchant = {email}")

    code, _ = http("GET", f"{inst.base}/embed/salesplay/subscription/info"
                          f"?partner_key={pk}&aat={aat}", token=token)
    r.check(tag, "subscription/info behaves the same WITH auth", code != 401,
            f"HTTP {code}")
    return email, token


def scenario_payment(r, inst, cfg, conn, aat, tag, skip):
    """The plan-resolution regression: a completed payment must activate
    Standard (25,000 tokens), never the retired Starter (200). This one needs a
    real card on Salesplay's side, so it pauses for a human."""
    print(f"\n  -- {tag}: real payment on instance {inst.name} (paid)")
    pk = cfg["partner_key"]

    code, token, email = onboard(inst, pk, aat)
    if not r.check(tag, "onboard succeeds", code == 200 and bool(token), f"HTTP {code}"):
        return None
    print(f"       merchant = {email}")

    if skip:
        r.skip(tag, "plan resolves to Standard after payment", "not requested; pass --payment")
        return email, token

    print("\n" + "!" * 74)
    print(f"  MANUAL STEP - complete a Salesplay payment for:")
    print(f"    merchant : {email}")
    print(f"    widget   : http://localhost:5173/src/embed/embed.html?pk={pk}")
    print(f"    backend  : {inst.base}  (SUBSCRIPTION_FREE=false)")
    print("  This spends real money unless you are on a Salesplay sandbox merchant.")
    print("!" * 74)
    try:
        input("  Press Enter once the payment has gone through (Ctrl-C to abort): ")
    except (EOFError, KeyboardInterrupt):
        r.skip(tag, "plan resolves to Standard after payment", "no TTY / aborted")
        return email, token

    row = subscription_row(conn, email)
    r.check(tag, "plan resolves to Standard, not Starter",
            bool(row) and row["plan_name"] == STANDARD_PLAN,
            f"got {row['plan_name']}" if row else "no row")
    r.check(tag, "token limit is 25000, not 200",
            bool(row) and float(row["tokens_limit"]) == 25000.0,
            f"got {row['tokens_limit']}" if row else "no row")
    r.check(tag, "subscription is active",
            bool(row) and row["status"] == "active",
            f"got {row['status']}" if row else "no row")
    return email, token


# ---------------------------------------------------------------- infra checks

def check_shared_db(r, instances, conn, partner_key):
    """The claim under test: N backends, one database, all live at once."""
    print("\n  -- INFRA: concurrent instances on one database")
    up = [i for i in instances if i.is_up()]
    r.check("INFRA", f"all {len(instances)} instances up simultaneously",
            len(up) == len(instances), f"{len(up)} up")

    # The bootstrap that the rejected two-branch plan died on. Same code on both
    # sides means both write identical plan rows, so Standard must survive.
    row = db_one(conn, "SELECT name, is_active, tokens_limit, trial_days "
                       "FROM subscription_plans WHERE name = %s", (STANDARD_PLAN,))
    r.check("INFRA", "Standard plan survives concurrent bootstraps",
            bool(row) and row["is_active"] == 1 and float(row["tokens_limit"]) == 25000.0,
            f"{row}" if row else "missing")

    # Each instance must serve its OWN flag value, not whichever booted last.
    served = {}
    for inst in up:
        _, body = inst.context(partner_key)
        served[inst.name] = body.get("subscription_free") if isinstance(body, dict) else None
    expected = {inst.name: inst.subscription_free for inst in up}
    r.check("INFRA", "each instance serves its own SUBSCRIPTION_FREE",
            served == expected, f"served={served} expected={expected}")


def check_scheduler_lock(r, instances, conn):
    """GET_LOCK('datamind_scheduler') is MySQL-server-global, so exactly one
    instance should own the sync scheduler no matter how many are running -
    otherwise every user's integrations get synced once per instance.

    Two independent signals, because they fail differently:
      * how many instances logged that they won the lock (should be 1)
      * whether anyone still holds it (should be yes, for the process lifetime)

    Both must hold. Winning it and then dropping it is as broken as two winners,
    since the next instance to ask will win it too.
    """
    print("\n  -- INFRA: scheduler advisory lock")
    winners = []
    for inst in instances:
        try:
            text = inst.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "acquired DB advisory lock" in text:
            winners.append(inst.name)
    r.check("INFRA", "exactly one instance wins the scheduler lock",
            len(winners) == 1, f"winners = {winners or 'none'}")

    row = db_one(conn, "SELECT IS_USED_LOCK('datamind_scheduler') AS owner")
    owner = row and row["owner"]
    r.check("INFRA", "the scheduler lock is still held",
            owner is not None,
            f"owner connection id = {owner}" if owner else
            "nobody holds it - the lock connection was returned to the pool")


def check_restart_sweep(r, instances, conn):
    """bootstrap_integration_tables clears 'syncing' with no tenant filter, so
    restarting one instance errors in-flight syncs belonging to the other. This
    is the one real shared-database defect - assert it so it is documented
    behaviour rather than a production surprise."""
    print("\n  -- INFRA: cross-instance restart sweep")
    victim = instances[0]
    other = instances[1] if len(instances) > 1 else None
    if not other:
        r.skip("INFRA", "restart sweep crosses instances", "only one instance configured")
        return

    row = db_one(conn, "SELECT id, user_email, status FROM user_integrations LIMIT 1")
    if not row:
        r.skip("INFRA", "restart sweep crosses instances", "no integrations to mark")
        return

    db_exec(conn, "UPDATE user_integrations SET status='syncing' WHERE id=%s", (row["id"],))
    other.restart()
    after = db_one(conn, "SELECT status, last_error FROM user_integrations WHERE id=%s",
                   (row["id"],))
    swept = after and after["status"] == "error"
    r.check("INFRA", "restarting one instance sweeps the other's 'syncing' rows",
            bool(swept),
            "confirmed - known defect, integrations.py:398" if swept else f"got {after}")
    db_exec(conn, "UPDATE user_integrations SET status='active', last_error=NULL "
                  "WHERE id=%s", (row["id"],))


# ------------------------------------------------------------------------ main

def load_config(path):
    if not path.exists():
        sys.exit(f"ERROR: {path} not found.\n"
                 f"       Copy scripts/qa_salesplay.example.json to {path.name} "
                 f"and fill in your aats.")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_emails(cfg):
    """Map each configured aat to the merchant email Salesplay reports."""
    out = {}
    for key, aat in cfg["aats"].items():
        out[key] = profile_email(cfg, aat)
    return out


def cmd_check(cfg, instances, args):
    print("==> Config")
    print(f"  partner_key : {cfg['partner_key']}")
    print(f"  instances   : " + ", ".join(f"{i.name}:{i.port} "
                                          f"(free={i.subscription_free})" for i in instances))
    print(f"  aats        : {len(cfg['aats'])}")

    print("\n==> Salesplay aats")
    emails = resolve_emails(cfg)
    bad = [k for k, v in emails.items() if not v]
    for k, v in emails.items():
        print(f"  {k}: {v or 'UNRESOLVED - expired or invalid'}")
    dupes = len(set(v for v in emails.values() if v)) != len([v for v in emails.values() if v])

    print("\n==> DataMind database")
    conn = db_connect(ROOT / cfg.get("backend_dir", "datamind/backend"))
    live = [v for v in emails.values() if v]
    rows = db_all(conn, f"SELECT email FROM users WHERE email IN "
                        f"({','.join(['%s'] * len(live))})", live) if live else []
    existing = [r["email"] for r in rows]
    print(f"  already onboarded: {existing or 'none - all fresh'}")
    conn.close()

    if bad:
        print(f"\n  WARNING: {len(bad)} aat(s) did not resolve: {bad}")
    if dupes:
        print("\n  WARNING: two aats map to the same merchant email.")
    if existing:
        print("\n  NOTE: run `reset` first for a clean slate.")
    return not bad and not dupes


def cmd_reset(cfg, instances, args):
    emails = [e for e in resolve_emails(cfg).values() if e]
    if not emails:
        sys.exit("ERROR: no aats resolved; nothing to reset.")
    print("==> These DataMind accounts will be DELETED (Salesplay is untouched):")
    for e in emails:
        print(f"     {e}")
    if not args.yes:
        try:
            if input("  Type 'delete' to confirm: ").strip() != "delete":
                sys.exit("  Aborted.")
        except (EOFError, KeyboardInterrupt):
            sys.exit("  Aborted.")
    conn = db_connect(ROOT / cfg.get("backend_dir", "datamind/backend"))
    deleted = reset_accounts(conn, emails)
    conn.close()
    print(f"==> Deleted: {deleted or 'nothing (already clean)'}")
    return True


def cmd_up(cfg, instances, args):
    for inst in instances:
        inst.start()
    return True


def cmd_down(cfg, instances, args):
    for inst in instances:
        inst.stop()
    return True


def cmd_status(cfg, instances, args):
    for inst in instances:
        up = inst.is_up()
        mode = "?"
        if up:
            _, body = inst.context(cfg["partner_key"])
            mode = body.get("subscription_free") if isinstance(body, dict) else "?"
        print(f"  [{inst.name}] port {inst.port}  "
              f"{'UP  ' if up else 'DOWN'}  subscription_free={mode}")
    return True


def cmd_run(cfg, instances, args):
    r = Results()
    by_name = {i.name: i for i in instances}
    scen = cfg["scenarios"]

    def inst_for(key):
        return by_name[scen[key]["instance"]]

    def aat_for(key):
        return cfg["aats"][scen[key]["aat"]]

    print("\n" + "=" * 78)
    print("PHASE 0 - start every instance against the shared database")
    print("=" * 78)
    for inst in instances:
        inst.start()
    conn = db_connect(ROOT / cfg.get("backend_dir", "datamind/backend"))

    check_shared_db(r, instances, conn, cfg['partner_key'])
    check_scheduler_lock(r, instances, conn)

    print("\n" + "=" * 78)
    print("PHASE 1 - build merchant states on both instances")
    print("=" * 78)
    b1 = scenario_beta_fresh(r, inst_for("B1_fresh_beta"), cfg,
                             aat_for("B1_fresh_beta"), "B1")
    b2 = scenario_trial(r, inst_for("B2_trial_beta"), cfg, conn,
                        aat_for("B2_trial_beta"), "B2", expect_free=True)
    b3 = scenario_expired(r, inst_for("B3_expired_beta"), cfg, conn,
                          aat_for("B3_expired_beta"), "B3", expect_free=True)
    a1 = scenario_paid_fresh(r, inst_for("A1_fresh_paid"), cfg,
                             aat_for("A1_fresh_paid"), "A1")
    a3 = scenario_payment(r, inst_for("A3_paid_active"), cfg, conn,
                          aat_for("A3_paid_active"), "A3", skip=not args.payment)

    if not args.skip_sweep:
        check_restart_sweep(r, instances, conn)

    print("\n" + "=" * 78)
    print("PHASE 2 - end the free period: flip every beta instance to paid")
    print("=" * 78)
    beta = [i for i in instances if i.subscription_free]
    for inst in beta:
        print(f"  flipping instance {inst.name} to SUBSCRIPTION_FREE=false")
        inst.restart(subscription_free=False)

    target = beta[0] if beta else instances[0]

    code, body = target.context(cfg["partner_key"])
    r.check("FLIP", "flipped instance now reports subscription_free=false",
            code == 200 and body.get("subscription_free") is False,
            f"got {body.get('subscription_free') if isinstance(body, dict) else code}")

    if b2:
        email, token = b2
        before = subscription_row(conn, email)
        code, sub = http("GET", f"{target.base}/v1/billing/subscription", token=token)
        r.check("FLIP", "mid-trial merchant is untouched by the flip",
                code == 200 and sub.get("status") == "trial",
                f"status={sub.get('status') if isinstance(sub, dict) else code}")
        r.check("FLIP", "mid-trial period_end unchanged",
                bool(before) and before["period_end"] == subscription_row(
                    conn, email)["period_end"],
                f"{before['period_end']}" if before else "no row")

    if b3:
        email, token = b3
        code, sub = http("GET", f"{target.base}/v1/billing/subscription", token=token)
        r.check("FLIP", "expired merchant stays expired after the flip",
                code == 200 and sub.get("status") == "expired",
                f"status={sub.get('status') if isinstance(sub, dict) else code}")
        # The whole point of the flip: paying is possible again.
        code, _ = http("POST", f"{target.base}/embed/salesplay/subscription/preview",
                       preview_body(cfg["partner_key"], aat_for("B3_expired_beta")),
                       token=token)
        r.check("FLIP", "payment route is reachable again (no 403)",
                code != 403, f"HTTP {code}")

    if b1:
        email, token = b1
        code, sub = http("GET", f"{target.base}/v1/billing/subscription", token=token)
        r.check("FLIP", "never-trialled merchant now sees paid mode",
                code == 200 and sub.get("subscription_free") is False,
                f"free={sub.get('subscription_free') if isinstance(sub, dict) else code}")

    conn.close()
    ok = r.report()
    if not args.keep_up:
        print("\n==> Stopping instances (use --keep-up to leave them running)")
        for inst in instances:
            inst.stop()
    return ok


COMMANDS = {
    "check": cmd_check, "up": cmd_up, "down": cmd_down,
    "status": cmd_status, "reset": cmd_reset, "run": cmd_run,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted(COMMANDS))
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--payment", action="store_true",
                    help="ALSO run the manual card step (A3). Off by default: it "
                         "needs a real Salesplay charge, and the card/subscription "
                         "path is covered by manual testing.")
    ap.add_argument("--skip-sweep", action="store_true",
                    help="skip the cross-instance restart sweep check")
    ap.add_argument("--keep-up", action="store_true",
                    help="leave instances running after the suite finishes")
    ap.add_argument("--yes", action="store_true", help="no confirmation prompt (reset)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    instances = [Instance(spec, cfg) for spec in cfg["instances"]]
    try:
        ok = COMMANDS[args.command](cfg, instances, args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        for inst in instances:
            inst.stop()
        return 130
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
