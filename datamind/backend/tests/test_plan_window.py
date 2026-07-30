"""Phase 1 (docs/16 §7 guard 2 + doc 14 Part B): the plan window is calendar-
correct, computed in ONE place, applied by clamping, and never bypassable.

These are the cases that were wrong in production:
  - Growth (12 months) refused "same month last year" by ~5 days (months * 30)
  - a range starting before the window raised instead of returning its covered
    part, so the model had no figures and could only apologise
  - a model-written created_at bound made the SQL path skip the plan window
"""
from datetime import date

import pytest

from billing import window_start
from mcp_server import safety
from mcp_server.report_tools import _clamp_to_window


# ── calendar-correct window ───────────────────────────────────────────────────

def test_window_is_calendar_months_not_30_day_blocks():
    # Growth = 12 months. "Same month last year" must be inside the window.
    assert window_start(12, today=date(2026, 7, 30)) == date(2025, 7, 30)
    # The old months*30 gave 2025-08-04 and refused five days the merchant paid for.
    assert window_start(12, today=date(2026, 7, 30)) < date(2025, 8, 4)


def test_window_crosses_year_boundary():
    assert window_start(3, today=date(2026, 2, 15)) == date(2025, 11, 15)


def test_window_clamps_day_to_short_month():
    # 1 month before 31 March is 28 Feb, not an invalid 31 Feb.
    assert window_start(1, today=date(2026, 3, 31)) == date(2026, 2, 28)


def test_pro_deep_window_does_not_overflow():
    assert window_start(200, today=date(2026, 7, 30)) == date(2009, 11, 30)


# ── clamp, don't refuse ───────────────────────────────────────────────────────

def _window_on(monkeypatch, start: date):
    monkeypatch.setattr("mcp_server.report_tools._window_start", lambda m: start)


def test_partial_overlap_is_clamped_not_refused(monkeypatch):
    """The reported symptom: plan opens 20 Apr, merchant asks for April.
    Must return 20–30 Apr, not 'no data'."""
    _window_on(monkeypatch, date(2026, 4, 20))
    start, clamped = _clamp_to_window(3, date(2026, 4, 1), date(2026, 4, 30))
    assert start == date(2026, 4, 20)
    assert clamped is True


def test_range_fully_inside_window_is_untouched(monkeypatch):
    _window_on(monkeypatch, date(2026, 4, 20))
    start, clamped = _clamp_to_window(3, date(2026, 5, 1), date(2026, 5, 31))
    assert start == date(2026, 5, 1)
    assert clamped is False


def test_range_fully_outside_window_still_raises(monkeypatch):
    _window_on(monkeypatch, date(2026, 4, 20))
    with pytest.raises(ValueError):
        _clamp_to_window(3, date(2026, 1, 1), date(2026, 1, 31))


# ── the plan bound is not bypassable ──────────────────────────────────────────

def test_model_supplied_date_filter_does_not_bypass_the_plan_bound():
    """safety.py used to trust any query that already filtered on created_at,
    so the model could write its own bound and read outside the paid window."""
    sql = ("SELECT SUM(total) FROM sp_receipts "
           "WHERE created_at >= '2019-01-01'")
    out = safety.enforce_date_filter(sql, 3)
    assert "2019-01-01" in out            # the model's own bound is kept
    assert "INTERVAL '3' MONTH" in out    # sqlglot's mysql rendering      # and the plan bound is AND-ed on top


def test_plan_bound_still_injected_when_model_wrote_none():
    out = safety.enforce_date_filter("SELECT SUM(total) FROM sp_receipts", 3)
    assert "INTERVAL '3' MONTH" in out    # sqlglot's mysql rendering


def test_reference_tables_are_not_date_filtered():
    out = safety.enforce_date_filter("SELECT product_name FROM sp_products", 3)
    assert "INTERVAL" not in out
