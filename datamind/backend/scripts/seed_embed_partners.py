#!/usr/bin/env python3
"""
seed_embed_partners.py
======================
One-time script to register embed partner rows in the database.
Run once after the backend has started (so bootstrap_embed_tables has run).

Usage:
    cd datamind/backend
    python scripts/seed_embed_partners.py

Generates a fresh partner key for each provider if none exists yet.
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
from embed import SALESPLAY_BRANDING

PARTNERS = [
    {
        "provider_id":     "salesplay",
        "partner_name":    "Salesplay",
        "allowed_origins": os.getenv(
            "SALESPLAY_EMBED_ORIGINS",
            "https://app.salesplay.io,https://backoffice.salesplay.io",
        ),
        "branding": SALESPLAY_BRANDING,
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
            "accent_color": "#6366f1",
            "product_name": "Ask Your Loyverse Data",
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
        # Check if a row already exists for this provider
        cursor.execute(
            "SELECT partner_key, active FROM embed_partners WHERE provider_id = %s LIMIT 1",
            (p["provider_id"],)
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
