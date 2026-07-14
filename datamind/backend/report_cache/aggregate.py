"""
report_cache/aggregate.py
==========================
The additivity-aware aggregation core (PLAN 05 Step 1, doc 09 C3). This is
where "silently wrong numbers" are prevented — the single most important
correctness rule in the whole cache.

Metric.agg (from registry.py) decides what may be combined across periods:
  sum          → add the base values across days/months.
  ratio        → NEVER add the ratio itself; recompute it from the SUMMED
                 numerator/denominator (avg_ticket = Σnet ÷ Σcount, not the
                 mean of daily avg_tickets).
  non_additive → cannot be derived from stored parts at all (distinct counts,
                 per-unit prices, whole-range payout totals). The caller MUST
                 re-fetch the exact range from the report API — aggregate never
                 fabricates these from cached facts.

Everything here is pure (no DB, no HTTP) so it's exhaustively unit-testable —
report_cache/answer.py does the I/O and calls these.
"""

from typing import List, Optional

from logger import get_logger
from report_cache.registry import REPORTS

log = get_logger(__name__)


def _report(report_id: str):
    report = REPORTS.get(report_id)
    if report is None:
        raise ValueError(f"Unknown report_id: {report_id!r}")
    return report


def _ranking_metric(report) -> Optional[str]:
    """Denominator of the report's ratio metric = its "sales size" metric — the
    same generic top-N ranking key report_cache/ingest.py uses (works across
    dimensional reports whose field names genuinely differ)."""
    for metric in report.metrics:
        if metric.agg == "ratio" and metric.den:
            return metric.den
    return None


def _combine(metrics_dicts: List[dict], report) -> dict:
    """Sum every `sum` metric across the given metric dicts, then derive every
    `ratio` metric from those sums. Returns {key: value}. `non_additive`
    metrics are never produced here (the caller must live-fetch them)."""
    sums: dict = {}
    for metric in report.metrics:
        if metric.agg != "sum":
            continue
        total = 0.0
        present = False
        for md in metrics_dicts:
            v = md.get(metric.key)
            if v is not None:
                total += v
                present = True
        if present:
            sums[metric.key] = total

    out = dict(sums)
    for metric in report.metrics:
        if metric.agg != "ratio":
            continue
        num = sums.get(metric.num)
        den = sums.get(metric.den)
        if num is not None and den:  # den not None and not zero
            out[metric.key] = num / den
    return out


def aggregate_scalar(facts: List[dict], report_id: str,
                     metrics: Optional[List[str]] = None) -> dict:
    """Combine daily scalar facts over a range. `facts` = rows from
    read.get_daily_facts (each with a parsed `metrics` dict). `metrics` filters
    which keys are returned (None = all additive/ratio metrics). Returns:
        {"metrics": {...}, "days": N, "non_additive_skipped": [...],
         "provenance": "from_cache"}
    A requested non_additive metric is NOT computed — it's listed in
    `non_additive_skipped` so the caller knows it must live-fetch."""
    report = _report(report_id)
    combined = _combine([f.get("metrics", {}) for f in facts], report)

    non_additive_skipped = []
    if metrics is not None:
        requested = set(metrics)
        for metric in report.metrics:
            if metric.key in requested and metric.agg == "non_additive":
                non_additive_skipped.append(metric.key)
        combined = {k: v for k, v in combined.items() if k in requested}

    return {
        "metrics": combined,
        "days": len(facts),
        "non_additive_skipped": non_additive_skipped,
        "provenance": "from_cache",
    }


def aggregate_dim(facts: List[dict], report_id: str,
                  top_n: Optional[int] = None) -> List[dict]:
    """Combine dimensional facts (possibly spanning several months) per dim
    member: sum additive metrics, recompute ratios from the sums. Sorted by the
    report's sales-size metric descending; capped to top_n if given. Returns
    [{dim_key, dim_name, metrics{...}}]."""
    report = _report(report_id)

    grouped: dict = {}
    for f in facts:
        key = f["dim_key"]
        bucket = grouped.setdefault(key, {"dim_name": f.get("dim_name") or key, "rows": []})
        bucket["rows"].append(f.get("metrics", {}))
        if f.get("dim_name"):
            bucket["dim_name"] = f["dim_name"]

    out = []
    for key, bucket in grouped.items():
        out.append({
            "dim_key": key,
            "dim_name": bucket["dim_name"],
            "metrics": _combine(bucket["rows"], report),
        })

    rank_key = _ranking_metric(report)
    if rank_key:
        out.sort(key=lambda r: r["metrics"].get(rank_key, 0.0), reverse=True)
    if top_n is not None:
        out = out[:top_n]
    return out


def needs_live_fetch(report_id: str, metrics: Optional[List[str]], coverage: Optional[dict]) -> bool:
    """Decide cache-vs-live for a SCALAR report over a day range (doc 09 C3/C4/C6):
    live-fetch if the report isn't daily-cacheable, OR any requested metric is
    non_additive, OR the cache doesn't fully cover the range, OR the range
    includes a still-mutating open period. Dimensional coverage is decided in
    answer.py (coverage() is scalar-only)."""
    report = _report(report_id)
    if report.kind != "scalar":
        raise ValueError(f"needs_live_fetch is for scalar reports; {report_id} is {report.kind!r}")

    if not report.daily_cacheable:
        return True

    requested = set(metrics) if metrics else {m.key for m in report.metrics}
    if any(m.agg == "non_additive" and m.key in requested for m in report.metrics):
        return True

    if coverage is not None and (not coverage.get("covered") or coverage.get("has_open")):
        return True
    return False
