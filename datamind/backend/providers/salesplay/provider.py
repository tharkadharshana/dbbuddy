"""
providers/salesplay/provider.py
================================
SalesPlay POS provider — implements BaseProvider.
"""

import json
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Optional

from providers.base import BaseProvider, ProviderManifest, ValidationResult, SyncResult
from providers.salesplay.sync import (
    SalesPlayAPIClient,
    sync_shops, sync_categories, sync_payment_types,
    sync_products, sync_customers, sync_receipts,
)
from logger import get_logger

log = get_logger(__name__)

_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
_SCHEMA_PATH   = Path(__file__).parent / "schema.sql"


class SalesPlayProvider(BaseProvider):

    @property
    def manifest(self) -> ProviderManifest:
        with open(_MANIFEST_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return ProviderManifest(**data)

    # ── Validate credentials ──────────────────────────────────────────────────

    def validate_credentials(self, creds: dict) -> ValidationResult:
        api_token = creds.get("api_token", "").strip()
        if not api_token:
            return ValidationResult(ok=False, error="API token is required.")
        try:
            client  = SalesPlayAPIClient(api_token)
            account = client.validate()
            log.info("SalesPlay credentials validated",
                     business=account.get("business_name"))
            return ValidationResult(
                ok=True,
                details={
                    "merchant_name": account.get("business_name", "SalesPlay Account"),
                    "shop_count":    account.get("shop_count", 0),
                    "currency":      account.get("currency", ""),
                }
            )
        except Exception as e:
            log.warning("SalesPlay credential validation failed", error=str(e))
            return ValidationResult(ok=False, error=str(e))

    # ── Schema ────────────────────────────────────────────────────────────────

    def get_schema_sql(self, table_prefix: str) -> str:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        return sql.replace("{prefix}", table_prefix)

    # ── Sync ──────────────────────────────────────────────────────────────────

    def sync(
        self,
        creds: dict,
        conn,
        table_prefix: str,
        since: Optional[datetime] = None,
        progress_callback=None,
    ) -> SyncResult:
        api_token = creds.get("api_token", "").strip()
        client    = SalesPlayAPIClient(api_token)
        cursor    = conn.cursor()

        total_rows = 0

        def progress(msg: str):
            log.info("SalesPlay sync", step=msg, prefix=table_prefix)
            if progress_callback:
                progress_callback(msg)

        sync_mode = "Delta" if since else "Full"
        progress(f"{sync_mode} sync started (since={since})")

        # Ordered so lookups (shops, categories, payment_types)
        # exist before referencing tables (products, customers, receipts)
        steps = [
            ("Shops",         sync_shops),
            ("Categories",    sync_categories),
            ("Payment Types", sync_payment_types),
            ("Products",      sync_products),
            ("Customers",     sync_customers),
            ("Receipts",      sync_receipts),
        ]

        try:
            for label, fn in steps:
                progress(f"  Syncing {label}…")
                count = fn(client, cursor, table_prefix, since=since)
                total_rows += count

                # Commit after each step so we don't lose data if a later step fails
                conn.commit()
                progress(f"  ✓ {label}: {count} rows")
            progress(f"✅ SalesPlay sync complete — {total_rows} rows synced")
            log.info("SalesPlay sync complete",
                     prefix=table_prefix, total=total_rows)

            return SyncResult(
                ok=True,
                rows_fetched=total_rows,
                rows_inserted=total_rows,
                rows_updated=0,
            )

        except Exception as e:
            conn.rollback()
            log.error("SalesPlay sync failed", error=str(e), prefix=table_prefix)
            return SyncResult(ok=False, error=str(e))
