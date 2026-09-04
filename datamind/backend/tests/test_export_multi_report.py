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
