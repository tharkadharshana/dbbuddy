"""
providers/salesplay/provider.py
================================
SalesPlay POS provider — implements BaseProvider.
"""

import json
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

    def validate_credentials(self, creds: dict) -> ValidationResult:
        api_token = creds.get("api_token", "").strip()
        if not api_token:
            return ValidationResult(ok=False, error="API token is required.")
        try:
            client  = SalesPlayAPIClient(api_token)
            account = client.validate()   # uses /merchant endpoint
            log.info("SalesPlay credentials validated",
                     business=account.get("business_name"))
            return ValidationResult(
                ok=True,
                details={
                    "merchant_name": account.get("business_name", "SalesPlay Account"),
                    "shop_count":    account.get("shop_count", 0),
                    "currency":      account.get("currency", ""),
                    "merchant_id":   account.get("merchant_id", ""),
                }
            )
        except Exception as exc:
            log.warning("SalesPlay validation failed", error=str(exc))
            return ValidationResult(ok=False, error=str(exc))

    def get_schema_sql(self, table_prefix: str) -> str:
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        return sql.replace("{prefix}", table_prefix)

    def sync(
        self,
        creds: dict,
        conn,
        table_prefix: str,
        since: Optional[datetime] = None,
        progress_callback=None,
        row_budget: Optional[int] = None,
    ) -> SyncResult:
        from providers.base import RowBudget
        budget = RowBudget(row_budget)

        api_token = creds.get("api_token", "").strip()
        client    = SalesPlayAPIClient(api_token)
        cursor    = conn.cursor()
        total     = 0

        def progress(msg: str):
            log.info("SalesPlay sync", step=msg, prefix=table_prefix)
            if progress_callback:
                progress_callback(msg)

        mode = "Delta" if since else "Full"
        progress(f"{mode} sync started (since={since})")

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
                if budget.exhausted:
                    progress(f"  ⚠ Skipping {label} — row limit reached")
                    break
                progress(f"  Syncing {label}…")
                count = fn(client, cursor, table_prefix, since=since, budget=budget)
                total += count
                conn.commit()
                progress(f"  ✓ {label}: {count} rows")

            if budget.skipped > 0:
                progress(f"⚠ Row limit reached — {budget.skipped:,} rows skipped")
            else:
                progress(f"✅ Sync complete — {total} total rows")

            return SyncResult(
                ok=True,
                rows_fetched=total + budget.skipped,
                rows_inserted=total,
                rows_skipped=budget.skipped,
                limit_hit=budget.skipped > 0,
            )

        except Exception as exc:
            conn.rollback()
            log.error("SalesPlay sync failed", error=str(exc), prefix=table_prefix)
            return SyncResult(ok=False, error=str(exc))
