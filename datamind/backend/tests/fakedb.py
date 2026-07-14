"""
tests/fakedb.py
=================
Minimal in-memory fake of the three report_cache tables (report_daily_fact,
report_dim_fact, report_sync_state) — just enough surface to exercise
report_cache/store.py and report_cache/read.py's exact query shapes without
a live MySQL connection, so PLAN 03's ingestion/read tests are fast and
hermetic. Not a general SQL engine: routes by which table name appears in
the SQL string, since store.py/read.py only ever issue a small, fixed set of
query shapes against these three tables.
"""


class FakeCursor:
    def __init__(self, db: dict):
        self.db = db
        self._result = []

    def execute(self, sql, params=()):
        sql_lower = sql.lower()
        if "report_daily_fact" in sql_lower:
            self._exec_daily_fact(sql_lower, params)
        elif "report_dim_fact" in sql_lower:
            self._exec_dim_fact(sql_lower, params)
        elif "report_sync_state" in sql_lower:
            self._exec_sync_state(sql_lower, params)
        else:
            raise NotImplementedError(f"FakeCursor doesn't recognize this SQL: {sql[:120]!r}")

    def _exec_daily_fact(self, sql, params):
        table = self.db.setdefault("report_daily_fact", {})
        if sql.strip().startswith("insert"):
            tenant_id, report_id, shop_id, business_date, metrics_json, status = params
            key = (tenant_id, report_id, shop_id, business_date)
            table[key] = {
                "tenant_id": tenant_id, "report_id": report_id, "shop_id": shop_id,
                "business_date": business_date, "metrics": metrics_json, "status": status,
                "fetched_at": "NOW",
            }
        elif sql.strip().startswith("select"):
            tenant_id, report_id, shop_id, start, end = params
            rows = [
                dict(r) for r in table.values()
                if r["tenant_id"] == tenant_id and r["report_id"] == report_id
                and r["shop_id"] == shop_id and start <= r["business_date"] <= end
            ]
            rows.sort(key=lambda r: r["business_date"])
            self._result = rows
        else:
            raise NotImplementedError(sql)

    def _exec_dim_fact(self, sql, params):
        table = self.db.setdefault("report_dim_fact", {})
        if sql.strip().startswith("insert"):
            (tenant_id, report_id, shop_id, period_month, dim_type, dim_key, dim_name,
             metrics_json, status) = params
            key = (tenant_id, report_id, shop_id, period_month, dim_type, dim_key)
            table[key] = {
                "tenant_id": tenant_id, "report_id": report_id, "shop_id": shop_id,
                "period_month": period_month, "dim_type": dim_type, "dim_key": dim_key,
                "dim_name": dim_name, "metrics": metrics_json, "status": status, "fetched_at": "NOW",
            }
        elif sql.strip().startswith("select"):
            tenant_id, report_id, shop_id, period_month = params
            rows = [
                dict(r) for r in table.values()
                if r["tenant_id"] == tenant_id and r["report_id"] == report_id
                and r["shop_id"] == shop_id and r["period_month"] == period_month
            ]
            rows.sort(key=lambda r: r["dim_name"] or "")
            self._result = rows
        else:
            raise NotImplementedError(sql)

    def _exec_sync_state(self, sql, params):
        table = self.db.setdefault("report_sync_state", {})
        tenant_id, report_id, shop_id, period, grain, status, error = params
        key = (tenant_id, report_id, shop_id, period, grain)
        existing = table.get(key)
        attempts = (existing["attempts"] + 1) if existing else 1
        table[key] = {
            "tenant_id": tenant_id, "report_id": report_id, "shop_id": shop_id,
            "period": period, "grain": grain, "status": status, "attempts": attempts,
            "last_error": error, "fetched_at": "NOW",
        }

    def fetchall(self):
        return [dict(r) for r in self._result]

    def fetchone(self):
        return dict(self._result[0]) if self._result else None

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.db: dict = {}
        self.committed = False
        self.rolled_back = False

    def cursor(self, dictionary=False):
        return FakeCursor(self.db)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass

    # test-only helpers, not part of the real mysql-connector interface
    def daily_fact_rows(self):
        return list(self.db.get("report_daily_fact", {}).values())

    def dim_fact_rows(self):
        return list(self.db.get("report_dim_fact", {}).values())

    def sync_state_rows(self):
        return list(self.db.get("report_sync_state", {}).values())
