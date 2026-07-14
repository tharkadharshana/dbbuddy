"""
report_cache/normalize.py
==========================
Pure functions that turn raw SalesPlay report-API JSON (PHP number_format()
strings, tenant-locale dates) into typed Python values keyed by the metric
names declared in report_cache.registry. See docs/09_Report_Cache_Plan_Review.md
Part 8 (C8).

Rules: strip thousands separators/currency symbols; blank/"0.00"/"-" are
treated correctly (0.00 -> 0.0, blank/"-" -> None); never raise on a bad
cell — log and return None instead, so one malformed field never aborts an
ingestion batch.
"""

from datetime import datetime
from typing import Any, Optional

from logger import get_logger
from report_cache.registry import REPORTS

log = get_logger(__name__)

# Candidate strptime formats for dates emitted by DateTimeFormatHelper
# (tenant-configurable date_format_php). Tried in order before falling back
# to pandas' flexible parser.
# ponytail: finite format list + pandas fallback, not a full locale-aware
# parser — if a tenant's date_format produces something outside this list AND
# pandas can't infer it, parse_api_date logs and returns None. Upgrade path:
# have PLAN_02's profile sync pass the tenant's actual date_format_php here
# instead of guessing.
_DATE_FORMATS = (
    "%b %d,%Y", "%b %d, %Y",   # "Apr 05,2026"
    "%Y-%m-%d",
    "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y",
    "%d %b %Y", "%d %B %Y",
)


def parse_number(s: Any) -> Optional[float]:
    """'201,852.00' -> 201852.0 ; '' / '-' -> None ; already-numeric passes through."""
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

    # Drop thousands separators and any currency symbol, keep digits/dot/minus.
    cleaned = "".join(ch for ch in text.replace(",", "") if ch.isdigit() or ch in ".-")

    if cleaned in ("", "-", ".", "-."):
        return None

    try:
        value = float(cleaned)
    except ValueError:
        log.warning("parse_number: unparseable value", raw=s)
        return None

    return -value if negative else value


def parse_api_date(s: Any) -> Optional[str]:
    """Tenant-locale date string -> ISO 'YYYY-MM-DD', or None if unparseable."""
    if not s:
        return None
    text = str(s).strip()
    if not text:
        return None

    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    try:
        import pandas as pd
        return pd.to_datetime(text).date().isoformat()
    except Exception:
        log.warning("parse_api_date: unparseable value", raw=s)
        return None


def _extract_metrics(report_id: str, raw: dict) -> dict:
    """Pull every declared metric key that's present in `raw`, parsed to float."""
    report = REPORTS.get(report_id)
    if report is None:
        log.warning("normalize: unknown report_id", report=report_id)
        return {}

    metrics = {}
    for metric in report.metrics:
        raw_key = metric.key if metric.key in raw else next(
            (alias for alias in metric.aliases if alias in raw), None
        )
        if raw_key is None:
            continue
        value = parse_number(raw[raw_key])
        if value is not None:
            metrics[metric.key] = value
    return metrics


def normalize_summary(report_id: str, raw_summary: dict) -> dict:
    """{metric_key: float} from a report's `data.summary` block."""
    if not raw_summary:
        return {}
    return _extract_metrics(report_id, raw_summary)


def normalize_daily_rows(report_id: str, table_data: list) -> list:
    """[{business_date, metrics{...}}] — only sales_summary's table_data is
    pre-grouped by day (GROUP BY DATE server-side); other reports' table_data
    is per-entity, not per-day (see registry.py module docstring)."""
    rows = []
    for raw in table_data or []:
        business_date = parse_api_date(raw.get("date"))
        if business_date is None:
            log.warning("normalize_daily_rows: dropping row with unparseable date",
                        report=report_id, raw_date=raw.get("date"))
            continue
        rows.append({
            "business_date": business_date,
            "metrics": _extract_metrics(report_id, raw),
        })
    return rows


# dim_type -> (raw key field, raw name field) in each dimensional report's table_data row
_DIM_FIELDS = {
    "sales_by_products": ("product_code", "product_name"),
    "sales_by_category": ("product_category", "product_category"),
}


def normalize_dim_rows(report_id: str, rows: list) -> list:
    """[{dim_key, dim_name, metrics{...}}] for dimensional reports (product/category)."""
    key_field, name_field = _DIM_FIELDS.get(report_id, (None, None))
    if key_field is None:
        log.warning("normalize_dim_rows: report is not dimensional", report=report_id)
        return []

    out = []
    for raw in rows or []:
        dim_key = raw.get(key_field)
        if not dim_key:
            continue
        out.append({
            "dim_key": str(dim_key),
            "dim_name": raw.get(name_field) or str(dim_key),
            "metrics": _extract_metrics(report_id, raw),
        })
    return out
