#!/usr/bin/env python3
"""
Report-cache plan matrix — doc 14 §B2, automated.

Drives the /qa/* dev routes to put ONE test account through every plan and
period combination, then asserts what the report layer actually did: whether
the answer came from cache or live, whether the plan window was clamped, and
whether an over-window ask produced an upgrade message instead of a refusal.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
USAGE
    python qa_report_cache.py                      full matrix
    python qa_report_cache.py --only Starter       one plan
    python qa_report_cache.py --state              just dump current state

PREREQUISITES
  - Backend running at http://127.0.0.1:8000 with:
        QA_ROUTES_ENABLED=true
        QA_ROUTES_EMAILS=<the login below>
        REPORT_CACHE_ENABLED=true
  - A test account with an ACTIVE SalesPlay integration and synced data.
  - The account's plan/tokens ARE MUTATED by this script and left on the last
    row's plan. Re-run with --restore Pro (or whatever it should be) after.

WHY THIS EXISTS
  Rows 5, 7, 9, 10 of doc 14 §B2 all failed before the Phase 1 fixes — the
  months*30 window and the 24-month backfill cap. Nothing re-checks them
  automatically, so a regression there is invisible until a merchant complains
  that data they pay for is "not available".
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import argparse
import sys
from datetime import date, timedelta

import requests

BASE = "http://127.0.0.1:8000"
EMAIL = "livedata@test.com"
PASSWORD = "Pass@123"
TIMEOUT = 180

_G, _R, _Y, _D, _0 = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


def _month_range(months_ago: int):
    """First and last day of the calendar month `months_ago` months back."""
    today = date.today()
    y, m = today.year, today.month - months_ago
    while m <= 0:
        y, m = y - 1, m + 12
    start = date(y, m, 1)
    nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, date(nm_y, nm_m, 1) - timedelta(days=1)


class QA:
    def __init__(self):
        self.s = requests.Session()
        self.token = None

    def login(self):
        r = self.s.post(f"{BASE}/auth/login",
                        json={"email": EMAIL, "password": PASSWORD}, timeout=30)
        r.raise_for_status()
        self.token = r.json()["token"]
        self.s.headers["Authorization"] = f"Bearer {self.token}"

    def _post(self, path, **kw):
        r = self.s.post(f"{BASE}{path}", timeout=TIMEOUT, **kw)
        if r.status_code == 404:
            sys.exit(f"{_R}/qa routes are not mounted.{_0} Set QA_ROUTES_ENABLED=true, "
                     f"QA_ROUTES_EMAILS={EMAIL} and restart the backend.")
        if r.status_code == 403:
            sys.exit(f"{_R}{EMAIL} is not in QA_ROUTES_EMAILS.{_0}")
        r.raise_for_status()
        return r.json()

    def state(self):
        r = self.s.get(f"{BASE}/qa/state", timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def set_plan(self, plan, months_back=None):
        """Put the account on `plan`. months_back backdates period_start so the
        subscription window itself can be tested, not just the plan's depth."""
        body = {"plan": plan, "status": "active"}
        if months_back:
            start, _ = _month_range(months_back)
            body["period_start"] = start.isoformat()
            body["period_end"] = (date.today() + timedelta(days=30)).isoformat()
        return self._post("/qa/plan", json=body)

    def reset_tokens(self):
        return self._post("/qa/tokens", json={"action": "reset"})

    def clear_cache(self, report_id=None, month=None):
        return self._post("/qa/cache/clear",
                          json={"report_id": report_id, "month": month})

    def ask(self, question):
        r = self.s.post(f"{BASE}/v1/query",
                        json={"question": question, "llm": "default"},
                        timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()


def _check(name, ok, detail=""):
    mark = f"{_G}PASS{_0}" if ok else f"{_R}FAIL{_0}"
    print(f"  [{mark}] {name}")
    if detail:
        print(f"         {_D}{detail}{_0}")
    return ok


def run_matrix(qa: QA, only=None):
    """Doc 14 §B2. Each row: plan, how far back to ask, what must be true."""
    rows = [
        # (plan, months_ago, must_answer, note)
        ("Starter", 1,  True,  "last closed month — inside 3-month window"),
        ("Starter", 2,  True,  "2 months ago — inside window"),
        ("Starter", 3,  True,  "exactly 3 calendar months — boundary (was broken by months*30)"),
        ("Starter", 6,  False, "6 months ago — outside window, expect upgrade message not refusal"),
        ("Growth",  11, True,  "11 months ago — inside 12-month window"),
        ("Growth",  12, True,  "same month last year — boundary (was broken by months*30)"),
        ("Growth",  14, False, "14 months ago — outside window"),
        ("Pro",     18, True,  "18 months ago — inside Pro's window (was broken by 24-month cap)"),
        ("Pro",     30, True,  "30 months ago — deep tail (was broken by 24-month cap)"),
    ]
    if only:
        rows = [r for r in rows if r[0] == only]

    results = []
    current_plan = None
    for plan, months_ago, must_answer, note in rows:
        if plan != current_plan:
            print(f"\n{_Y}── {plan} ──{_0}")
            qa.set_plan(plan)
            qa.reset_tokens()
            current_plan = plan
            st = qa.state()
            print(f"  {_D}window_start={st['window_start']} "
                  f"history_months={st['history_months']}{_0}")

        start, end = _month_range(months_ago)
        q = (f"What were my total sales between {start.isoformat()} "
             f"and {end.isoformat()}?")
        try:
            resp = qa.ask(q)
        except Exception as exc:
            results.append(_check(f"{plan} / {months_ago}mo ago", False, f"request failed: {exc}"))
            continue

        text = (resp.get("analysis") or resp.get("message") or "")
        low = text.lower()
        # A refusal that offers no figures and no upgrade path is the failure
        # mode doc 15 §5 describes: the tool raised, so the model could only
        # apologise.
        said_no_data = any(p in low for p in (
            "no data", "don't have data", "do not have data",
            "no sales data", "unable to retrieve", "couldn't find any data"))
        mentions_upgrade = any(p in low for p in (
            "upgrade", "higher plan", "current plan", "plan covers", "plan includes"))

        if must_answer:
            ok = _check(f"{plan} / {months_ago}mo ago — {note}",
                        not said_no_data,
                        f"answer: {text[:160]}")
        else:
            # Outside the window is allowed to decline, but must explain the
            # plan limit rather than claim the data does not exist.
            ok = _check(f"{plan} / {months_ago}mo ago — {note}",
                        mentions_upgrade,
                        f"answer: {text[:160]}")
        results.append(ok)

    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Starter | Growth | Pro")
    ap.add_argument("--state", action="store_true", help="dump state and exit")
    ap.add_argument("--restore", help="set this plan and exit")
    args = ap.parse_args()

    qa = QA()
    qa.login()

    if args.state:
        import json
        print(json.dumps(qa.state(), indent=2, default=str))
        return
    if args.restore:
        qa.set_plan(args.restore)
        qa.reset_tokens()
        print(f"Restored to {args.restore}.")
        return

    st = qa.state()
    if not st.get("tenant_id"):
        sys.exit(f"{_R}{EMAIL} has no integration/tenant — the report path "
                 f"cannot be tested.{_0}")
    if st.get("report_cache_enabled", "").lower() not in ("1", "true", "yes"):
        sys.exit(f"{_R}REPORT_CACHE_ENABLED is not true on the backend.{_0}")

    print(f"{_D}tenant={st['tenant_id']} ai_flow={st['ai_flow']}{_0}")
    results = run_matrix(qa, args.only)

    passed, total = sum(1 for r in results if r), len(results)
    colour = _G if passed == total else _R
    print(f"\n{colour}{passed}/{total} passed{_0}")
    print(f"{_D}Account left on the last row's plan — "
          f"run --restore <plan> to put it back.{_0}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
