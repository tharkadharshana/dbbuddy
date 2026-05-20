"""
providers/base.py
=================
Abstract base class that every external API provider must implement.

To add a new provider (e.g. Shopify):
  1. mkdir providers/shopify/
  2. Create manifest.json  (metadata + credential fields)
  3. Create schema.sql     (tables to create for this provider)
  4. Create sync.py        (API → DB sync logic)
  5. Create provider.py    (implements BaseProvider)
  6. Register in providers/__init__.py

That is ALL. Zero changes anywhere else.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool
    error: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    # e.g. {"business_name": "Joe's Cafe", "plan": "Pro", "stores": 3}


@dataclass
class SyncResult:
    ok: bool
    rows_fetched: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    limit_hit: bool = False
    error: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class RowBudget:
    """Tracks how many rows may still be inserted during a sync.
    Pass to every entity-sync function; call request() before each INSERT.
    If the budget is None the insert is always allowed (no plan limit)."""

    def __init__(self, limit: Optional[int]):
        self._remaining = limit  # None = unlimited
        self.skipped = 0

    @property
    def exhausted(self) -> bool:
        return self._remaining is not None and self._remaining <= 0

    def request(self) -> bool:
        """Return True if this row is within budget, False if it must be skipped."""
        if self._remaining is None:
            return True
        if self._remaining > 0:
            self._remaining -= 1
            return True
        self.skipped += 1
        return False


@dataclass
class ProviderManifest:
    """
    Static metadata about a provider.
    Loaded from manifest.json — defines what the UI renders.
    """
    provider_id: str         # machine id: "loyverse", "shopify"
    display_name: str        # human name: "Loyverse POS"
    description: str         # one-line description
    logo_emoji: str          # emoji used in UI (no external images needed)
    category: str            # "POS" | "Ecommerce" | "Accounting" | "CRM"
    website: str             # link to provider's website
    docs_url: str            # link to their API docs
    sync_interval_minutes: int   # how often to auto-sync (0 = manual only)
    supports_delta_sync: bool    # can sync only new records?
    credential_fields: List[Dict[str, Any]]  # what the user needs to fill in
    # Each field: {"key": "api_token", "label": "API Token",
    #              "type": "password"|"text"|"select",
    #              "hint": "Found in Settings → API", "required": True,
    #              "options": [...]}  # only for type=select


# ── Abstract base ─────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    """
    Every external API provider inherits from this.
    Implement all four abstract methods.
    """

    @property
    @abstractmethod
    def manifest(self) -> ProviderManifest:
        """Return the provider manifest (metadata + credential field definitions)."""

    @abstractmethod
    def validate_credentials(self, creds: Dict[str, str]) -> ValidationResult:
        """
        Test whether the given credentials actually work.
        Make a lightweight API call (e.g. fetch current user / account info).
        Do NOT save anything. Return ValidationResult.
        """

    @abstractmethod
    def get_schema_sql(self, table_prefix: str) -> str:
        """
        Return the SQL string to CREATE all tables for this provider.
        Use `table_prefix` as the table name prefix.
        Example: f"CREATE TABLE {table_prefix}_receipts (...)"
        Tables must use IF NOT EXISTS and be idempotent.
        """

    @abstractmethod
    def sync(
        self,
        creds: Dict[str, str],
        conn,                              # MySQL connection to DataMind's own DB
        table_prefix: str,                 # e.g. "dm_u42_loyverse"
        since: Optional[datetime],         # None = full sync, datetime = delta sync
        progress_callback=None,            # optional callable(str) for live progress
        row_budget: Optional[int] = None,  # max new rows allowed; None = unlimited
        user_email: str = "",              # owner of this integration
    ) -> SyncResult:
        """
        Fetch data from the external API and upsert into our tables.
        - If since is None: full historical sync (first connect)
        - If since is a datetime: delta sync (only new/changed records)
        Must be idempotent — safe to run multiple times.
        """

    # ── Helpers (implemented, not abstract) ───────────────────────────────────

    @property
    def provider_id(self) -> str:
        return self.manifest.provider_id

    def get_table_prefix(self, user_email: str, suffix: str = "") -> str:
        """
        Generate a safe, unique table prefix for this user+provider.
        e.g. "dm_a1b2c3d4e5f6a7b8_loyverse"

        Uses 16 hex chars (64 bits of SHA-256) instead of 8 (32 bits of MD5).
        Birthday collision: ~1% at ~600M users vs ~9K users with old 8-char MD5.
        Existing prefixes stored in the DB are unaffected — this only changes
        newly-created integrations.
        """
        import hashlib
        user_hash = hashlib.sha256(user_email.lower().encode()).hexdigest()[:16]
        base = f"dm_{user_hash}_{self.provider_id}"
        return f"{base}_{suffix}" if suffix else base
