"""
Auth & user settings — MySQL-backed.

Users and their settings are stored in the same MySQL database as billing,
so everything is in one place, thread-safe, and transactional.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional

import mysql.connector
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "datamind-secret-change-in-production-2024")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer  = HTTPBearer(auto_error=False)


# ── DB connection (same MySQL as billing) ─────────────────────────────────────

def _get_conn():
    return mysql.connector.connect(
        host     = os.getenv("DATAMIND_DB_HOST",     os.getenv("DB_HOST",     "localhost")),
        port     = int(os.getenv("DATAMIND_DB_PORT", os.getenv("DB_PORT",     "3306"))),
        database = os.getenv("DATAMIND_DB_NAME",     os.getenv("DB_NAME",     "")),
        user     = os.getenv("DATAMIND_DB_USER",     os.getenv("DB_USER",     "root")),
        password = os.getenv("DATAMIND_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
        connection_timeout=10,
    )


# ── Bootstrap (called once on startup) ───────────────────────────────────────

def bootstrap_auth_tables():
    """Create users and user_settings tables. No-op if they already exist."""
    conn = _get_conn()
    cur  = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            name          VARCHAR(255) NOT NULL,
            email         VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uq_email (email)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_email       VARCHAR(255) NOT NULL PRIMARY KEY,
            gemini_api_key   VARCHAR(500) NOT NULL DEFAULT '',
            deepseek_api_key VARCHAR(500) NOT NULL DEFAULT '',
            db_configs       JSON,
            active_db_index  INT          NOT NULL DEFAULT 0,
            default_llm      VARCHAR(50)  NOT NULL DEFAULT 'gemini',
            theme            VARCHAR(20)  NOT NULL DEFAULT 'dark',
            updated_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    conn.commit()
    conn.close()


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": email, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ── User CRUD ─────────────────────────────────────────────────────────────────

def get_user(email: str) -> Optional[dict]:
    conn = _get_conn()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE email = %s", (email.lower(),))
    user = cur.fetchone()
    conn.close()
    if not user:
        return None
    user["settings"] = get_user_settings(email.lower())
    return user


def create_user(name: str, email: str, password: str) -> dict:
    email = email.lower()
    conn  = _get_conn()
    cur   = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
            (name, email, hash_password(password)),
        )
        # Create default settings row
        cur.execute(
            "INSERT INTO user_settings (user_email) VALUES (%s)",
            (email,),
        )
        conn.commit()
    except mysql.connector.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        conn.close()

    return get_user(email)


def authenticate_user(email: str, password: str) -> dict:
    conn = _get_conn()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE email = %s", (email.lower(),))
    user = cur.fetchone()
    conn.close()
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user["settings"] = get_user_settings(email.lower())
    return user


# ── Settings ──────────────────────────────────────────────────────────────────

def get_user_settings(email: str) -> dict:
    conn = _get_conn()
    cur  = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM user_settings WHERE user_email = %s", (email.lower(),))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {
            "gemini_api_key": "", "deepseek_api_key": "",
            "db_configs": [], "active_db_index": 0,
            "default_llm": "gemini", "theme": "dark",
        }
    # db_configs is stored as JSON in MySQL — parse if it's a string
    raw = row.get("db_configs")
    if isinstance(raw, str):
        try:
            row["db_configs"] = json.loads(raw)
        except Exception:
            row["db_configs"] = []
    elif raw is None:
        row["db_configs"] = []
    # Remove internal columns the rest of the app doesn't need
    row.pop("user_email", None)
    row.pop("updated_at", None)
    return row


def update_user_settings(email: str, settings_patch: dict) -> dict:
    email = email.lower()
    conn  = _get_conn()
    cur   = conn.cursor()

    # Ensure settings row exists
    cur.execute(
        "INSERT IGNORE INTO user_settings (user_email) VALUES (%s)", (email,)
    )

    # Build SET clause dynamically from patch keys
    allowed = {"gemini_api_key", "deepseek_api_key", "db_configs",
               "active_db_index", "default_llm", "theme"}
    patch   = {k: v for k, v in settings_patch.items() if k in allowed}
    if not patch:
        conn.close()
        return get_user_settings(email)

    set_clause = ", ".join(f"{k} = %s" for k in patch)
    values     = []
    for k, v in patch.items():
        values.append(json.dumps(v) if k == "db_configs" else v)
    values.append(email)

    cur.execute(f"UPDATE user_settings SET {set_clause} WHERE user_email = %s", values)
    conn.commit()
    conn.close()
    return get_user_settings(email)


# ── FastAPI dependency ────────────────────────────────────────────────────────

def current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    email = decode_token(creds.credentials)
    if not email:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
