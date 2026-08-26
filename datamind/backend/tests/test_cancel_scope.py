"""cancel_subscription must never touch a trial.

A trial exists only in DataMind. Salesplay reports subscribe_status=0 for every
merchant who has not paid, so the sync-down in embed.py fires on every trial
user's page load — if that cancels trials, a merchant loses access the first
time they refresh. subscribe_to_plan is the opposite case: it clears the trial
on purpose, because a paid plan is replacing it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import billing


class _FakeCursor:
    def __init__(self, sql_log):
        self.sql_log = sql_log
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.sql_log.append(" ".join(sql.split()))

    def fetchone(self):
        return {"validity_days": 30}

    def close(self):
        pass


class _FakeConn:
    def __init__(self, sql_log):
        self.sql_log = sql_log
        self.autocommit = True

    def cursor(self, dictionary=False):
        return _FakeCursor(self.sql_log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _run(fn, monkeypatch):
    sql_log = []
    monkeypatch.setattr(billing, "_get_conn", lambda: _FakeConn(sql_log))
    fn()
    return [s for s in sql_log if "UPDATE user_subscriptions" in s and "cancelled" in s]


def test_sync_down_cancel_leaves_trials_alone(monkeypatch):
    updates = _run(lambda: billing.cancel_subscription("merchant@example.com"), monkeypatch)
    assert updates, "cancel_subscription ran no cancelling UPDATE"
    for sql in updates:
        assert "'trial'" not in sql, f"sync-down cancel would kill trials: {sql}"
        assert "status = 'active'" in sql


def test_subscribing_still_clears_the_trial(monkeypatch):
    updates = _run(
        lambda: billing.subscribe_to_plan("merchant@example.com", 1, period_days=30),
        monkeypatch,
    )
    assert updates, "subscribe_to_plan ran no cancelling UPDATE"
    assert any("'trial'" in sql for sql in updates), \
        "paying must retire the trial row, else the merchant keeps two subscriptions"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
