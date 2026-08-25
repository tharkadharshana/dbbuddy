#!/usr/bin/env python3
"""
migrate_multi_brand_identity.py
===============================
Move account identity from `email` to `(email, partner_key)`.

The same email address can belong to different merchants under different
brands. Today they collide onto one users row and one tenant_id, so two
businesses read each other's sales data. This migration gives users a real
composite key plus a MySQL-generated `account_key` scalar that every other
table references.

Stored `user_integrations.table_prefix` values are NEVER touched, so no tenant
changes and nothing resyncs.

Usage (from datamind/backend/), with every backend instance STOPPED:
    python scripts/migrate_multi_brand_identity.py --dry-run
    python scripts/migrate_multi_brand_identity.py --apply
    python scripts/migrate_multi_brand_identity.py --rollback

Users with no integration have no derivable brand. Give them one explicitly:
    --orphan-partner-key sp_live_xxxx
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Tables whose user_email column holds an ACCOUNT identity.
#
# Deliberately excludes sp_customers.email and sp_shops.email: those are POS
# customer and shop addresses synced from the provider, not our users.
# Migrating them would corrupt merchant data.
CHILD_TABLES = [
    "addon_purchases",
    "conversations",
    "credit_usage_log",
    "integration_records",
    "integration_sync_state",
    "llm_usage_log",
    "subscription_usage",
    "usage_log",
    "user_api_keys",
    "user_credits",
    "user_integrations",
    "user_subscriptions",
    "widget_feedback",
]

ACCOUNT_KEY_LEN = 320  # partner_key(64) + ':' + email(254) = 319


def _table_exists(cur, table):
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return cur.fetchone()[0] > 0


def _column_exists(cur, table, column):
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    return cur.fetchone()[0] > 0


def _column_len(cur, table, column):
    cur.execute(
        "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _primary_key_cols(cur, table):
    cur.execute(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND CONSTRAINT_NAME = 'PRIMARY' ORDER BY ORDINAL_POSITION",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def _surrogate_pk(cur, table):
    """Name of the table's AUTO_INCREMENT primary key column, or None.

    A live users table may carry an `id INT AUTO_INCREMENT PRIMARY KEY` with
    email merely UNIQUE, even though init_users_table() declares email as the
    key -- CREATE TABLE IF NOT EXISTS never rewrote an older table. MySQL and
    MariaDB both refuse to drop a primary key off an auto_increment column
    (errno 1075), so on that shape the composite constraint has to be a UNIQUE
    key instead. Same guarantee, different index.
    """
    pk = _primary_key_cols(cur, table)
    if len(pk) != 1:
        return None
    cur.execute(
        "SELECT EXTRA FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, pk[0]),
    )
    row = cur.fetchone()
    return pk[0] if row and "auto_increment" in (row[0] or "").lower() else None


def _unique_indexes_on(cur, table, column):
    """Single-column UNIQUE index names covering `column` (never PRIMARY)."""
    cur.execute(
        "SELECT INDEX_NAME FROM INFORMATION_SCHEMA.STATISTICS s "
        "WHERE s.TABLE_SCHEMA = DATABASE() AND s.TABLE_NAME = %s "
        "AND s.NON_UNIQUE = 0 AND s.INDEX_NAME <> 'PRIMARY' "
        "GROUP BY s.INDEX_NAME "
        "HAVING COUNT(*) = 1 AND MAX(s.COLUMN_NAME) = %s",
        (table, column),
    )
    return [r[0] for r in cur.fetchall()]


def _index_exists(cur, table, name):
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (table, name),
    )
    return cur.fetchone()[0] > 0


def resolve_brands(cur, orphan_partner_key):
    """Map every existing user email to the partner_key it belongs to.

    Rule 1: the user's integration names a provider, and that provider's
            partner row supplies the key. Unambiguous only while each provider
            has exactly one brand -- which is why this has to run BEFORE a
            second brand is added.
    Rule 2: no integration means no derivable brand. The caller supplies one.
    """
    cur.execute("SELECT partner_key, provider_id FROM embed_partners")
    by_provider = {}
    for pk, pid in cur.fetchall():
        by_provider.setdefault(pid, []).append(pk)

    ambiguous = {p: k for p, k in by_provider.items() if len(k) > 1}
    if ambiguous:
        detail = "\n  ".join("{}: {}".format(p, ", ".join(k)) for p, k in ambiguous.items())
        raise SystemExit(
            "REFUSING: these providers already have more than one brand, so "
            "provider -> brand is no longer a function:\n  " + detail +
            "\nThis migration must run before a second brand is added."
        )

    cur.execute(
        "SELECT u.email, MIN(i.provider_id) "
        "FROM users u LEFT JOIN user_integrations i ON i.user_email = u.email "
        "GROUP BY u.email"
    )
    mapping = {}
    orphans = []
    unmapped = []
    for email, provider in cur.fetchall():
        if provider is None:
            orphans.append(email)
            continue
        keys = by_provider.get(provider)
        if not keys:
            unmapped.append((email, provider))
            continue
        mapping[email] = keys[0]

    if unmapped:
        detail = "\n  ".join("{} ({})".format(e, p) for e, p in unmapped[:20])
        raise SystemExit(
            "REFUSING: no partner row exists for these users' providers:\n  " + detail
        )

    if orphans:
        if not orphan_partner_key:
            raise SystemExit(
                "REFUSING: {} user(s) have no integration and so no derivable "
                "brand. Re-run with --orphan-partner-key <key> once you have "
                "confirmed which brand they belong to. Sample:\n  ".format(len(orphans))
                + "\n  ".join(orphans[:20])
            )
        known = set()
        for ks in by_provider.values():
            known.update(ks)
        if orphan_partner_key not in known:
            raise SystemExit(
                "REFUSING: --orphan-partner-key {} is not a known "
                "partner_key.".format(orphan_partner_key)
            )
        for e in orphans:
            mapping[e] = orphan_partner_key

    return mapping, orphans


def apply(conn, dry_run, orphan_partner_key):
    cur = conn.cursor()
    todo = []

    def run(sql, params=None, label=None):
        todo.append(label or sql.split("\n")[0][:100])
        if not dry_run:
            cur.execute(sql, params or ())

    mapping, orphans = resolve_brands(cur, orphan_partner_key)
    print("Resolved {} user(s) to a brand ({} via --orphan-partner-key).".format(
        len(mapping), len(orphans)))

    # 1. Widen every child column before anything writes a longer value into it.
    for t in CHILD_TABLES:
        if not _table_exists(cur, t):
            print("  skip (absent): " + t)
            continue
        if _column_len(cur, t, "user_email") != ACCOUNT_KEY_LEN:
            run("ALTER TABLE {} MODIFY COLUMN user_email VARCHAR({}) NOT NULL".format(
                t, ACCOUNT_KEY_LEN),
                label="widen {}.user_email -> {}".format(t, ACCOUNT_KEY_LEN))

    # 2. partner_key, then backfill. init_users_table may have added it already.
    if not _column_exists(cur, "users", "partner_key"):
        run("ALTER TABLE users ADD COLUMN partner_key VARCHAR(64) NOT NULL DEFAULT ''",
            label="add users.partner_key")
    for email, pk in mapping.items():
        run("UPDATE users SET partner_key = %s WHERE email = %s AND partner_key = ''",
            (pk, email), label="backfill partner_key for " + email)

    # 3. The generated key. Refuse if any row would produce ':email'.
    if not dry_run:
        cur.execute("SELECT COUNT(*) FROM users WHERE partner_key = ''")
        if cur.fetchone()[0]:
            raise SystemExit(
                "REFUSING: users.partner_key is still empty for some rows after backfill."
            )
    if not _column_exists(cur, "users", "account_key"):
        run("ALTER TABLE users ADD COLUMN account_key VARCHAR({}) "
            "AS (CONCAT(partner_key, ':', email)) STORED".format(ACCOUNT_KEY_LEN),
            label="add generated users.account_key")
        run("ALTER TABLE users ADD UNIQUE KEY uq_account_key (account_key)",
            label="add uq_account_key")

    # 4. Repoint children while users.email is still the raw address and still
    #    unique. Rows already carrying an account_key simply will not join.
    for t in CHILD_TABLES:
        if not _table_exists(cur, t):
            continue
        run("UPDATE {} c JOIN users u ON u.email = c.user_email "
            "SET c.user_email = u.account_key "
            "WHERE c.user_email NOT LIKE '%:%'".format(t),
            label="repoint {}.user_email -> account_key".format(t))

    # 5. The composite constraint last, in whichever form this table allows.
    #    What has to be true either way: (email, partner_key) is unique and
    #    email alone is NOT. Leaving a single-column unique on email in place
    #    would block the second brand from ever using the same address, which
    #    is the whole point of the migration.
    surrogate = _surrogate_pk(cur, "users")
    if surrogate:
        print("users has a surrogate primary key ({}) -- adding the composite "
              "constraint as a UNIQUE key and keeping it.".format(surrogate))
        if not _index_exists(cur, "users", "uq_email_partner"):
            run("ALTER TABLE users ADD UNIQUE KEY uq_email_partner (email, partner_key)",
                label="add uq_email_partner (email, partner_key)")
        for name in _unique_indexes_on(cur, "users", "email"):
            run("ALTER TABLE users DROP INDEX {}".format(name),
                label="drop single-column unique {} on users.email".format(name))
    elif _primary_key_cols(cur, "users") != ["email", "partner_key"]:
        run("ALTER TABLE users DROP PRIMARY KEY, ADD PRIMARY KEY (email, partner_key)",
            label="swap users PRIMARY KEY -> (email, partner_key)")

    print("DRY RUN, would run:" if dry_run else "Applied:")
    for t in todo:
        print("  " + t)
    if not dry_run:
        conn.commit()
        print("\nDone. {} statement(s) committed.".format(len(todo)))
    cur.close()


def rollback(conn, dry_run=False):
    cur = conn.cursor()
    todo = []

    def run(sql, label=None):
        todo.append(label or sql.split("\n")[0][:100])
        if not dry_run:
            cur.execute(sql)

    if not _column_exists(cur, "users", "account_key"):
        raise SystemExit("Nothing to roll back: users.account_key does not exist.")

    # Undo whichever shape apply() produced.
    if _index_exists(cur, "users", "uq_email_partner"):
        # Once two brands share an address, email alone is no longer unique and
        # this rollback cannot complete. Say so plainly instead of dying inside
        # an ALTER at 3am.
        cur.execute("SELECT COUNT(*) FROM (SELECT email FROM users "
                    "GROUP BY email HAVING COUNT(*) > 1) d")
        dupes = cur.fetchone()[0]
        if dupes:
            raise SystemExit(
                "REFUSING: {} email address(es) now belong to more than one "
                "brand. Rolling back would have to delete one of each pair. "
                "Restore from the pre-migration dump instead.".format(dupes))
        if not _unique_indexes_on(cur, "users", "email"):
            run("ALTER TABLE users ADD UNIQUE KEY uq_email (email)",
                label="restore single-column unique on users.email")
        run("ALTER TABLE users DROP INDEX uq_email_partner",
            label="drop uq_email_partner")
    elif _primary_key_cols(cur, "users") != ["email"]:
        run("ALTER TABLE users DROP PRIMARY KEY, ADD PRIMARY KEY (email)",
            label="restore users PRIMARY KEY -> (email)")
    for t in CHILD_TABLES:
        if not _table_exists(cur, t):
            continue
        run("UPDATE {} c JOIN users u ON u.account_key = c.user_email "
            "SET c.user_email = u.email".format(t),
            label="restore {}.user_email -> email".format(t))
    run("ALTER TABLE users DROP INDEX uq_account_key", label="drop uq_account_key")
    run("ALTER TABLE users DROP COLUMN account_key", label="drop users.account_key")
    run("ALTER TABLE users DROP COLUMN partner_key", label="drop users.partner_key")

    print("DRY RUN, would run:" if dry_run else "Rolled back:")
    for t in todo:
        print("  " + t)
    if not dry_run:
        conn.commit()
        print("\nDone. {} statement(s) committed.".format(len(todo)))
    cur.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="print what would run, change nothing")
    g.add_argument("--apply", action="store_true", help="run the migration")
    g.add_argument("--rollback", action="store_true", help="undo the migration")
    ap.add_argument("--orphan-partner-key", help="partner_key for users with no integration")
    args = ap.parse_args()

    from pool import get_internal_conn

    conn = get_internal_conn()
    try:
        if args.rollback:
            rollback(conn)
        else:
            apply(conn, dry_run=args.dry_run, orphan_partner_key=args.orphan_partner_key)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
