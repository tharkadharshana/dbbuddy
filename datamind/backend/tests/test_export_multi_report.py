"""
tests/test_export_multi_report.py — report_tools._set_last_result.

One answer often spans several reports ("today's sales summary" = sales_summary
for the money + receipts for the count). The slot holds one result, so a plain
overwrite shipped the export whichever report ran last: the chat showed eleven
figures, the spreadsheet five, and the PDF a single column.
"""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.report_tools import _set_last_result


def _ctx():
    return SimpleNamespace(business=SimpleNamespace(last_result=None))


def test_metric_reports_merge_into_one_row():
    rctx = _ctx()
    _set_last_result(rctx, "report:sales_summary 2026-09-04..2026-09-04", None,
                     {"gross_sales": 4303.89, "net_sales": 3010.72,
                      "gross_profit": 696.15})
    _set_last_result(rctx, "report:receipts 2026-09-04..2026-09-04", None,
                     {"receipt_count": 5, "avg_receipt_value": 854.75})

    last = rctx.business.last_result
    assert len(last["data"]) == 1
    # Both reports survive — this is the actual bug.
    assert last["data"][0]["gross_sales"] == 4303.89
    assert last["data"][0]["receipt_count"] == 5
    assert set(last["columns"]) == {
        "gross_sales", "net_sales", "gross_profit",
        "receipt_count", "avg_receipt_value"}


def test_later_metric_wins_on_key_collision():
    rctx = _ctx()
    _set_last_result(rctx, "report:a 1..2", None, {"net_sales": 1})
    _set_last_result(rctx, "report:b 1..2", None, {"net_sales": 2})
    assert rctx.business.last_result["data"][0]["net_sales"] == 2


def test_row_result_replaces_rather_than_merges():
    """A detail table is a different shape; merging it into a summary row would
    invent a table nobody queried."""
    rctx = _ctx()
    _set_last_result(rctx, "report:sales_summary 1..2", None, {"net_sales": 3010.72})
    _set_last_result(rctx, "report-detail:receipts 1..2",
                     [{"receipt_number": "A1"}, {"receipt_number": "A2"}], {})

    last = rctx.business.last_result
    assert len(last["data"]) == 2
    assert last["columns"] == ["receipt_number"]
    assert "net_sales" not in last["data"][0]


def test_metric_after_rows_does_not_merge_into_a_detail_row():
    rctx = _ctx()
    _set_last_result(rctx, "report-detail:receipts 1..2", [{"receipt_number": "A1"}], {})
    _set_last_result(rctx, "report:sales_summary 1..2", None, {"net_sales": 3010.72})

    last = rctx.business.last_result
    assert last["data"] == [{"net_sales": 3010.72}]


def test_empty_result_clears():
    rctx = _ctx()
    _set_last_result(rctx, "report:x 1..2", None, {"a": 1})
    _set_last_result(rctx, "report:y 1..2", None, {})
    assert rctx.business.last_result["data"] == []


# --- document layout validation (agent._clean_document_spec) ---------------

import pytest

from mcp_server.agent import _clean_document_spec


def test_stray_column_is_dropped_quietly():
    spec = _clean_document_spec(
        {"title": "Summary", "line_columns": ["a", "b", "c", "nope"]},
        ["a", "b", "c"])
    assert spec["line_columns"] == ["a", "b", "c"]


def test_mostly_missing_layout_raises_instead_of_rendering_one_column():
    """The PDF that showed 'Receipt Count' alone under a full summary title."""
    with pytest.raises(ValueError) as exc:
        _clean_document_spec(
            {"title": "Today's Sales Summary",
             "line_columns": ["gross_sales", "net_sales", "gross_profit",
                              "receipt_count"]},
            ["receipt_count"])
    assert "not in the figures currently loaded" in str(exc.value)
    assert "gross_sales" in str(exc.value)


def test_no_surviving_columns_still_raises():
    with pytest.raises(ValueError):
        _clean_document_spec({"line_columns": ["x"]}, ["y"])


# --- percentage scaling (agent._percent_point_columns) ---------------------

from mcp_server.agent import _percent_point_columns


def test_only_percentage_ratios_are_flagged_for_scaling():
    """avg_receipt_value is a ratio too — scaling THAT by 100 would be a new
    bug, so the flag comes from the metric's own label, not the agg type."""
    flagged = _percent_point_columns(
        ["gross_sales", "gross_margin_pct", "avg_receipt_value",
         "receipt_count", "profit_margin"])
    assert flagged == ["gross_margin_pct", "profit_margin"]


def test_a_sql_column_merely_named_pct_is_left_alone():
    """Nothing guarantees a hand-written SQL column is a fraction; a value under
    1 is equally consistent with a small percentage."""
    assert _percent_point_columns(["conversion_pct", "some_rate"]) == []
