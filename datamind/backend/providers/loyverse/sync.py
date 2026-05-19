"""
providers/loyverse/sync.py
==========================
Fetches data from the Loyverse REST API and upserts into DataMind's tables.
Supports both full sync (since=None) and delta sync (since=datetime).
"""

import requests
import time
from datetime import datetime, timezone
from typing import Optional, Callable, List, Dict, Any

from logger import get_logger

log = get_logger(__name__)

BASE_URL = "https://api.loyverse.com/v1.0"
PAGE_SIZE = 250        # Loyverse max items per page
RATE_LIMIT_SLEEP = 0.2 # seconds between requests (avoid 429)


class LoyverseAPIClient:
    """Thin wrapper around the Loyverse REST API."""

    def __init__(self, api_token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
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
        """Fetch all pages from a paginated Loyverse endpoint."""
        results = []
        cursor = None
        p = dict(params or {})
        p["limit"] = PAGE_SIZE
        if since:
            p["updated_at_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        while True:
            if cursor:
                p["cursor"] = cursor
            data = self.get(endpoint, p)
            items = data.get(key, [])
            results.extend(items)
            time.sleep(RATE_LIMIT_SLEEP)
            cursor = data.get("cursor")
            if not cursor or not items:
                break

        log.debug("Loyverse paginate complete", endpoint=endpoint, total=len(results))
        return results

    def validate(self) -> dict:
        """Test credentials — fetch merchant info."""
        return self.get("/merchants/me")


# ── Safe value helpers ─────────────────────────────────────────────────────────

def _dt(val) -> Optional[str]:
    """Convert ISO datetime string to MySQL datetime string, or None."""
    if not val:
        return None
    try:
        val = val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(val)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
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


# ── Individual entity sync functions ──────────────────────────────────────────

def sync_stores(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    rows = client.paginate("/stores", "stores", since=since)
    count = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_stores`
              (id, name, address, phone_number, description, default_tax_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              name=VALUES(name), address=VALUES(address),
              phone_number=VALUES(phone_number), updated_at=VALUES(updated_at)
        """, (
            r.get("id"), _str(r.get("name"), 255), _str(r.get("address")),
            _str(r.get("phone_number"), 50), _str(r.get("description")),
            r.get("default_tax_id"), _dt(r.get("created_at")), _dt(r.get("updated_at")),
        ))
        count += 1
    return count


def sync_employees(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    rows = client.paginate("/employees", "employees", since=since)
    count = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_employees`
              (id, name, email, phone_number, role, store_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              name=VALUES(name), email=VALUES(email), role=VALUES(role),
              store_id=VALUES(store_id), updated_at=VALUES(updated_at)
        """, (
            r.get("id"), _str(r.get("name"), 255), _str(r.get("email"), 255),
            _str(r.get("phone_number"), 50), _str(r.get("role"), 50),
            r.get("store_id"), _dt(r.get("created_at")), _dt(r.get("updated_at")),
        ))
        count += 1
    return count


def sync_categories(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    rows = client.paginate("/categories", "categories", since=since)
    count = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_categories` (id, name, color)
            VALUES (%s,%s,%s)
            ON DUPLICATE KEY UPDATE name=VALUES(name), color=VALUES(color)
        """, (r.get("id"), _str(r.get("name"), 255), _str(r.get("color"), 20)))
        count += 1
    return count


def sync_products(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    rows = client.paginate("/items", "items", since=since)
    count = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_products`
              (id, handle, item_name, description, category_id,
               track_stock, sold_by_weight, is_composite, created_at, updated_at, deleted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              item_name=VALUES(item_name), category_id=VALUES(category_id),
              track_stock=VALUES(track_stock), updated_at=VALUES(updated_at),
              deleted_at=VALUES(deleted_at)
        """, (
            r.get("id"), _str(r.get("handle"), 255), _str(r.get("item_name"), 255),
            _str(r.get("description")), r.get("category_id"),
            int(bool(r.get("track_stock"))), int(bool(r.get("sold_by_weight"))),
            int(bool(r.get("is_composite"))),
            _dt(r.get("created_at")), _dt(r.get("updated_at")), _dt(r.get("deleted_at")),
        ))
        # Sync variants
        for v in r.get("variants", []):
            import json
            stores_json = json.dumps(v.get("stores", [])) if v.get("stores") else None
            cursor.execute(f"""
                INSERT INTO `{prefix}_variants`
                  (id, item_id, sku, barcode, cost, default_price, purchase_cost,
                   default_pricing_type, stores_json, created_at, updated_at, deleted_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  cost=VALUES(cost), default_price=VALUES(default_price),
                  stores_json=VALUES(stores_json), updated_at=VALUES(updated_at)
            """, (
                v.get("id"), r.get("id"), _str(v.get("sku"), 100),
                _str(v.get("barcode"), 100), _dec(v.get("cost")),
                _dec(v.get("default_price")), _dec(v.get("purchase_cost")),
                _str(v.get("default_pricing_type"), 20), stores_json,
                _dt(v.get("created_at")), _dt(v.get("updated_at")), _dt(v.get("deleted_at")),
            ))
        count += 1
    return count


def sync_customers(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    rows = client.paginate("/customers", "customers", since=since)
    count = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_customers`
              (id, name, email, phone_number, address, city, region,
               postal_code, country_code, customer_code, note,
               total_visits, total_spent, points_balance,
               created_at, updated_at, deleted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              name=VALUES(name), email=VALUES(email), total_visits=VALUES(total_visits),
              total_spent=VALUES(total_spent), points_balance=VALUES(points_balance),
              updated_at=VALUES(updated_at), deleted_at=VALUES(deleted_at)
        """, (
            r.get("id"), _str(r.get("name"), 255), _str(r.get("email"), 255),
            _str(r.get("phone_number"), 50), _str(r.get("address")),
            _str(r.get("city"), 100), _str(r.get("region"), 100),
            _str(r.get("postal_code"), 20), _str(r.get("country_code"), 10),
            _str(r.get("customer_code"), 100), _str(r.get("note")),
            int(r.get("total_visits") or 0), _dec(r.get("total_money_spent")),
            _dec(r.get("points_balance")),
            _dt(r.get("created_at")), _dt(r.get("updated_at")), _dt(r.get("deleted_at")),
        ))
        count += 1
    return count


def sync_receipts(client: LoyverseAPIClient, cursor, prefix: str, since=None, budget=None) -> int:
    params = {}
    if since:
        params["updated_at_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = client.paginate("/receipts", "receipts", params=params)
    inserted = 0
    for r in rows:
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}_receipts`
              (receipt_number, receipt_date, order_type, store_id, cashier_id,
               customer_id, customer_name, total_money, points_earned,
               points_deducted, points_balance, note, receipt_type, refund_for,
               total_discounts, total_tax, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              total_money=VALUES(total_money), updated_at=VALUES(updated_at),
              customer_id=VALUES(customer_id), customer_name=VALUES(customer_name)
        """, (
            r.get("receipt_number"), _dt(r.get("receipt_date")),
            _str(r.get("order_type"), 50), r.get("store_id"), r.get("cashier_id"),
            r.get("customer_id"), _str(r.get("customer_name"), 255),
            _dec(r.get("total_money")), _dec(r.get("points_earned")),
            _dec(r.get("points_deducted")), _dec(r.get("points_balance")),
            _str(r.get("note")), _str(r.get("receipt_type"), 20),
            r.get("refund_for"),
            _dec(r.get("total_discounts")), _dec(r.get("total_tax")),
            _dt(r.get("created_at")), _dt(r.get("updated_at")),
        ))
        # Line items
        for li in r.get("line_items", []):
            cursor.execute(f"""
                INSERT IGNORE INTO `{prefix}_receipt_line_items`
                  (id, receipt_number, item_id, variant_id, item_name, variant_name,
                   sku, quantity, price, gross_total_money, discount, total_money,
                   cost, category_id, line_note)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                li.get("id"), r.get("receipt_number"),
                li.get("item_id"), li.get("variant_id"),
                _str(li.get("item_name"), 255), _str(li.get("variant_name"), 255),
                _str(li.get("sku"), 100), _dec(li.get("quantity")),
                _dec(li.get("price")), _dec(li.get("gross_total_money")),
                _dec(li.get("discount")), _dec(li.get("total_money")),
                _dec(li.get("cost")), li.get("category_id"), _str(li.get("line_note")),
            ))
        # Payments
        for pay in r.get("payments", []):
            cursor.execute(f"""
                INSERT IGNORE INTO `{prefix}_payment_line_items`
                  (id, receipt_number, payment_type_id, payment_type_name, money_amount)
                VALUES (%s,%s,%s,%s,%s)
            """, (
                pay.get("id"), r.get("receipt_number"),
                pay.get("payment_type_id"), _str(pay.get("name"), 100),
                _dec(pay.get("money_amount")),
            ))
        inserted += 1
    return inserted
