#!/usr/bin/env python3
"""
test_embed_integration.py
=========================
Automated tests for Steps 8 and 9 of the Salesplay embed integration.

Step 8 — Rate limiter:   POST /embed/init is capped at 5 calls/min per IP.
Step 9 — Pool under load: 30 concurrent authenticated requests all succeed.

Usage:
    python tests/test_embed_integration.py --email you@example.com --password yourpass

The email/password must belong to a DataMind account that already has a
Salesplay integration connected (created during the manual onboarding test).
"""

import sys
import time
import argparse
import concurrent.futures

import requests

BASE        = "http://localhost:8000"
PARTNER_KEY = "sp_dev_test"

# -- Colour helpers -------------------------------------------------------------

def green(s):  return s
def red(s):    return s
def yellow(s): return s
def bold(s):   return s
def dim(s):    return s

PASS = "PASS [OK]"
FAIL = "FAIL [!!]"


# -- Helpers --------------------------------------------------------------------

def login(email: str, password: str) -> str:
    """Login and return a JWT token."""
    r = requests.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    if r.status_code != 200:
        print(red(f"  Login failed ({r.status_code}): {r.json()}"))
        sys.exit(1)
    token = r.json()["token"]
    print(green(f"  Logged in as {email}"))
    return token


def section(title: str):
    width = 65
    print(f"\n{'-' * width}")
    print(f"  {title}")
    print(f"{'-' * width}")


# -- Test 1: /embed/context endpoint -------------------------------------------

def test_context_endpoint() -> bool:
    section("Test 1 — GET /embed/context (sanity check)")
    passed = True

    # 1a valid key
    r = requests.get(f"{BASE}/embed/context?pk={PARTNER_KEY}")
    ok = r.status_code == 200 and r.json().get("provider_id") == "salesplay"
    print(f"  Valid key -> {r.status_code}  {PASS if ok else FAIL}")
    if not ok:
        print(red(f"    Response: {r.text}"))
    passed = passed and ok

    # 1b invalid key
    r = requests.get(f"{BASE}/embed/context?pk=not_a_real_key")
    ok = r.status_code == 404
    print(f"  Invalid key -> {r.status_code}  {PASS if ok else FAIL}")
    passed = passed and ok

    # 1c missing key
    r = requests.get(f"{BASE}/embed/context")
    ok = r.status_code == 422   # FastAPI validation error — pk is required
    print(f"  Missing key -> {r.status_code}  {PASS if ok else FAIL}")
    passed = passed and ok

    return passed


# -- Test 2: /embed/validate-token endpoint -------------------------------------

def test_validate_token_endpoint() -> bool:
    section("Test 2 — POST /embed/validate-token (no auth required)")
    passed = True

    # 2a: no JWT header — should work (this is the whole point of the fix)
    r = requests.post(f"{BASE}/embed/validate-token", json={
        "partner_key": PARTNER_KEY,
        "api_token":   "obviously_fake_token_xyz"
    })
    # Result will be 200 with ok=False (Salesplay rejected the token)
    # The important thing is NOT 401 (not authenticated)
    ok = r.status_code == 200 and r.json().get("ok") == False
    print(f"  No JWT, bad token -> {r.status_code} ok={r.json().get('ok')}  {PASS if ok else FAIL}")
    if r.status_code == 401:
        print(red("    Still returning 401 — fix did not apply. Restart the backend."))
    passed = passed and ok

    # 2b: invalid partner key
    r = requests.post(f"{BASE}/embed/validate-token", json={
        "partner_key": "wrong_key",
        "api_token":   "anything"
    })
    ok = r.status_code == 404
    print(f"  Invalid partner key -> {r.status_code}  {PASS if ok else FAIL}")
    passed = passed and ok

    return passed


# -- Test 3: /embed/init security headers --------------------------------------

def test_security_headers() -> bool:
    section("Test 3 — Security headers on /embed/* routes")
    passed = True

    r = requests.get(f"{BASE}/embed/context?pk={PARTNER_KEY}")
    csp = r.headers.get("content-security-policy", "")
    cto = r.headers.get("x-content-type-options", "")

    ok_csp = "frame-ancestors" in csp
    print(f"  Content-Security-Policy -> '{csp}'  {PASS if ok_csp else FAIL}")
    passed = passed and ok_csp

    ok_cto = cto == "nosniff"
    print(f"  X-Content-Type-Options  -> '{cto}'  {PASS if ok_cto else FAIL}")
    passed = passed and ok_cto

    # Non-embed route should NOT have frame-ancestors
    r2 = requests.get(f"{BASE}/health")
    no_csp = "frame-ancestors" not in r2.headers.get("content-security-policy", "")
    print(f"  Non-embed route has no frame-ancestors  {PASS if no_csp else FAIL}")
    passed = passed and no_csp

    return passed


# -- Test 4 (Step 8): Rate limiter on /embed/init ------------------------------

def test_rate_limiter() -> bool:
    section("Step 8 — POST /embed/init rate limiter (5 req/min per IP)")
    print(dim("  Fires 10 calls rapidly. The limiter allows 5 per 60s per IP."))
    print(dim("  Earlier tests in this run may have used some of the budget,"))
    print(dim("  so we only assert: some calls pass AND some calls get 429.\n"))

    results = []
    for i in range(10):
        r = requests.post(f"{BASE}/embed/init", json={
            "partner_key": "invalid_key_for_rate_test",
            "api_token":   "fake",
            "name":        "Rate Test",
            "email":       f"ratetest_{i}@example.com",
            "password":    "test1234"
        })
        results.append(r.status_code)
        if r.status_code == 429:
            label = "<- rate limited [429]"
        elif r.status_code == 404:
            label = dim("<- past limiter, bad partner key [404]")
        else:
            label = red(f"<- unexpected {r.status_code}: {r.text[:60]}")
        print(f"  Call {i+1}: HTTP {r.status_code}  {label}")

    passed_limiter = results.count(429)
    passed_through = sum(1 for s in results if s != 429)

    limiter_fired = passed_limiter > 0
    some_passed   = passed_through > 0

    print(f"\n  Calls that got through:    {passed_through}")
    print(f"  Calls that were blocked:   {passed_limiter}")
    print(f"\n  Limiter fired (429 seen):  {PASS if limiter_fired else FAIL}")
    print(f"  At least 1 call passed:    {PASS if some_passed else FAIL}")

    print(dim("\n  (Rate limit window resets after 60 seconds)"))
    return limiter_fired and some_passed


# -- Test 5 (Step 9): Connection pool under concurrent load --------------------

def test_connection_pool(token: str) -> bool:
    section("Step 9 — Connection pool: 30 concurrent authenticated requests")
    print(dim("  Firing 30 simultaneous GET /providers/salesplay/status requests..."))

    headers = {"Authorization": f"Bearer {token}"}
    n = 30
    errors = []

    def call(_):
        try:
            r = requests.get(
                f"{BASE}/providers/salesplay/status",
                headers=headers,
                timeout=15
            )
            return r.status_code
        except Exception as e:
            return f"ERR:{e}"

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        statuses = list(ex.map(call, range(n)))
    elapsed = round(time.time() - t0, 2)

    ok_count   = sum(1 for s in statuses if s == 200)
    fail_count = n - ok_count
    errors     = [s for s in statuses if s != 200]

    print(f"\n  {ok_count}/{n} succeeded in {elapsed}s")
    if errors:
        unique_errors = {}
        for e in errors:
            unique_errors[e] = unique_errors.get(e, 0) + 1
        for e, count in unique_errors.items():
            print(red(f"  {count}× {e}"))

    passed = ok_count == n
    if not passed and any("Too many connections" in str(e) for e in errors):
        print(red("\n  DB connection pool exhausted — check DB_POOL_SIZE in .env"))
    print(f"\n  All {n} requests succeeded:  {PASS if passed else FAIL}")
    return passed


# -- Test 6: Pool survives burst + normal request after ------------------------

def test_pool_recovery(token: str) -> bool:
    section("Test 6 — Pool recovery: normal request after burst")
    print(dim("  Verifies connections are returned to pool after concurrent burst.\n"))

    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{BASE}/providers/salesplay/status", headers=headers, timeout=10)
    ok = r.status_code == 200
    print(f"  Single request after burst -> HTTP {r.status_code}  {PASS if ok else FAIL}")
    return ok


# -- Test 7: /embed/init rejects bad Salesplay token cleanly -------------------

def test_init_bad_token() -> bool:
    section("Test 7 — POST /embed/init rejects invalid Salesplay API token")
    print(dim("  Uses the real partner key — should reach Salesplay validation and fail cleanly.\n"))

    import time as t
    r = requests.post(f"{BASE}/embed/init", json={
        "partner_key": PARTNER_KEY,
        "api_token":   "invalid_salesplay_token_abc123",
        "name":        "Test User",
        "email":       f"badtoken_{int(t.time())}@example.com",
        "password":    "testpass123"
    })
    # 422 = validation failed (Salesplay rejected token) — correct behaviour
    # 500 = unhandled exception — broken
    # 201/200 = accepted bad token — broken
    ok = r.status_code == 422
    detail = r.json().get("detail", "")
    print(f"  HTTP {r.status_code}: {detail[:80]}")
    print(f"  Returns 422 (not 200 or 500):  {PASS if ok else FAIL}")
    return ok


# -- Runner ---------------------------------------------------------------------

def main():
    global BASE

    parser = argparse.ArgumentParser(description="Embed integration test runner")
    parser.add_argument("--email",    required=True, help="DataMind account email")
    parser.add_argument("--password", required=True, help="DataMind account password")
    parser.add_argument("--base",     default=BASE,  help="API base URL")
    args = parser.parse_args()

    BASE = args.base.rstrip("/")

    print("\n" + "=" * 67)
    print("  DataMind Embed Integration -- Automated Test Suite")
    print("=" * 67)
    print(f"  Backend: {BASE}")
    print(f"  Partner: {PARTNER_KEY}")

    # Login once to get a token for pool tests
    print(f"\n  Authenticating...")
    token = login(args.email, args.password)

    results = {}
    results["context_endpoint"]    = test_context_endpoint()
    results["validate_token"]      = test_validate_token_endpoint()
    results["security_headers"]    = test_security_headers()
    # Run init_bad_token BEFORE rate_limiter — rate_limiter exhausts the
    # 5-call budget for this IP, so anything after it gets 429 instead of 422.
    results["init_bad_token"]      = test_init_bad_token()
    results["rate_limiter"]        = test_rate_limiter()
    results["connection_pool"]     = test_connection_pool(token)
    results["pool_recovery"]       = test_pool_recovery(token)

    # Summary
    section("Summary")
    total  = len(results)
    passed = sum(1 for v in results.values() if v)
    failed = total - passed

    for name, ok in results.items():
        label = name.replace("_", " ").title()
        print(f"  {PASS if ok else FAIL}  {label}")

    print(f"\n  {passed}/{total} tests passed", end="")
    if failed == 0:
        print("  -- All tests passed!")
    else:
        print(f"  -- {failed} test(s) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
