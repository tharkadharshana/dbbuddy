"""
tests/test_forecast.py — PLAN 06 forecast + growth over the cached series.

Hermetic: the cached daily series (read.get_daily_facts) and the tier window are
monkeypatched; Prophet runs for real on a synthetic rising series (fast at ~90
points). Skips if prophet isn't installed in the environment.
"""

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from report_cache import tiers
from report_cache.insights import forecast as fc_mod
from report_cache.insights import trends as tr_mod

prophet = pytest.importorskip("prophet")   # skip cleanly if Prophet absent


def _series_facts(n, start_value=100.0, step=2.0, end=None):
    """n consecutive daily facts ending today, net_sales rising linearly."""
    end = end or date.today()
    return [{"business_date": end - timedelta(days=n - 1 - i),
             "metrics": {"net_sales": start_value + step * i}} for i in range(n)]


@pytest.fixture(autouse=True)
def _window(monkeypatch):
    monkeypatch.setattr(tiers, "window_start", lambda t: date.today() - timedelta(days=400))
    yield


def test_forecast_rising_series_projects_forward(monkeypatch):
    facts = _series_facts(90, start_value=100.0, step=3.0)
    monkeypatch.setattr(fc_mod.read, "get_daily_facts", lambda *a, **k: facts)

    out = fc_mod.forecast_metric(conn=None, tenant_id="t1", metric="net_sales", horizon_days=30)
    assert out["status"] == "ok"
    assert out["horizon_days"] == 30
    assert len(out["forecast"]) == 30
    pt = out["forecast"][0]
    assert pt["yhat_lower"] <= pt["yhat"] <= pt["yhat_upper"]   # confidence band ordered
    # a clearly rising history should predict above the earliest history point
    assert out["summary"]["next_30_avg"] > out["history"][0]["value"]


def test_forecast_insufficient_history_is_graceful(monkeypatch):
    monkeypatch.setattr(fc_mod.read, "get_daily_facts", lambda *a, **k: _series_facts(10))
    out = fc_mod.forecast_metric(conn=None, tenant_id="t1", metric="net_sales", horizon_days=30)
    assert out["status"] == "insufficient_history"
    assert "history" not in out or "forecast" not in out


def test_forecast_rejects_ratio_metric(monkeypatch):
    monkeypatch.setattr(fc_mod.read, "get_daily_facts", lambda *a, **k: _series_facts(90))
    out = fc_mod.forecast_metric(conn=None, tenant_id="t1", metric="gross_margin_pct", horizon_days=30)
    assert out["status"] == "not_additive"


def test_forecast_rejects_non_daily_cacheable_report(monkeypatch):
    out = fc_mod.forecast_metric(conn=None, tenant_id="t1", metric="receipt_count",
                                 report_id="receipts", horizon_days=30)
    assert out["status"] == "unsupported"


def test_horizon_capped(monkeypatch):
    monkeypatch.setattr(fc_mod.read, "get_daily_facts", lambda *a, **k: _series_facts(90))
    monkeypatch.setattr(fc_mod, "_MAX_HORIZON_DAYS", 60)
    out = fc_mod.forecast_metric(conn=None, tenant_id="t1", horizon_days=365)
    assert out["horizon_days"] == 60


def test_growth_summary_month_over_month(monkeypatch):
    # two full months: May totals 300, June totals 600 → +100% MoM
    facts = [
        {"business_date": date(2026, 5, 10), "metrics": {"net_sales": 100.0}},
        {"business_date": date(2026, 5, 20), "metrics": {"net_sales": 200.0}},
        {"business_date": date(2026, 6, 10), "metrics": {"net_sales": 600.0}},
    ]
    monkeypatch.setattr(tr_mod.read, "get_daily_facts", lambda *a, **k: facts)
    out = tr_mod.growth_summary(conn=None, tenant_id="t1", metric="net_sales", months=6)
    assert out["status"] == "ok"
    assert out["months"][-1]["value"] == 600.0
    assert out["months"][-1]["mom_pct"] == 100.0
    assert out["months"][0]["mom_pct"] is None   # first month has no prior


def test_growth_rejects_ratio_metric(monkeypatch):
    monkeypatch.setattr(tr_mod.read, "get_daily_facts", lambda *a, **k: [])
    out = tr_mod.growth_summary(conn=None, tenant_id="t1", metric="gross_margin_pct")
    assert out["status"] == "not_additive"
