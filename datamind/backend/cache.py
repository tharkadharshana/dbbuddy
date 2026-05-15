"""
Schema Cache — the core of the dynamic query system.

Flow:
1. User connects a DB → `build_cache(user_email, db_config, schemas, fkeys, samples, llm)`
   - LLM generates custom SQL for every template based on the actual schema
   - Saves everything to  data/cache/{email_hash}_{db_hash}.json
   - This runs ONCE. Never again unless the DB changes.

2. Every API call → `get_cache(user_email, db_config)` → returns cached SQL instantly
   - Zero LLM tokens used after the first build

3. User changes DB → old cache is ignored, new cache is built for the new DB

Cache file structure:
{
  "db_fingerprint": "...",        # hash of host+port+database
  "built_at": "2024-01-01T...",
  "schema_text": "...",           # full schema used
  "catalogue": [...],             # LLM-generated analytics catalogue
  "template_sql": {               # LLM-generated SQL per template ID
    "revenue_trend": "SELECT ...",
    "top_products":  "SELECT ...",
    ...
  },
  "auto_columns": {               # best date/value cols for forecast & anomaly
    "forecast_date":  "created_at",
    "forecast_value": "total_amount",
    "anomaly_date":   "created_at",
    "anomaly_value":  "total_amount"
  }
}
"""

import json
import hashlib
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

from logger import get_logger

log = get_logger(__name__)

DATA_DIR = Path(__file__).parent / "data" / "cache"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Fingerprinting ─────────────────────────────────────────────────────────────

def _db_fingerprint(db_config: dict) -> str:
    """Stable hash of the DB identity (host+port+database)."""
    key = f"{db_config.get('host','env')}:{db_config.get('port',3306)}:{db_config.get('database','env')}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _user_hash(email: str) -> str:
    return hashlib.md5(email.encode()).hexdigest()[:10]


def _cache_path(email: str, db_config: dict) -> Path:
    return DATA_DIR / f"{_user_hash(email)}_{_db_fingerprint(db_config)}.json"


# ── Read / Write ───────────────────────────────────────────────────────────────

def get_cache(email: str, db_config: dict) -> Optional[Dict]:
    """Return the cache for this user+DB, or None if not built yet."""
    p = _cache_path(email, db_config)
    if not p.exists():
        log.debug("Cache miss", user=email, path=p.name,
                  db=db_config.get("database", "env"))
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        log.debug("Cache hit", user=email, path=p.name,
                  templates=len(data.get("template_sql", {})))
        return data
    except Exception as e:
        log.warning("Cache read failed", user=email, path=p.name, error=str(e))
        return None


def save_cache(email: str, db_config: dict, data: Dict):
    """Persist the cache to disk."""
    p = _cache_path(email, db_config)
    data["db_fingerprint"] = _db_fingerprint(db_config)
    data["built_at"] = datetime.utcnow().isoformat()
    with open(p, "w") as f:
        json.dump(data, f, indent=2)
    log.info("Cache saved", user=email, path=p.name,
             templates=len(data.get("template_sql", {})),
             catalogue=len(data.get("catalogue", [])))


def invalidate_cache(email: str, db_config: dict):
    """Delete the cache for this user+DB (called when user changes DB config)."""
    p = _cache_path(email, db_config)
    if p.exists():
        p.unlink()
        log.info("Cache invalidated", user=email, path=p.name)


def list_caches(email: str) -> List[str]:
    """List all cache files for a user."""
    prefix = _user_hash(email)
    return [str(f) for f in DATA_DIR.glob(f"{prefix}_*.json")]


def get_cache_status(email: str, db_config: dict) -> Dict:
    """Return cache metadata without loading the full cache."""
    cache = get_cache(email, db_config)
    if not cache:
        return {"cached": False}
    return {
        "cached": True,
        "built_at": cache.get("built_at"),
        "template_count": len(cache.get("template_sql", {})),
        "catalogue_count": len(cache.get("catalogue", [])),
    }
