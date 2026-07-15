"""
Run a SQL migration file against the core DataMind DB.

Usage (from datamind/backend/):
    python scripts/run_migration.py scripts/migrations/2026_07_report_profile.sql

Uses pool.get_internal_conn() — the same helper integrations.py uses — which
resolves DATAMIND_DB_* first, then DB_*. (Plain db.get_connection() reads only
DB_*, which is blank in a standard .env, and fails.)
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main(path: str) -> None:
    with open(path, encoding="utf-8") as f:
        sql = f.read()

    from pool import get_internal_conn

    conn = get_internal_conn()
    try:
        cursor = conn.cursor()
        no_comments = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        statements = [s.strip() for s in no_comments.split(";") if s.strip()]
        for stmt in statements:
            cursor.execute(stmt)
            print(f"OK: {stmt.splitlines()[0][:80]}")
        conn.commit()
        print(f"Applied {len(statements)} statement(s) from {path}")
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
