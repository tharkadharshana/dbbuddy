"""
report_cache/normalize.py — pure functions turning raw report-API JSON into
typed metric dicts keyed by registry metric names.

The API formats every number with the TENANT'S number-format settings
(NumberFormatHelper / NumberFormatRepository — keys 'decimal_separator' and
'thousond_separator' [sic], which can be European: 1.234,56). parse_number is
therefore separator-aware; callers pass the tenant_profile.number_format we
stored at onboarding. Never raise on a bad cell — return None so one
malformed field can't abort an ingestion batch.
"""

import json
import re
from datetime import date, datetime
from typing import Any, Optional

from logger import get_logger

from .registry import Report

log = get_logger(__name__)

# Formats DateTimeFormatHelper can emit for sales_summary table_data dates,
# tried in order. ponytail: finite list, not locale-aware parsing — unmatched
# formats log + skip the row; extend the list if a tenant format surfaces.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%b %d,%Y", "%b %d, %Y",
    "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y",
    "%d %b %Y", "%d %B %Y",
)


def separators(number_format) -> tuple:
    """(decimal_sep, thousand_sep) from a tenant_profile.number_format value
    (dict or JSON string), defaulting to ('.', ',')."""
    nf = number_format
    if isinstance(nf, str):
        try:
            nf = json.loads(nf)
        except ValueError:
            nf = None
    if not isinstance(nf, dict):
        return ".", ","
    return nf.get("decimal_separator") or ".", nf.get("thousond_separator") or ","


def parse_number(s: Any, decimal_sep: str = ".", thousand_sep: str = ",") -> Optional[float]:
    """'201,852.00' -> 201852.0 ; '1.234,56' (EU separators) -> 1234.56 ;
    '' / '-' -> None ; already-numeric passes through."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)

    text = str(s).strip()
    if text in ("", "-", "--"):
        return None

    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]

    text = text.replace(thousand_sep, "").replace(decimal_sep, ".")
    # First numeric token — ignores currency symbols/labels around the number.
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        log.warning("parse_number: unparseable value", raw=str(s)[:40])
        return None
    value = float(m.group(0))
    return -value if negative else value


def parse_api_date(s: Any) -> Optional[date]:
    if not s:
        return None
    text = str(s).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    log.warning("parse_api_date: unrecognized date", raw=text[:40])
    return None


def _pick(row: dict, metric) -> Any:
    """Raw value for a metric from a row, trying its key then aliases."""
    if metric.key in row:
        return row[metric.key]
    for alias in metric.aliases:
        if alias in row:
            return row[alias]
    return None


def normalize_metrics(report: Report, row: dict, decimal_sep: str = ".",
                      thousand_sep: str = ",") -> dict:
    """{metric_key: float|None} for every declared metric present in `row`.
    Ratio metrics are never stored — they're recomputed at read time.

    Reports with NO declared metrics (registry long-tail) store every
    numeric-parseable key of the row as-is; those values are treated as
    non-additive downstream."""
    out = {}
    if not report.metrics:
        for key, raw in row.items():
            value = parse_number(raw, decimal_sep, thousand_sep)
            if value is not None:
                out[key] = value
        return out
    for m in report.metrics:
        if m.agg == "ratio":
            continue
        raw = _pick(row, m)
        if raw is not None:
            out[m.key] = parse_number(raw, decimal_sep, thousand_sep)
    return out


def normalize_daily_rows(report: Report, table_data: list, decimal_sep: str = ".",
                         thousand_sep: str = ",") -> list:
    """sales_summary table_data (GROUP BY DATE server-side) ->
    [(date, {metric_key: value})]. Rows with unparseable dates are skipped."""
    out = []
    for row in table_data or []:
        day = parse_api_date(row.get("date"))
        if day is None:
            continue
        out.append((day, normalize_metrics(report, row, decimal_sep, thousand_sep)))
    return out


def normalize_dim_rows(report: Report, table_data: list, decimal_sep: str = ".",
                       thousand_sep: str = ",") -> list:
    """Dimensional table_data -> [(dim_key, dim_label, {metric_key: value})].
    dim_key joins the report's dim_fields (e.g. category + subcategory)."""
    out = []
    for row in table_data or []:
        parts = [str(row.get(f) or "").strip() for f in report.dim_fields]
        dim_label = " / ".join(p for p in parts if p)
        if not dim_label:
            continue
        dim_key = dim_label.lower()[:180]
        out.append((dim_key, dim_label[:255],
                    normalize_metrics(report, row, decimal_sep, thousand_sep)))
    return out
