import os
import json
from datetime import datetime, timedelta
from typing import Optional

import mysql.connector
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pool import get_internal_conn as _get_conn

# ── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "datamind-secret-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

_DEFAULT_SETTINGS = {
    "gemini_api_key": "",
    "deepseek_api_key": "",
    "db_configs": [],
    "active_db_index": 0,
    "default_llm": "gemini",
    "theme": "dark",
}


def init_users_table():
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email         VARCHAR(255) PRIMARY KEY,
            name          VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            settings      JSON         NOT NULL,
            created_at    DATETIME     NOT NULL
        )
    """)
    # Add settings column if the table pre-existed without it
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'users'
          AND COLUMN_NAME  = 'settings'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE users ADD COLUMN settings JSON NOT NULL DEFAULT (JSON_OBJECT())")
    # Add name column if missing (older schema may not have it)
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'users'
          AND COLUMN_NAME  = 'name'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE users ADD COLUMN name VARCHAR(255) NOT NULL DEFAULT ''")
    # Add password_hash column if missing
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = 'users'
          AND COLUMN_NAME  = 'password_hash'
    """)
    if cursor.fetchone()[0] == 0:
        cursor.execute("ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NOT NULL DEFAULT ''")
    conn.commit()
    cursor.close()
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

def _row_to_user(row) -> dict:
    email, name, password_hash, settings_json, created_at = row
    settings = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
    return {
        "email": email,
        "name": name,
        "password_hash": password_hash,
        "settings": {**_DEFAULT_SETTINGS, **settings},
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
    }


def get_user(email: str) -> Optional[dict]:
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT email, name, password_hash, settings, created_at FROM users WHERE email = %s", (email.lower(),))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return _row_to_user(row) if row else None


def create_user(name: str, email: str, password: str) -> dict:
    email = email.lower()
    if get_user(email):
        raise HTTPException(status_code=400, detail="Email already registered")
    settings = dict(_DEFAULT_SETTINGS)
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (email, name, password_hash, settings, created_at) VALUES (%s, %s, %s, %s, %s)",
        (email, name, hash_password(password), json.dumps(settings), datetime.utcnow()),
    )
    conn.commit()
    cursor.close()
    conn.close()
    return {"email": email, "name": name, "settings": settings, "created_at": datetime.utcnow().isoformat()}


def authenticate_user(email: str, password: str) -> dict:
    user = get_user(email.lower())
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return user


def update_user_settings(email: str, settings_patch: dict) -> dict:
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    current = user.get("settings", {})
    current.update(settings_patch)
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET settings = %s WHERE email = %s", (json.dumps(current), email))
    conn.commit()
    cursor.close()
    conn.close()
    return current


def delete_user(email: str):
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE email = %s", (email.lower(),))
    conn.commit()
    cursor.close()
    conn.close()


def get_user_settings(email: str) -> dict:
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.get("settings", {})


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
