"""
tests/test_periods.py
=======================
Unit tests for report_cache/periods.py — pure functions, no DB/network.
Run: cd datamind/backend && pytest tests/test_periods.py -q
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from report_cache.periods import (
    daterange_days, daterange_to_months, is_open_period, month_bounds, status_for,
)


# ── month_bounds ─────────────────────────────────────────────────────────────

def test_month_bounds_mid_month():
    assert month_bounds(date(2026, 2, 15)) == (date(2026, 2, 1), date(2026, 2, 28))


def test_month_bounds_leap_year_february():
    assert month_bounds(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_month_bounds_december():
    assert month_bounds(date(2026, 12, 25)) == (date(2026, 12, 1), date(2026, 12, 31))


# ── is_open_period / status_for ──────────────────────────────────────────────

def test_is_open_period_day_exact_match():
    today = date(2026, 7, 14)
    assert is_open_period(date(2026, 7, 14), today, "day") is True
    assert is_open_period(date(2026, 7, 13), today, "day") is False


def test_is_open_period_month_same_year_month():
    today = date(2026, 7, 14)
    assert is_open_period(date(2026, 7, 1), today, "month") is True
    assert is_open_period(date(2026, 7, 31), today, "month") is True  # day component ignored
    assert is_open_period(date(2026, 6, 1), today, "month") is False


def test_is_open_period_invalid_grain_raises():
    with pytest.raises(ValueError):
        is_open_period(date(2026, 7, 14), date(2026, 7, 14), "year")


def test_status_for_open_day():
    today = date(2026, 7, 14)
    assert status_for(date(2026, 7, 14), today, "day") == "open"


def test_status_for_closed_day():
    today = date(2026, 7, 14)
    assert status_for(date(2026, 7, 13), today, "day") == "closed"


def test_status_for_open_month():
    today = date(2026, 7, 14)
    assert status_for(date(2026, 7, 1), today, "month") == "open"


def test_status_for_closed_month():
    today = date(2026, 7, 14)
    assert status_for(date(2026, 6, 1), today, "month") == "closed"


def test_status_for_never_returns_finalized():
    # finalized is set only by PLAN 04's re-finalization job, never here.
    today = date(2026, 7, 14)
    for period in (date(2020, 1, 1), date(2026, 7, 14), date(2030, 1, 1)):
        for grain in ("day", "month"):
            assert status_for(period, today, grain) in ("open", "closed")


# ── daterange_to_months ──────────────────────────────────────────────────────

def test_daterange_to_months_within_one_month():
    assert daterange_to_months(date(2026, 3, 5), date(2026, 3, 20)) == [date(2026, 3, 1)]


def test_daterange_to_months_spans_multiple_months():
    result = daterange_to_months(date(2026, 3, 15), date(2026, 5, 3))
    assert result == [date(2026, 3, 1), date(2026, 4, 1), date(2026, 5, 1)]


def test_daterange_to_months_spans_year_boundary():
    result = daterange_to_months(date(2026, 11, 1), date(2027, 2, 1))
    assert result == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1), date(2027, 2, 1)]


def test_daterange_to_months_start_after_end_raises():
    with pytest.raises(ValueError):
        daterange_to_months(date(2026, 5, 1), date(2026, 4, 1))


# ── daterange_days ───────────────────────────────────────────────────────────

def test_daterange_days_single_day():
    assert daterange_days(date(2026, 7, 14), date(2026, 7, 14)) == [date(2026, 7, 14)]


def test_daterange_days_multi_day():
    result = daterange_days(date(2026, 7, 1), date(2026, 7, 3))
    assert result == [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]


def test_daterange_days_start_after_end_raises():
    with pytest.raises(ValueError):
        daterange_days(date(2026, 7, 3), date(2026, 7, 1))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
