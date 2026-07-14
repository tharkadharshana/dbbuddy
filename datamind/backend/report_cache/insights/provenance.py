"""
report_cache/insights/provenance.py
=====================================
Numeric-provenance guard for generated insights (PLAN 06 Step 5, same intent as
llm.generate_report_summary's fabrication guard). Pure + testable.

`unsupported_numbers(text, allowed)` returns the numeric literals in a generated
answer that do NOT match any number the tools actually produced — i.e. candidate
fabrications. It is used two ways:
  - a hard assertion in tests (a mock LLM citing only pack numbers must be clean);
  - a SOFT signal in production (logged), because legitimate general business
    advice DOES introduce benchmark numbers ("aim for ~30% margin") that aren't
    in the data pack. So this flags, it does not silently delete — the strong
    system prompt is the primary guard; this is the verify-after check.
"""

import re
from typing import Iterable, List, Set

# A number token: optional sign, digits with optional thousands separators and
# decimal part. Deliberately does NOT eat a trailing '%' or currency symbol.
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _to_float(token: str):
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def extract_numbers(text: str) -> List[float]:
    """Every numeric literal in `text`, as floats (commas stripped)."""
    out = []
    for m in _NUM_RE.findall(text or ""):
        v = _to_float(m)
        if v is not None:
            out.append(v)
    return out


def collect_numbers(obj) -> Set[float]:
    """Recursively pull every number out of a fact pack (dicts/lists/scalars) so
    it can be used as the `allowed` set. Strings are parsed for embedded numbers
    too (report values are often formatted strings)."""
    found: Set[float] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.add(float(obj))
    elif isinstance(obj, str):
        found.update(extract_numbers(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            found |= collect_numbers(v)
    elif isinstance(obj, (list, tuple, set)):
        for v in obj:
            found |= collect_numbers(v)
    return found


def _matches(value: float, allowed: Iterable[float], rel_tol: float, abs_tol: float) -> bool:
    for a in allowed:
        if abs(value - a) <= abs_tol:
            return True
        if a and abs(value - a) / abs(a) <= rel_tol:
            return True
        # a derived integer part often appears rounded (e.g. "1,234" for 1234.56)
        if abs(round(value) - round(a)) <= abs_tol:
            return True
    return False


def unsupported_numbers(text: str, allowed: Iterable[float],
                        rel_tol: float = 0.01, abs_tol: float = 0.5) -> List[float]:
    """Numbers appearing in `text` that don't match any `allowed` number
    (within tolerance). Empty list = every figure is grounded in the pack.
    Small integers 0..31 are treated as supported (they're almost always dates,
    counts, horizons, or 'top 5' style qualifiers, not fabricated KPIs)."""
    allowed = set(allowed)
    out = []
    for v in extract_numbers(text):
        if 0 <= v <= 31 and float(v).is_integer():
            continue
        if not _matches(v, allowed, rel_tol, abs_tol):
            out.append(v)
    return out
