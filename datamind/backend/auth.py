"""
Auth & user settings module.
Uses TinyDB (a tiny JSON file-based DB) so no extra DB is needed.
All user data is stored in data/users.json next to this file.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

from passlib.context import CryptContext
from jose import JWTError, jwt
from tinydb import TinyDB, Query
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# ── Config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.getenv("SECRET_KEY", "datamind-secret-change-in-production-2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

# ── Database ──────────────────────────────────────────────────────────────────

def _db():
    return TinyDB(DATA_DIR / "users.json")


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
    db = _db()
    User = Query()
    result = db.search(User.email == email.lower())
    return result[0] if result else None


def create_user(name: str, email: str, password: str) -> dict:
    email = email.lower()
    if get_user(email):
        raise HTTPException(status_code=400, detail="Email already registered")
    db = _db()
    user = {
        "name": name,
        "email": email,
        "password_hash": hash_password(password),
        "created_at": datetime.utcnow().isoformat(),
        # Settings stored per-user
        "settings": {
            "gemini_api_key": "",
            "deepseek_api_key": "",
            "db_configs": [],          # list of DB connection profiles
            "active_db_index": 0,
            "default_llm": "gemini",
            "theme": "dark",
        }
    }
    db.insert(user)
    return user


def authenticate_user(email: str, password: str) -> dict:
    user = get_user(email.lower())
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return user


def update_user_settings(email: str, settings_patch: dict) -> dict:
    db = _db()
    User = Query()
    user = get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    current = user.get("settings", {})
    current.update(settings_patch)
    db.update({"settings": current}, User.email == email)
    return current


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
