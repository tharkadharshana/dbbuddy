"""
providers/salesplay/sync.py
===========================
Fetches data from the SalesPlay REST API and upserts into DataMind's tables.

API Base: https://api.salesplaypos.com/v1.0
Auth:     Authorization: Bearer <token>
Pagination: cursor-based, max 250 items per page
Rate limit: 300 requests / 300 seconds — we sleep 1s between pages to stay safe.

Endpoints used:
  GET /shops               → {prefix}shops
  GET /category            → {prefix}categories
  GET /payment_types       → {prefix}payment_types
  GET /products            → {prefix}products
  GET /customers           → {prefix}customers
  GET /receipts            → {prefix}receipts + {prefix}receipt_line_items
"""

import requests
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from logger import get_logger

log = get_logger(__name__)

# BASE_URL   = "https://api.salesplaypos.com/v1.0"
BASE_URL = "https://spdeveloperapi.nvision.lk/v1.0"
PAGE_SIZE  = 250        # SalesPlay max per page
RATE_SLEEP = 1.0        # seconds between paginated requests (300 req/300s limit)


# ── API Client ────────────────────────────────────────────────────────────────

class SalesPlayAPIClient:
    """Thin, robust wrapper around the SalesPlay REST API."""

    def __init__(self, api_token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type":  "application/json",
        })
        self._token = api_token

    def get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.info("SalesPlay API Request", url=url, params=params)
        for attempt in range(3):
            try:
                resp = self.session.get(url, params=params or {}, timeout=30)
            except Exception as e:
                log.error("SalesPlay Network Error", error=str(e), attempt=attempt)
                time.sleep(2)
                continue

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 10))
                log.warning("SalesPlay rate limited", seconds=retry_after, attempt=attempt)
                time.sleep(retry_after)
                continue
            if resp.status_code == 401:
                raise Exception("SalesPlay API token is invalid or expired.")
            if not resp.ok:
                raise Exception(f"SalesPlay API {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise Exception("SalesPlay API failed after 3 retries (rate limited).")

    def paginate(self, endpoint: str, key: str,
                 params: dict = None,
                 since: Optional[datetime] = None) -> List[Dict]:
        """Fetch all pages of a paginated endpoint."""
        results = []
        p = dict(params or {})
        p["limit"] = PAGE_SIZE
        if since:
            p["updated_at_min"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor = None

        prev_cursor = None
        while True:
            if cursor:
                p["cursor"] = cursor
            data = self.get(endpoint, p)
            items = data.get(key, [])
            results.extend(items)

            new_cursor = data.get("cursor")
            # Stop if no cursor, no items, or cursor is repeating (infinite loop protection)
            if not new_cursor or not items or new_cursor == prev_cursor:
                break

            prev_cursor = new_cursor
            cursor = new_cursor
            time.sleep(RATE_SLEEP)

        log.debug("SalesPlay paginate done", endpoint=endpoint, total=len(results))
        return results

    def validate(self) -> dict:
        """Test the token — fetch first shop."""
        try:
            shops = self.get("/shops", {"limit": 1})
            shop_list = shops.get("shops", [])
            if shop_list:
                return {
                    "business_name": shop_list[0].get("name", ""),
                    "shop_count":    len(shop_list),
                    "currency":      shop_list[0].get("currency", ""),
                }
            return {"business_name": "SalesPlay Account", "shop_count": 0}
        except Exception as e:
            raise Exception(f"Token validation failed: {e}")


# ── Safe value helpers ────────────────────────────────────────────────────────

def _dt(val) -> Optional[str]:
    """ISO datetime string → MySQL DATETIME string."""
    if not val:
        return None
    try:
        # Handle both "2024-01-15T10:30:00.000Z" and "2024-01-15T10:30:00Z"
        clean = val.replace("Z", "+00:00") if val.endswith("Z") else val
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None

def _dec(val, default=None):
    """Safe decimal — returns None if falsy."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default

def _str(val, maxlen=None) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    if maxlen:
        s = s[:maxlen]
    return s


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_shops(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None) -> int:
    """Sync shops → {prefix}shops."""
    shops = client.paginate("/shops", "shops", since=since)
    count = 0
    for s in shops:
        cursor.execute(f"""
            INSERT INTO `{prefix}shops`
                (id, shop_name, address, phone, email, currency, country, timezone, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                shop_name=VALUES(shop_name), address=VALUES(address),
                phone=VALUES(phone), email=VALUES(email),
                currency=VALUES(currency), country=VALUES(country),
                timezone=VALUES(timezone), status=VALUES(status),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(s.get("id"), 64),
            _str(s.get("name"), 255),
            _str(s.get("address"), 500),
            _str(s.get("phone_number"), 50),
            _str(s.get("email"), 255),
            _str(s.get("currency"), 10),
            _str(s.get("country_code"), 10),
            _str(s.get("timezone"), 100),
            _str(s.get("status"), 20),
            _dt(s.get("created_at")),
            _dt(s.get("updated_at")),
        ))
        count += 1
    log.debug("Synced shops", prefix=prefix, count=count)
    return count


def sync_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                    since: Optional[datetime] = None) -> int:
    """Sync categories → {prefix}categories."""
    items = client.paginate("/category", "categories", since=since)
    count = 0
    for c in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}categories`
                (id, category_name, color, shop_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                category_name=VALUES(category_name), color=VALUES(color),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(c.get("id"), 64),
            _str(c.get("name"), 255),
            _str(c.get("color"), 20),
            _str(c.get("shop_id"), 64),
            _dt(c.get("created_at")),
            _dt(c.get("updated_at")),
        ))
        count += 1
    log.debug("Synced categories", prefix=prefix, count=count)
    return count


def sync_payment_types(client: SalesPlayAPIClient, cursor, prefix: str,
                       since: Optional[datetime] = None) -> int:
    """Sync payment types → {prefix}payment_types."""
    items = client.paginate("/payment_types", "payment_types", since=since)
    count = 0
    for pt in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}payment_types`
                (id, payment_name, payment_type, is_active, shop_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                payment_name=VALUES(payment_name), payment_type=VALUES(payment_type),
                is_active=VALUES(is_active), updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(pt.get("id"), 64),
            _str(pt.get("name"), 255),
            _str(pt.get("type"), 50),
            1 if pt.get("active") else 0,
            _str(pt.get("shop_id"), 64),
            _dt(pt.get("created_at")),
            _dt(pt.get("updated_at")),
        ))
        count += 1
    log.debug("Synced payment_types", prefix=prefix, count=count)
    return count


def sync_products(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """Sync products → {prefix}products. Flattens first variant."""
    items = client.paginate("/products", "products", since=since)
    count = 0
    for p in items:
        variants = p.get("variants") or []
        first    = variants[0] if variants else {}
        cursor.execute(f"""
            INSERT INTO `{prefix}products`
                (id, product_name, description, category_id, reference_id,
                 sold_by_weight, is_active, primary_supplier_id, track_stock,
                 variant_id, sku, barcode, cost, price, purchase_cost,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                product_name=VALUES(product_name), description=VALUES(description),
                category_id=VALUES(category_id), is_active=VALUES(is_active),
                variant_id=VALUES(variant_id), sku=VALUES(sku), barcode=VALUES(barcode),
                cost=VALUES(cost), price=VALUES(price), purchase_cost=VALUES(purchase_cost),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(p.get("id"), 64),
            _str(p.get("name"), 500),
            _str(p.get("description")),
            _str(p.get("category_id"), 64),
            _str(p.get("reference_id"), 100),
            1 if p.get("sold_by_weight") else 0,
            0 if p.get("deleted_at") else 1,
            _str(p.get("primary_supplier_id"), 64),
            1 if p.get("track_stock") else 0,
            # first variant
            _str(first.get("id"), 64),
            _str(first.get("sku"), 100),
            _str(first.get("barcode"), 100),
            _dec(first.get("cost")),
            _dec(first.get("price")),
            _dec(first.get("purchase_cost")),
            _dt(p.get("created_at")),
            _dt(p.get("updated_at")),
        ))
        count += 1
    log.debug("Synced products", prefix=prefix, count=count)
    return count


def sync_customers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None) -> int:
    """Sync customers → {prefix}customers."""
    items = client.paginate("/customers", "customers", since=since)
    count = 0
    for c in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}customers`
                (id, customer_name, email, phone_number, customer_code, note,
                 total_visits, total_spent, points_balance, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                customer_name=VALUES(customer_name), email=VALUES(email),
                phone_number=VALUES(phone_number), note=VALUES(note),
                total_visits=VALUES(total_visits), total_spent=VALUES(total_spent),
                points_balance=VALUES(points_balance),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(c.get("id"), 64),
            _str(c.get("name"), 255),
            _str(c.get("email"), 255),
            _str(c.get("phone_number"), 50),
            _str(c.get("customer_code"), 100),
            _str(c.get("note")),
            int(c.get("total_visits") or 0),
            _dec(c.get("total_money_spent"), 0),
            _dec(c.get("points_balance"), 0),
            _dt(c.get("created_at")),
            _dt(c.get("updated_at")),
        ))
        count += 1
    log.debug("Synced customers", prefix=prefix, count=count)
    return count


def sync_receipts(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """
    Sync receipts + line items.
    Fetches both SALE and REFUND receipt types.
    Returns total rows inserted across both tables.
    """
    total = 0
    # Try /receipts first, fallback to /pos/receipts if we get a 401/404
    endpoint = "/receipts"
    try:
        # Test if the endpoint is accessible
        client.get(endpoint, {"limit": 1})
    except Exception as e:
        if "401" in str(e) or "404" in str(e):
            log.warning("Receipts endpoint failed, trying fallback /pos/receipts", error=str(e))
            endpoint = "/pos/receipts"
        else:
            log.error("Receipts sync aborted", error=str(e))
            return 0

    for receipt_type in ["SALE", "REFUND"]:
        try:
            items = client.paginate(
                endpoint, "receipts",
                params={"type": receipt_type},
                since=since,
            )
        except Exception as e:
            log.warning(f"Failed to fetch {receipt_type} receipts", error=str(e))
            continue
        for r in items:
            # First payment method for simplicity
            payments = r.get("payments") or []
            first_pay = payments[0] if payments else {}

            cursor.execute(f"""
                INSERT INTO `{prefix}receipts`
                    (id, receipt_number, shop_id, customer_id, created_at, updated_at,
                     total_money, total_discount, total_tax,
                     points_earned, points_deducted, note,
                     receipt_type, status, payment_type_id, payment_amount)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    total_money=VALUES(total_money), total_discount=VALUES(total_discount),
                    total_tax=VALUES(total_tax), status=VALUES(status),
                    points_earned=VALUES(points_earned), points_deducted=VALUES(points_deducted),
                    payment_type_id=VALUES(payment_type_id), payment_amount=VALUES(payment_amount),
                    updated_at=VALUES(updated_at), synced_at=NOW()
            """, (
                _str(r.get("id"), 64),
                _str(r.get("receipt_number"), 100),
                _str(r.get("shop_id"), 64),
                _str(r.get("customer_id"), 64),
                _dt(r.get("created_at")),
                _dt(r.get("updated_at")),
                _dec(r.get("total_money"), 0),
                _dec(r.get("total_discount"), 0),
                _dec(r.get("total_tax"), 0),
                _dec(r.get("points_earned"), 0),
                _dec(r.get("points_deducted"), 0),
                _str(r.get("note")),
                receipt_type,
                _str(r.get("status"), 30),
                _str(first_pay.get("payment_type_id"), 64),
                _dec(first_pay.get("money_amount")),
            ))
            total += 1

            # Line items
            for item in (r.get("line_items") or []):
                item_id = _str(item.get("id"), 64) or f"{r.get('id')}_{item.get('variant_id','')}"
                cursor.execute(f"""
                    INSERT INTO `{prefix}receipt_line_items`
                        (id, receipt_id, product_id, variant_id, product_name, sku,
                         quantity, price, gross_total_money, total_discount,
                         total_money, cost, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        quantity=VALUES(quantity), price=VALUES(price),
                        gross_total_money=VALUES(gross_total_money),
                        total_discount=VALUES(total_discount), total_money=VALUES(total_money),
                        cost=VALUES(cost), synced_at=NOW()
                """, (
                    item_id,
                    _str(r.get("id"), 64),
                    _str(item.get("item_id"), 64),
                    _str(item.get("variant_id"), 64),
                    _str(item.get("name"), 500),
                    _str(item.get("sku"), 100),
                    _dec(item.get("quantity"), 0),
                    _dec(item.get("price"), 0),
                    _dec(item.get("gross_total_money"), 0),
                    _dec(item.get("total_discount"), 0),
                    _dec(item.get("total_money"), 0),
                    _dec(item.get("cost")),
                    _dt(r.get("created_at")),
                ))
                total += 1

    log.debug("Synced receipts", prefix=prefix, total=total)
    return total
