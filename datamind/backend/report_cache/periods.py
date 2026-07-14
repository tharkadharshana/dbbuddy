"""
report_cache/periods.py
=========================
Pure date/period helpers for the cache's open/closed freshness model
(docs/09_Report_Cache_Plan_Review.md C4, PLAN_03 Step 2).

A period is "open" while it still contains today — POS data mutates after
the fact (refunds, voids, corrections), so an open period's cached numbers
are never final. "finalized" is a third, stricter state set only by PLAN 04's
re-finalization job (a deliberate re-fetch of a trailing window to catch late
edits) — nothing in this module ever produces "finalized".
"""

import calendar
from datetime import date, timedelta
from typing import List, Literal, Tuple, Union

Grain = Literal["day", "month"]


def month_bounds(period_month: date) -> Tuple[date, date]:
    """First and last day of period_month's calendar month. period_month need
    not itself be the 1st — only year/month are used."""
    first = period_month.replace(day=1)
    last_day = calendar.monthrange(first.year, first.month)[1]
    last = first.replace(day=last_day)
    return first, last


def is_open_period(business_date_or_month: date, today: date, grain: Grain) -> bool:
    """Does this period contain `today`? grain='day' -> exact date match.
    grain='month' -> same calendar year+month (period value need not be the 1st)."""
    if grain == "day":
        return business_date_or_month == today
    if grain == "month":
        return (business_date_or_month.year, business_date_or_month.month) == (today.year, today.month)
    raise ValueError(f"Unknown grain: {grain!r}")


def status_for(period: date, today: date, grain: Grain) -> Union[Literal["open"], Literal["closed"]]:
    """'open' if the period contains today, else 'closed'. Never returns
    'finalized' — see module docstring."""
    return "open" if is_open_period(period, today, grain) else "closed"


def daterange_to_months(start: date, end: date) -> List[date]:
    """List of month-first dates spanning [start, end] inclusive."""
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end})")

    months = []
    cur = start.replace(day=1)
    end_month = end.replace(day=1)
    while cur <= end_month:
        months.append(cur)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


def daterange_days(start: date, end: date) -> List[date]:
    """List of every calendar day in [start, end] inclusive. Used by
    report_cache/read.py:coverage() to find missing days."""
    if start > end:
        raise ValueError(f"start ({start}) must be <= end ({end})")
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days
