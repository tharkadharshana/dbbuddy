"""
evals/runner.py — the eval + safety regression harness (PLAN 08 Step 4/6).

Runs evals/dataset.jsonl through the pipeline and scores each case:
  - safety  : red-team SQL that must stay blocked, and tenant-scoping that must
              be present. Runs NOW against mcp_server.sql_guard / safety — no
              LLM, no live API, no DB. This is the regression GATE.
  - parity  : cache-derived answer == live-API answer (doc 09 Part 8). Needs a
              live report-API token (REPORT_API_EVAL_TOKEN) + a fixture tenant.
  - data    : exact-number match against a fixture tenant (needs LLM + fixture).
  - general : general-knowledge answered without data tools (needs LLM).

Cases whose `requires` resource isn't configured are SKIPPED (not failed), so the
safety gate is meaningful in any environment while richer evals light up when
secrets are present.

CLI:
  python evals/runner.py --report     # human scorecard
  python evals/runner.py --ci         # exit 1 if any safety case fails or score < threshold
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import safety, sql_guard

_DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.jsonl")
_SCORE_THRESHOLD = float(os.getenv("EVALS_SCORE_THRESHOLD", "1.0"))   # of runnable (non-skipped) cases

PASS, FAIL, SKIP = "pass", "fail", "skip"


def load_dataset(path: str = _DATASET) -> list:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                cases.append(json.loads(line))
    return cases


def _resource_available(requires: str) -> bool:
    if requires == "live_token":
        return bool(os.getenv("REPORT_API_EVAL_TOKEN"))
    if requires in ("llm", "fixture_llm"):
        return bool(os.getenv("EVALS_LLM_KEY") or os.getenv("OPENAI_API_KEY"))
    return False


def _run_safety(case: dict) -> tuple:
    kind = case["kind"]
    if kind == "sql_reject":
        try:
            if case.get("guard") == "legacy":
                safety.block_mutations(case["sql"])
                safety.block_unsafe_constructs(case["sql"])
            else:
                if "allowed" in case:
                    sql_guard.assert_safe_select(case["sql"], case["allowed"])
                if case.get("tenant"):
                    sql_guard.enforce_tenant_ast(case["sql"], case["tenant"])
            return FAIL, "expected the query to be rejected, but no guard raised"
        except ValueError:
            return PASS, "correctly rejected"
    if kind == "sql_scope":
        out = sql_guard.enforce_tenant_ast(case["sql"], case["tenant"])
        if case["expect_contains"] in out:
            return PASS, out
        return FAIL, f"tenant predicate missing; got: {out}"
    return SKIP, f"unknown safety kind {kind!r}"


def run_case(case: dict) -> dict:
    ctype = case["type"]
    try:
        if ctype == "safety":
            status, detail = _run_safety(case)
        elif ctype in ("parity", "data", "general"):
            if not _resource_available(case.get("requires", "")):
                status, detail = SKIP, f"requires {case.get('requires')} (not configured)"
            else:
                status, detail = _run_live_eval(case)
        else:
            status, detail = SKIP, f"unknown case type {ctype!r}"
    except Exception as exc:                      # a harness error is a failure, not a crash
        status, detail = FAIL, f"harness error: {exc}"
    return {"id": case["id"], "type": ctype, "status": status, "detail": detail}


def _run_live_eval(case: dict) -> tuple:
    # Placeholder for the secret-gated evals (parity/data/general). Wiring these
    # to the live pipeline + fixture tenant is done where secrets exist; until
    # then they SKIP above. Kept explicit so the harness shape is complete.
    return SKIP, "live eval wiring pending fixture/secrets"


def score(results: list) -> dict:
    by_type: dict = {}
    for r in results:
        b = by_type.setdefault(r["type"], {PASS: 0, FAIL: 0, SKIP: 0})
        b[r["status"]] += 1
    runnable = sum(1 for r in results if r["status"] in (PASS, FAIL))
    passed = sum(1 for r in results if r["status"] == PASS)
    safety_failed = [r for r in results if r["type"] == "safety" and r["status"] == FAIL]
    return {
        "total": len(results),
        "runnable": runnable,
        "passed": passed,
        "score": round(passed / runnable, 4) if runnable else None,
        "by_type": by_type,
        "safety_failures": [r["id"] for r in safety_failed],
    }


def _print_report(results: list, card: dict) -> None:
    print("\n=== DataMind eval scorecard ===")
    for r in results:
        mark = {PASS: "PASS", FAIL: "FAIL", SKIP: "skip"}[r["status"]]
        print(f"  [{mark}] {r['type']:8} {r['id']:28} {r['detail'][:70]}")
    print(f"\nby type: {card['by_type']}")
    print(f"score (of runnable): {card['score']}  "
          f"({card['passed']}/{card['runnable']}); total {card['total']}")
    if card["safety_failures"]:
        print(f"SAFETY FAILURES: {card['safety_failures']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ci", action="store_true", help="exit non-zero if safety fails or score < threshold")
    ap.add_argument("--report", action="store_true", help="print a human scorecard")
    args = ap.parse_args()

    results = [run_case(c) for c in load_dataset()]
    card = score(results)
    if args.report or not args.ci:
        _print_report(results, card)

    if args.ci:
        ok = not card["safety_failures"] and (card["score"] is None or card["score"] >= _SCORE_THRESHOLD)
        print(json.dumps(card))
        if not ok:
            print(f"EVAL GATE FAILED (threshold {_SCORE_THRESHOLD}).", file=sys.stderr)
            sys.exit(1)
        print("EVAL GATE PASSED.")


if __name__ == "__main__":
    main()
