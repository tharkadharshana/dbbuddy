"""
providers/loyverse/provider.py
==============================
Loyverse POS provider — implements BaseProvider.
"""

import json
import dataclasses
from datetime import datetime
from pathlib import Path
from typing import Optional

from providers.base import BaseProvider, ProviderManifest, ValidationResult, SyncResult
from providers.loyverse.sync import (
    LoyverseAPIClient,
    sync_stores, sync_employees, sync_categories,
    sync_products, sync_customers, sync_receipts,
)
from logger import get_logger

log = get_logger(__name__)

_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
_SCHEMA_PATH   = Path(__file__).parent / "schema.sql"


class LoyverseProvider(BaseProvider):

    @property
    def manifest(self) -> ProviderManifest:
        with open(_MANIFEST_PATH) as f:
            data = json.load(f)
        return ProviderManifest(**data)

    # ── Validate ──────────────────────────────────────────────────────────────

    def validate_credentials(self, creds: dict) -> ValidationResult:
        api_token = creds.get("api_token", "").strip()
        if not api_token:
            return ValidationResult(ok=False, error="API token is required.")
        try:
            client = LoyverseAPIClient(api_token)
            merchant = client.validate()
            log.info("Loyverse credentials validated",
                     merchant_id=merchant.get("id"))
            return ValidationResult(
                ok=True,
                details={
                    "merchant_name": merchant.get("name"),
                    "country":       merchant.get("country_code"),
                    "currency":      merchant.get("currency_code"),
                    "plan":          merchant.get("plan_name"),
                }
            )
        except Exception as e:
            log.warning("Loyverse credential validation failed", error=str(e))
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
        row_budget: Optional[int] = None,
        user_email: str = "",
    ) -> SyncResult:
        from providers.base import RowBudget
        budget = RowBudget(row_budget)

        api_token = creds.get("api_token", "").strip()
        client    = LoyverseAPIClient(api_token)
        total     = 0

        def log_progress(msg: str):
            log.info("Loyverse sync", step=msg, prefix=table_prefix)
            if progress_callback:
                progress_callback(msg)

        sync_mode = "Delta" if since else "Full"
        log_progress(f"{sync_mode} sync started (since={since})")

        steps = [
            ("Stores",     sync_stores),
            ("Employees",  sync_employees),
            ("Categories", sync_categories),
            ("Products",   sync_products),
            ("Customers",  sync_customers),
            ("Receipts",   sync_receipts),
        ]

        try:
            for label, fn in steps:
                if budget.exhausted:
                    log_progress(f"  ⚠ Skipping {label} — row limit reached")
                    break
                log_progress(f"  Syncing {label}…")
                count = fn(client, conn, table_prefix, user_email,
                           since=since, budget=budget)
                total += count
                conn.commit()
                log_progress(f"  ✓ {label}: {count} rows")

            if budget.skipped > 0:
                log_progress(f"⚠ Row limit reached — {budget.skipped:,} rows skipped")
            else:
                log_progress(f"✅ Sync complete — {total} rows synced")

            log.info("Loyverse sync complete",
                     prefix=table_prefix, total=total, skipped=budget.skipped)

            return SyncResult(
                ok=True,
                rows_fetched=total + budget.skipped,
                rows_inserted=total,
                rows_skipped=budget.skipped,
                limit_hit=budget.skipped > 0,
            )

        except Exception as e:
            conn.rollback()
            log.error("Loyverse sync failed", error=str(e), prefix=table_prefix)
            return SyncResult(ok=False, error=str(e))
