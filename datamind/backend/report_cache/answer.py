"""
report_cache/answer.py — answer a metric question from cache when valid,
live from the report API otherwise. Owns the additivity rules.

Decision tree (deliberately simple — correctness first):

  LIVE exact-range fetch when ANY of:
    - a specific shop/cashier filter (cache holds shop_id='all' only)
    - the range touches the current (open) month — POS data mutates
    - the report is live-only (point-in-time stock/inventory reports)
    - month-grain report and the range isn't month-aligned
    - a non-additive metric is requested across >1 cached period
  Live results for exactly one closed month (all shops) are written through
  to the cache.

  CACHE otherwise: read facts, SUM additive metrics, recompute ratios from
  summed numerator/denominator (never average averages), and any month not
  yet cached is fetched+cached first (write-through) using the live token.

Everything raises plain ValueError with a model-readable message — these
surface as correctable tool errors in the tool-calling loop.
"""

from datetime import date, timedelta

from logger import get_logger

from . import read
from .client import ReportAPIClient
from .ingest import is_month_cached, month_end, warm_months_async
from .normalize import normalize_dim_rows, normalize_metrics, separators
from .registry import REPORTS

log = get_logger(__name__)


def months_covering(start: date, end: date) -> list:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def last_closed_day(today: date = None) -> date:
    """Last day of the most recent fully closed month."""
    today = today or date.today()
    return date(today.year, today.month, 1) - timedelta(days=1)


def aggregate(report, parts: list, requested=None) -> tuple:
    """Combine per-period metric dicts additivity-correctly.
    Returns (metrics, dropped) — `dropped` lists non-additive metrics that
    cannot be combined across periods (caller decides to go live or explain)."""
    keys = list(requested) if requested else (
        [m.key for m in report.metrics] or
        sorted({k for p in parts for k in p}))
    sums, dropped = {}, []

    def _sum(key):
        vals = [p[key] for p in parts if p.get(key) is not None]
        return round(sum(vals), 4) if vals else None

    for key in keys:
        m = report.metric(key)
        agg = m.agg if m else ("sum" if len(parts) == 1 else "non_additive")
        if agg == "sum":
            sums[key] = _sum(key)
        elif agg == "ratio":
            num, den = _sum(m.num), _sum(m.den)
            sums[key] = round(num / den, 4) if num is not None and den else None
        elif len(parts) == 1:
            sums[key] = parts[0].get(key)      # single period: stored value is exact
        else:
            dropped.append(key)
    return sums, dropped


def _requested_all_combinable(report, requested, n_periods: int) -> bool:
    if n_periods <= 1:
        return True
    if not report.metrics:
        return False                # long-tail undeclared metrics: never combine
    if not requested:
        return True                 # default ask: combinable metrics are answered,
                                    # non-additive ones dropped with a note
    return all((report.metric(k) and report.metric(k).agg != "non_additive")
               for k in requested)


def _is_empty_row(row: dict) -> bool:
    """True if every metric in the row is None/zero — e.g. a product that
    exists in the tenant's catalog but had no activity in the period. Noise
    the user never asked for; dropped before the row count reaches them."""
    values = [v for k, v in row.items() if k != "name"]
    return not values or all(v is None or v == 0 for v in values)


def _merge_dim_rows(report, dim_facts: list, requested=None, top_n=None) -> list:
    """Merge cached dimensional rows across months by dim_key (sum additive,
    recompute ratios). Returns rows sorted by the first summable metric desc,
    with all-zero/empty rows dropped."""
    by_key = {}
    for _, dim_key, dim_label, metrics in dim_facts:
        by_key.setdefault(dim_key, {"label": dim_label, "parts": []})["parts"].append(metrics)
    rows = []
    for dim_key, entry in by_key.items():
        merged, _ = aggregate(report, entry["parts"], requested)
        row = {"name": entry["label"], **merged}
        if not _is_empty_row(row):
            rows.append(row)
    sort_keys = [m.key for m in report.metrics if m.agg == "sum"]
    if requested:
        sort_keys = [k for k in requested if k in sort_keys] or sort_keys
    if sort_keys:
        rows.sort(key=lambda r: r.get(sort_keys[0]) or 0, reverse=True)
    return rows[:top_n] if top_n else rows


def _live_params(start: date, end: date, shop_id: str, cashier: str = None) -> dict:
    params = {"start_date": start.isoformat(), "end_date": end.isoformat(),
              "shop_id": shop_id or "all"}
    if cashier:
        params["cashier_id"] = cashier    # matched against invoice_cashier_name
    return params


def _answer_live(conn, tenant_id, report, start, end, shop_id, cashier, token,
                 number_format, requested, top_n):
    if not token:
        raise ValueError(
            "This question needs a live report fetch, but there is no active "
            "POS session right now. Cached monthly summaries are still available "
            "for fully past months with no shop/cashier filter.")
    dec, thou = separators(number_format)
    client = ReportAPIClient(token)
    if report.kind == "dimensional":
        raw = []
        page = 1
        # reuse ingest's pager for month fetches only; exact ranges page here
        while page <= 10:
            payload = client.get(report.endpoint, {
                **_live_params(start, end, shop_id, cashier),
                "page": page, "per_page": 100})
            data = payload.get("data") or {}
            raw.extend(data.get("table_data") or [])
            if not (payload.get("pagination") or {}).get("has_next_page"):
                break
            page += 1
        rows = [{"name": lbl, **m}
                for _, lbl, m in normalize_dim_rows(report, raw, dec, thou)]
        rows = [r for r in rows if not _is_empty_row(r)]
        sort_keys = [m.key for m in report.metrics if m.agg == "sum"]
        if sort_keys:
            rows.sort(key=lambda r: r.get(sort_keys[0]) or 0, reverse=True)
        if top_n:
            rows = rows[:top_n]
        parts = [{k: v for k, v in r.items() if k != "name"} for r in rows]
        summary, _ = aggregate(report, parts, requested) if parts else ({}, [])
        result = {"metrics": summary, "rows": rows}
    else:
        payload = client.get(report.endpoint, {
            **_live_params(start, end, shop_id, cashier), "page": 1, "per_page": 1})
        raw_summary = (payload.get("data") or {}).get("summary") or {}
        metrics = normalize_metrics(report, raw_summary, dec, thou)
        if requested:
            known = {k: metrics.get(k) for k in requested if k in metrics}
            # ratios aren't stored/normalized — recompute from parts
            for k in requested:
                m = report.metric(k)
                if m and m.agg == "ratio":
                    num, den = metrics.get(m.num), metrics.get(m.den)
                    known[k] = round(num / den, 4) if num is not None and den else None
            metrics = known
        result = {"metrics": metrics, "rows": []}

    # Write-through: an exact closed calendar month for all shops is cacheable.
    # Warm it in the BACKGROUND (never inline) so this live answer returns now
    # and the month is cached for the next identical ask.
    if (shop_id in (None, "all") and not cashier and report.cacheable
            and start.day == 1 and start.replace(day=1) == end.replace(day=1)
            and end == month_end(f"{start.year:04d}-{start.month:02d}")
            and end < date.today().replace(day=1)):
        ym = f"{start.year:04d}-{start.month:02d}"
        warm_months_async(tenant_id, report.id, [ym], token, number_format)

    result.update({"report_id": report.id, "start_date": start.isoformat(),
                   "end_date": end.isoformat(), "source": "live",
                   "shop_id": shop_id or "all"})
    return result


def answer_metric_query(conn, tenant_id: str, report_id: str, start: date,
                        end: date, shop_id: str = "all", cashier: str = None,
                        metrics=None, token: str = None, number_format=None,
                        top_n: int = None) -> dict:
    """The one entry point the report tools call. Returns
    {report_id, start_date, end_date, source, metrics, rows, [notes]}."""
    if report_id not in REPORTS:
        raise ValueError(f"Unknown report '{report_id}'.")
    report = REPORTS[report_id]
    today = date.today()
    if end > today:
        end = today
    if start > end:
        raise ValueError("start_date must be on or before end_date.")

    shop_id = shop_id or "all"
    closed_cutoff = last_closed_day(today)
    month_aligned = (report.grain == "day" or
                     (start.day == 1 and end == month_end(f"{end.year:04d}-{end.month:02d}")))
    ym_list = months_covering(start, end)

    use_cache = (shop_id == "all" and not cashier and report.cacheable
                 and end <= closed_cutoff and month_aligned
                 and _requested_all_combinable(
                     report, metrics,
                     len(ym_list) if report.grain != "day" else (end - start).days))
    if not use_cache:
        return _answer_live(conn, tenant_id, report, start, end, shop_id,
                            cashier, token, number_format, metrics, top_n)

    # ── cache path ────────────────────────────────────────────────────────────
    # If any month in the range isn't cached, do NOT fetch it inline — a
    # multi-month × paged fetch (with rate-limit sleeps) would time out the chat
    # request. Answer LIVE for the whole range now (one API call, server-side
    # aggregation) and warm the missing months in the background for next time.
    missing = [ym for ym in ym_list if not is_month_cached(conn, tenant_id, report_id, ym)]
    if missing:
        warm_months_async(tenant_id, report_id, missing, token, number_format)
        return _answer_live(conn, tenant_id, report, start, end, shop_id,
                            cashier, token, number_format, metrics, top_n)

    source = "cache"
    if report.kind == "dimensional":
        dim_facts = read.read_dim_facts(conn, tenant_id, report_id, ym_list)
        rows = _merge_dim_rows(report, dim_facts, metrics, top_n)
        summary, dropped = aggregate(
            report, [{k: v for k, v in r.items() if k != "name"} for r in rows],
            metrics)
        result = {"metrics": summary, "rows": rows}
    elif report.grain == "day":
        daily = read.read_daily_facts(conn, tenant_id, report_id, start, end)
        summary, dropped = aggregate(report, [m for _, m in daily], metrics)
        result = {"metrics": summary,
                  "rows": [{"date": d.isoformat(), **m} for d, m in daily]}
    else:
        monthly = read.read_month_facts(conn, tenant_id, report_id, ym_list)
        summary, dropped = aggregate(report, list(monthly.values()), metrics)
        result = {"metrics": summary,
                  "rows": [{"month": ym, **m} for ym, m in sorted(monthly.items())]}

    if dropped:
        result["notes"] = (
            f"Metrics {dropped} cannot be summed across periods (distinct counts "
            "/ unit values) — ask for a single month, or the exact range live.")
    result.update({"report_id": report_id, "start_date": start.isoformat(),
                   "end_date": end.isoformat(), "source": source, "shop_id": shop_id})
    return result
