#!/usr/bin/env python3
"""
census_user_brands.py
=====================
Read-only pre-flight for the multi-brand identity migration.

Answers the one question the migration cannot guess: which brand does each
existing user belong to? Users are matched to a brand through their
integration; anyone without an integration has no derivable brand and must be
resolved by hand before the migration runs.

Usage (from datamind/backend/):
    python scripts/census_user_brands.py

Prints nothing sensitive beyond email local-parts already in the DB. Makes no
writes of any kind.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    from pool import get_internal_conn

    conn = get_internal_conn()
    try:
        cur = conn.cursor(dictionary=True)

        print("=== Users by integration provider ===")
        cur.execute(
            """
            SELECT COALESCE(i.provider_id, '(none)') AS provider,
                   COUNT(DISTINCT u.email)           AS users
            FROM users u
            LEFT JOIN user_integrations i ON i.user_email = u.email
            GROUP BY provider
            ORDER BY users DESC
            """
        )
        rows = cur.fetchall()
        for r in rows:
            print(f"  {r['provider']:<12} {r['users']}")

        print("\n=== Partner rows available to map onto ===")
        cur.execute(
            "SELECT partner_key, partner_name, provider_id, active FROM embed_partners"
        )
        partners = cur.fetchall()
        for p in partners:
            state = "active" if p["active"] else "INACTIVE"
            print(f"  {p['provider_id']:<12} {p['partner_name']:<20} {p['partner_key']}  [{state}]")

        by_provider: dict = {}
        for p in partners:
            by_provider.setdefault(p["provider_id"], []).append(p)

        print("\n=== Derivation check ===")
        blocked = False
        for r in rows:
            provider = r["provider"]
            if provider == "(none)":
                continue
            candidates = by_provider.get(provider, [])
            if len(candidates) == 1:
                print(f"  OK       {provider}: {r['users']} user(s) -> {candidates[0]['partner_key']}")
            elif not candidates:
                print(f"  BLOCKED  {provider}: {r['users']} user(s) but NO partner row exists")
                blocked = True
            else:
                # More than one brand already shares this provider, so
                # provider -> brand is no longer a function. The migration has
                # to run before a second brand is added, not after.
                names = ", ".join(c["partner_key"] for c in candidates)
                print(f"  BLOCKED  {provider}: {r['users']} user(s) but {len(candidates)} partner rows ({names})")
                blocked = True

        orphans = next((r["users"] for r in rows if r["provider"] == "(none)"), 0)
        print(f"\n=== Users with no integration: {orphans} ===")
        if orphans:
            cur.execute(
                """
                SELECT u.email, u.created_at
                FROM users u
                LEFT JOIN user_integrations i ON i.user_email = u.email
                WHERE i.user_email IS NULL
                ORDER BY u.created_at
                LIMIT 50
                """
            )
            for r in cur.fetchall():
                print(f"  {r['created_at']}  {r['email']}")
            if orphans > 50:
                print(f"  ... and {orphans - 50} more")
            print(
                "\n  These have no derivable brand. Assign each a partner_key by hand\n"
                "  (production signup-domain history) before running the migration."
            )

        print()
        if blocked or orphans:
            print("RESULT: resolve the items above before migrating.")
            sys.exit(1)
        print("RESULT: every user maps to exactly one brand. Safe to migrate.")
        cur.close()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
