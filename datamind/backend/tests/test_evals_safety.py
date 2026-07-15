"""
tests/test_evals_safety.py — wires the eval harness's SAFETY suite into pytest,
so `pytest tests/` (and therefore CI) gates every doc-06 red-team case. The
secret-gated parity/data/general cases skip; the safety cases must all pass.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import runner


def test_all_safety_evals_pass():
    results = [runner.run_case(c) for c in runner.load_dataset()]
    card = runner.score(results)
    assert card["safety_failures"] == [], f"safety regressions: {card['safety_failures']}"
    # every safety case is runnable (no skips) and passes
    safety = [r for r in results if r["type"] == "safety"]
    assert safety and all(r["status"] == "pass" for r in safety)


def test_score_gate_would_pass():
    results = [runner.run_case(c) for c in runner.load_dataset()]
    card = runner.score(results)
    assert card["score"] == 1.0   # of the runnable (non-skipped) cases
