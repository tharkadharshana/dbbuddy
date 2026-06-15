"""
One-time migration: TinyDB users.json → MySQL users + user_settings tables.
Also populates connected_providers from user_integrations for each user.
Run: python migrate_users.py
"""
import os, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from auth import bootstrap_auth_tables, _conn

USERS_JSON = Path(__file__).parent / "data" / "users.json"


def get_connected_providers_for_user(email: str) -> list:
    """Read provider IDs directly from user_integrations."""
    try:
        db = _conn()
        cur = db.cursor(dictionary=True)
        cur.execute(
            "SELECT DISTINCT provider_id FROM user_integrations WHERE user_email = %s",
            (email,)
        )
        rows = cur.fetchall()
        cur.close(); db.close()
        return [r["provider_id"] for r in rows if r.get("provider_id")]
    except Exception as e:
        print(f"  WARN could not read integrations for {email}: {e}")
        return []


def migrate():
    print("Bootstrapping MySQL tables (adds missing columns if needed)...")
    bootstrap_auth_tables()

    db = _conn()
    cur = db.cursor()

    # Fix any existing users whose connected_providers is empty/null
    print("\nFixing connected_providers for all users in MySQL...")
    cur.execute("SELECT user_email FROM user_settings")
    existing_emails = [r[0] for r in cur.fetchall()]
    for email in existing_emails:
        pids = get_connected_providers_for_user(email)
        if pids:
            cur.execute(
                "UPDATE user_settings SET connected_providers = %s, onboarding_complete = 1 WHERE user_email = %s",
                (json.dumps(pids), email)
            )
            print(f"  FIXED {email} → {pids}")
        else:
            print(f"  OK    {email} (no integrations found)")
    db.commit()

    # Migrate users from TinyDB if file exists
    if USERS_JSON.exists():
        with open(USERS_JSON) as f:
            data = json.load(f)
        rows = list(data.get("_default", {}).values())
        print(f"\nMigrating {len(rows)} user(s) from users.json...")
        for u in rows:
            email = u["email"].lower()
            name  = u.get("name", email)
            pw_hash = u.get("password_hash", "")
            s = u.get("settings", {})

            cur.execute("SELECT email FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                print(f"  SKIP {email} (already in MySQL)")
                continue

            cur.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (%s,%s,%s,%s)",
                (name, email, pw_hash, u.get("created_at"))
            )
            pids = get_connected_providers_for_user(email)
            cur.execute("""
                INSERT INTO user_settings
                  (user_email, gemini_api_key, deepseek_api_key, db_configs,
                   active_db_index, default_llm, theme, connected_providers)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                email,
                s.get("gemini_api_key", ""),
                s.get("deepseek_api_key", ""),
                json.dumps(s.get("db_configs", [])),
                s.get("active_db_index", 0),
                s.get("default_llm", "gemini"),
                s.get("theme", "dark"),
                json.dumps(pids),
            ))
            db.commit()
            print(f"  OK  {email} → connected_providers={pids}")
    else:
        print("\nNo users.json found — skipping TinyDB migration.")

    cur.close(); db.close()
    print("\nDone.")


if __name__ == "__main__":
    migrate()
