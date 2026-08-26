#!/usr/bin/env python3
"""
seed_embed_partners.py
======================
One-time script to register embed partner rows in the database.
Run once after the backend has started (so bootstrap_embed_tables has run).

Usage:
    cd datamind/backend
    python scripts/seed_embed_partners.py

Generates a fresh partner key for each named brand if none exists yet.
For any brand beyond these bootstrap rows, use scripts/add_brand.py.
Prints the iframe tag to give to each partner when done.
"""

import os
import sys
import secrets

# Allow running from the backend directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import mysql.connector

PARTNERS = [
    {
        "provider_id":     "salesplay",
        "partner_name":    "Salesplay",
        "allowed_origins": os.getenv(
            "SALESPLAY_EMBED_ORIGINS",
            "https://app.salesplay.io,https://backoffice.salesplay.io",
        ),
        "branding": {
            "product_name":  "SalesPlay AI",
            "company_name":  "Salesplay",
            "brand_slug":    "salesplay",
            "primary_color": "#f59e0b",
            "app_domains":   [d.strip() for d in os.getenv(
                "SALESPLAY_APP_DOMAINS", "ai.salesplay.com").split(",") if d.strip()],
        },
        "key_prefix": "sp_live_",
    },
    {
        "provider_id":     "loyverse",
        "partner_name":    "Loyverse",
        "allowed_origins": os.getenv(
            "LOYVERSE_EMBED_ORIGINS",
            "https://r.loyverse.com,https://loyverse.com",
        ),
        "branding": {
            "product_name":  "Ask Your Loyverse Data",
            "company_name":  "Loyverse",
            "brand_slug":    "loyverse",
            "primary_color": "#6366f1",
            "app_domains":   [d.strip() for d in os.getenv(
                "LOYVERSE_APP_DOMAINS", "").split(",") if d.strip()],
        },
        "key_prefix": "ly_live_",
    },
]


def get_conn():
    return mysql.connector.connect(
        host     = os.getenv("DATAMIND_DB_HOST", os.getenv("DB_HOST", "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT", "3306"))),
        database = os.getenv("DATAMIND_DB_NAME", os.getenv("DB_NAME", "")),
        user     = os.getenv("DATAMIND_DB_USER", os.getenv("DB_USER", "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


def generate_key(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(18)


def seed():
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)

    import json

    print("\nDataMind Embed Partner Seeder")
    print("=" * 50)

    for p in PARTNERS:
        # Keyed on partner_name, not provider_id: several brands can share one
        # provider (Salesplay, Sellmo, any future whitelabel), so provider_id
        # no longer identifies a single row.
        cursor.execute(
            "SELECT partner_key, active FROM embed_partners WHERE partner_name = %s LIMIT 1",
            (p["partner_name"],)
        )
        existing = cursor.fetchone()

        if existing:
            print(f"\n  {p['partner_name']} ({p['provider_id']})")
            print(f"    Already registered: {existing['partner_key']}")
            print(f"    Active: {'yes' if existing['active'] else 'NO (set active=1 to re-enable)'}")
            key = existing["partner_key"]
        else:
            key = generate_key(p["key_prefix"])
            cursor.execute("""
                INSERT INTO embed_partners
                  (partner_key, partner_name, provider_id, allowed_origins, branding, active)
                VALUES (%s, %s, %s, %s, %s, 1)
            """, (
                key,
                p["partner_name"],
                p["provider_id"],
                p["allowed_origins"],
                json.dumps(p["branding"]),
            ))
            conn.commit()
            print(f"\n  {p['partner_name']} ({p['provider_id']})")
            print(f"    Created partner key: {key}")

        print(f"\n    Iframe tag for {p['partner_name']}:")
        print(f"    <iframe")
        print(f"      src=\"https://datamind.ai/src/embed/embed.html?pk={key}\"")
        print(f"      width=\"420\" height=\"680\" frameborder=\"0\"")
        print(f"      allow=\"clipboard-write\"")
        print(f"      style=\"border-radius:12px;\"")
        print(f"    ></iframe>")

    cursor.close()
    conn.close()

    print("\n" + "=" * 50)
    print("Done. Update EMBED_ALLOWED_ORIGINS in .env with the partner domains")
    print("before deploying to production.\n")


if __name__ == "__main__":
    seed()
