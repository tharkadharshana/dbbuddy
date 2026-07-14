"""
run_migration.py
=================
Generic .sql migration runner for DataMind's internal/core DB (the one
holding sp_*, user_integrations, and now report_cache tables). Statements
are split on ';' (same convention as integrations.py _split_sql) — DDL only,
no stored procedures/triggers with embedded semicolons.

Connects via pool.get_internal_conn() — NOT db.get_connection(). This
matters: db.get_connection(db_config=None) only reads DB_HOST/DB_NAME/
DB_USER/DB_PASSWORD, which are the "user default DB" fallback and are blank
in most .env files. The actual core DB credentials live in DATAMIND_DB_*,
and pool.py's get_internal_conn() is the one helper with the correct
DATAMIND_DB_* -> DB_* fallback chain (same one integrations.py uses
everywhere else for this DB) — see pool.py:_build_pool().

Usage:
    cd datamind/backend
    python scripts/run_migration.py scripts/migrations/2026_07_report_cache.sql

Safe to re-run — migration files use IF NOT EXISTS / IF EXISTS guards.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pool
from logger import get_logger

log = get_logger(__name__)


def _split_sql(sql: str) -> list:
    return [s.strip() for s in sql.split(";") if s.strip()]


def run(sql_path: str):
    with open(sql_path, encoding="utf-8") as f:
        sql = f.read()

    statements = _split_sql(sql)
    conn = pool.get_internal_conn()
    cursor = conn.cursor()
    try:
        for i, stmt in enumerate(statements, 1):
            cursor.execute(stmt)
            log.info("Migration statement executed", index=i, total=len(statements))
        conn.commit()
        log.info("Migration complete", file=sql_path, statements=len(statements))
        print(f"Migration complete: {len(statements)} statement(s) applied from {sql_path}")
    except Exception as exc:
        conn.rollback()
        log.error("Migration failed", file=sql_path, error=str(exc))
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/run_migration.py <path/to/migration.sql>")
        sys.exit(1)
    run(sys.argv[1])
