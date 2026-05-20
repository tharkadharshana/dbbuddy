"""
providers/loyverse/sync.py
==========================
Fetches data from the Loyverse REST API and upserts into the unified
integration_records table via providers/upsert.py.
Supports both full sync (since=None) and delta sync (since=datetime).
"""

import requests
import time
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from logger import get_logger
from providers.upsert import upsert_record

log = get_logger(__name__)

BASE_URL         = "https://api.loyverse.com/v1.0"
PAGE_SIZE        = 250
RATE_LIMIT_SLEEP = 0.2


class LoyverseAPIClient:
    """Thin wrapper around the Loyverse REST API."""

    def __init__(self, api_token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type":  "application/json",
        })

    def get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.debug("Loyverse API GET", url=url, params=params)
        resp = self.session.get(url, params=params or {}, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 5))
            log.warning("Loyverse rate limited, sleeping", seconds=retry_after)
            time.sleep(retry_after)
            resp = self.session.get(url, params=params or {}, timeout=30)
        if not resp.ok:
            raise Exception(f"Loyverse API {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def paginate(self, endpoint: str, key: str,
                 params: dict = None, since: Optional[datetime] = None) -> List[Dict]:
        results = []
        cursor  = None
        p = dict(params or {})
        p["limit"] = PAGE_SIZE
        if since:
            p["updated_at_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            if cursor:
                p["cursor"] = cursor
            data  = self.get(endpoint, p)
            items = data.get(key, [])
            results.extend(items)
            time.sleep(RATE_LIMIT_SLEEP)
            cursor = data.get("cursor")
            if not cursor or not items:
                break

        log.debug("Loyverse paginate complete", endpoint=endpoint, total=len(results))
        return results

    def validate(self) -> dict:
        return self.get("/merchants/me")


# ── Safe value helpers ─────────────────────────────────────────────────────────

def _dt(val) -> Optional[str]:
    if not val:
        return None
    try:
        val = val.replace("Z", "+00:00")
        return datetime.fromisoformat(val).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _dec(val, default=0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _str(val, maxlen=None) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    return s[:maxlen] if maxlen else s


# ── Sync functions ─────────────────────────────────────────────────────────────
# Each function: (client, conn, prefix, user_email, since, budget) → int

def sync_stores(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                since=None, budget=None) -> int:
    rows  = client.paginate("/stores", "stores", since=since)
    count = 0
    for r in rows:
        record = {
            "id":           r.get("id"),
            "name":         _str(r.get("name"), 255),
            "address":      _str(r.get("address")),
            "phone_number": _str(r.get("phone_number"), 50),
            "description":  _str(r.get("description")),
            "created_at":   _dt(r.get("created_at")),
            "updated_at":   _dt(r.get("updated_at")),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "shop", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
    return count


def sync_employees(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                   since=None, budget=None) -> int:
    rows  = client.paginate("/employees", "employees", since=since)
    count = 0
    for r in rows:
        record = {
            "id":           r.get("id"),
            "name":         _str(r.get("name"), 255),
            "email":        _str(r.get("email"), 255),
            "phone_number": _str(r.get("phone_number"), 50),
            "role":         _str(r.get("role"), 50),
            "store_id":     r.get("store_id"),
            "created_at":   _dt(r.get("created_at")),
            "updated_at":   _dt(r.get("updated_at")),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "employee", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
    return count


def sync_categories(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                    since=None, budget=None) -> int:
    rows  = client.paginate("/categories", "categories", since=since)
    count = 0
    for r in rows:
        record = {
            "id":    r.get("id"),
            "name":  _str(r.get("name"), 255),
            "color": _str(r.get("color"), 20),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "category", record,
                         id_field="id", budget=budget):
            count += 1
    return count


def sync_products(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                  since=None, budget=None) -> int:
    rows  = client.paginate("/items", "items", since=since)
    count = 0
    for r in rows:
        record = {
            "id":           r.get("id"),
            "handle":       _str(r.get("handle"), 255),
            "item_name":    _str(r.get("item_name"), 255),
            "description":  _str(r.get("description")),
            "category_id":  r.get("category_id"),
            "track_stock":  int(bool(r.get("track_stock"))),
            "sold_by_weight": int(bool(r.get("sold_by_weight"))),
            "is_composite": int(bool(r.get("is_composite"))),
            "created_at":   _dt(r.get("created_at")),
            "updated_at":   _dt(r.get("updated_at")),
            "deleted_at":   _dt(r.get("deleted_at")),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "product", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1

        # Variants stored as separate records
        for v in r.get("variants", []):
            stores_json = json.dumps(v.get("stores", [])) if v.get("stores") else None
            variant_record = {
                "id":                   v.get("id"),
                "item_id":              r.get("id"),
                "sku":                  _str(v.get("sku"), 100),
                "barcode":              _str(v.get("barcode"), 100),
                "cost":                 _dec(v.get("cost")),
                "default_price":        _dec(v.get("default_price")),
                "purchase_cost":        _dec(v.get("purchase_cost")),
                "default_pricing_type": _str(v.get("default_pricing_type"), 20),
                "stores_json":          stores_json,
                "created_at":           _dt(v.get("created_at")),
                "updated_at":           _dt(v.get("updated_at")),
                "deleted_at":           _dt(v.get("deleted_at")),
            }
            upsert_record(conn, prefix, user_email, "loyverse", "variant",
                          variant_record, id_field="id",
                          ext_created_field="created_at",
                          ext_updated_field="updated_at")

    return count


def sync_customers(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                   since=None, budget=None) -> int:
    rows  = client.paginate("/customers", "customers", since=since)
    count = 0
    for r in rows:
        record = {
            "id":           r.get("id"),
            "name":         _str(r.get("name"), 255),
            "email":        _str(r.get("email"), 255),
            "phone_number": _str(r.get("phone_number"), 50),
            "address":      _str(r.get("address")),
            "city":         _str(r.get("city"), 100),
            "region":       _str(r.get("region"), 100),
            "postal_code":  _str(r.get("postal_code"), 20),
            "country_code": _str(r.get("country_code"), 10),
            "customer_code": _str(r.get("customer_code"), 100),
            "note":         _str(r.get("note")),
            "total_visits": int(r.get("total_visits") or 0),
            "total_spent":  _dec(r.get("total_money_spent")),
            "points_balance": _dec(r.get("points_balance")),
            "created_at":   _dt(r.get("created_at")),
            "updated_at":   _dt(r.get("updated_at")),
            "deleted_at":   _dt(r.get("deleted_at")),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "customer", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
    return count


def sync_receipts(client: LoyverseAPIClient, conn, prefix: str, user_email: str,
                  since=None, budget=None) -> int:
    params = {}
    if since:
        params["updated_at_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = client.paginate("/receipts", "receipts", params=params)

    inserted = 0
    for r in rows:
        if budget is not None and budget.exhausted:
            break

        receipt_number = r.get("receipt_number")
        if not receipt_number:
            continue

        receipt_record = {
            "receipt_number":  receipt_number,
            "order_type":      _str(r.get("order_type"), 50),
            "store_id":        r.get("store_id"),
            "cashier_id":      r.get("cashier_id"),
            "customer_id":     r.get("customer_id"),
            "customer_name":   _str(r.get("customer_name"), 255),
            "total_money":     _dec(r.get("total_money")),
            "points_earned":   _dec(r.get("points_earned")),
            "points_deducted": _dec(r.get("points_deducted")),
            "points_balance":  _dec(r.get("points_balance")),
            "note":            _str(r.get("note")),
            "receipt_type":    _str(r.get("receipt_type"), 20),
            "refund_for":      r.get("refund_for"),
            "total_discounts": _dec(r.get("total_discounts")),
            "total_tax":       _dec(r.get("total_tax")),
            "created_at":      _dt(r.get("created_at")),
            "updated_at":      _dt(r.get("updated_at")),
        }
        if upsert_record(conn, prefix, user_email, "loyverse", "receipt",
                         receipt_record, id_field="receipt_number",
                         ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            inserted += 1

        # Line items
        for li in r.get("line_items", []):
            li_record = {
                "id":               li.get("id"),
                "receipt_number":   receipt_number,
                "item_id":          li.get("item_id"),
                "variant_id":       li.get("variant_id"),
                "item_name":        _str(li.get("item_name"), 255),
                "variant_name":     _str(li.get("variant_name"), 255),
                "sku":              _str(li.get("sku"), 100),
                "quantity":         _dec(li.get("quantity")),
                "price":            _dec(li.get("price")),
                "gross_total_money": _dec(li.get("gross_total_money")),
                "discount":         _dec(li.get("discount")),
                "total_money":      _dec(li.get("total_money")),
                "cost":             _dec(li.get("cost")),
                "category_id":      li.get("category_id"),
                "line_note":        _str(li.get("line_note")),
            }
            upsert_record(conn, prefix, user_email, "loyverse", "receipt_line_item",
                          li_record, id_field="id")

        # Payment line items
        for pay in r.get("payments", []):
            pay_record = {
                "id":                pay.get("id"),
                "receipt_number":    receipt_number,
                "payment_type_id":   pay.get("payment_type_id"),
                "payment_type_name": _str(pay.get("name"), 100),
                "money_amount":      _dec(pay.get("money_amount")),
            }
            upsert_record(conn, prefix, user_email, "loyverse", "payment_line_item",
                          pay_record, id_field="id")

    return inserted
