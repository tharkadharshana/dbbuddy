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
        # SalesPlay Developer API uses 'Token' instead of 'Authorization'
        self.session.headers.update({
            "Token": f"Bearer {api_token}",
            "Authorization": f"Bearer {api_token}", # Fallback
            "Content-Type":  "application/json",
        })
        self._token = api_token

    def get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.info("SalesPlay API Request", url=url, params=params)
        for attempt in range(3):
            try:
                # SalesPlay Developer API often expects GET filters in the JSON body
                # We send them both as params and as json to be safe
                resp = self.session.request("GET", url, params=params if endpoint == "/shops" else None, 
                                            json=params if endpoint != "/shops" else None, 
                                            timeout=30)
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
            p["created_at_min"] = since.strftime("%Y-%m-%d %H:%M:%S")
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


def sync_sub_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                        since: Optional[datetime] = None) -> int:
    """Sync sub_categories → {prefix}sub_categories."""
    items = client.paginate("/sub_category", "sub_categories", since=since)
    
    # Category lookup for names
    cursor.execute(f"SELECT id, category_name FROM `{prefix}categories`")
    cat_map = {row[0]: row[1] for row in cursor.fetchall()}
    
    count = 0
    for sc in items:
        cat_id = _str(sc.get("category_id"), 64)
        cursor.execute(f"""
            INSERT INTO `{prefix}sub_categories`
                (id, sub_category_name, category_id, category_name, shop_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sub_category_name=VALUES(sub_category_name), 
                category_id=VALUES(category_id), category_name=VALUES(category_name),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(sc.get("id"), 64),
            _str(sc.get("name"), 255),
            cat_id,
            cat_map.get(cat_id),
            _str(sc.get("shop_id"), 64),
            _dt(sc.get("created_at")),
            _dt(sc.get("updated_at")),
        ))
        count += 1
    log.debug("Synced sub_categories", prefix=prefix, count=count)
    return count


def sync_measurements(client: SalesPlayAPIClient, cursor, prefix: str,
                      since: Optional[datetime] = None) -> int:
    """Sync measurements → {prefix}measurements."""
    items = client.paginate("/measurements", "measurements", since=since)
    count = 0
    for m in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}measurements`
                (id, measurement_name, weight_scale_enable, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                measurement_name=VALUES(measurement_name), 
                weight_scale_enable=VALUES(weight_scale_enable),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(m.get("id"), 64),
            _str(m.get("name"), 255),
            1 if m.get("weight_scale_enable") else 0,
            _dt(m.get("created_at")),
            _dt(m.get("updated_at")),
        ))
        count += 1
    return count


def sync_suppliers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None) -> int:
    """Sync suppliers → {prefix}suppliers."""
    items = client.paginate("/suppliers", "suppliers", since=since)
    count = 0
    for s in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}suppliers`
                (id, supplier_name, phone_number, email, address, description, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                supplier_name=VALUES(supplier_name), phone_number=VALUES(phone_number),
                email=VALUES(email), address=VALUES(address), description=VALUES(description),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(s.get("id"), 64),
            _str(s.get("name"), 255),
            _str(s.get("phone_number"), 50),
            _str(s.get("email"), 255),
            _str(s.get("address")),
            _str(s.get("description")),
            _dt(s.get("created_at")),
            _dt(s.get("updated_at")),
        ))
        count += 1
    return count


def sync_taxes(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None) -> int:
    """Sync taxes → {prefix}taxes."""
    items = client.paginate("/taxes", "taxes", since=since)
    count = 0
    for t in items:
        cursor.execute(f"""
            INSERT INTO `{prefix}taxes`
                (id, tax_name, tax_type, calculation_method, rate, apply_after_taxes, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                tax_name=VALUES(tax_name), tax_type=VALUES(tax_type),
                calculation_method=VALUES(calculation_method), rate=VALUES(rate),
                apply_after_taxes=VALUES(apply_after_taxes),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(t.get("id"), 64),
            _str(t.get("name"), 255),
            _str(t.get("tax_type"), 50),
            _str(t.get("calculation_method"), 50),
            _dec(t.get("rate"), 0),
            1 if t.get("apply_tax_after_other_taxes") else 0,
            _dt(t.get("created_at")),
            _dt(t.get("updated_at")),
        ))
        count += 1
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
    
    # Lookups for names
    cursor.execute(f"SELECT id, category_name FROM `{prefix}categories`")
    cat_map = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(f"SELECT id, sub_category_name FROM `{prefix}sub_categories`")
    subcat_map = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(f"SELECT id, measurement_name FROM `{prefix}measurements`")
    meas_map = {row[0]: row[1] for row in cursor.fetchall()}

    count = 0
    for p in items:
        cat_id    = _str(p.get("category_id"), 64)
        subcat_id = _str(p.get("sub_category_id"), 64)
        meas_id   = _str(p.get("measurement_id"), 64)

        variants = p.get("variants") or []
        first    = variants[0] if variants else {}
        cursor.execute(f"""
            INSERT INTO `{prefix}products`
                (id, product_name, description, 
                 category_id, category_name, sub_category_id, sub_category_name,
                 measurement_id, measurement_name, reference_id,
                 sold_by_weight, is_active, primary_supplier_id, track_stock,
                 variant_id, sku, barcode, cost, price, purchase_cost,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                product_name=VALUES(product_name), description=VALUES(description),
                category_id=VALUES(category_id), category_name=VALUES(category_name),
                sub_category_id=VALUES(sub_category_id), sub_category_name=VALUES(sub_category_name),
                measurement_id=VALUES(measurement_id), measurement_name=VALUES(measurement_name),
                is_active=VALUES(is_active),
                variant_id=VALUES(variant_id), sku=VALUES(sku), barcode=VALUES(barcode),
                cost=VALUES(cost), price=VALUES(price), purchase_cost=VALUES(purchase_cost),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            _str(p.get("id"), 64),
            _str(p.get("name"), 500),
            _str(p.get("description")),
            cat_id,
            cat_map.get(cat_id),
            subcat_id,
            subcat_map.get(subcat_id),
            meas_id,
            meas_map.get(meas_id),
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
    # Lookups for names
    cursor.execute(f"SELECT id, shop_name FROM `{prefix}shops`")
    shop_map = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(f"SELECT id, customer_name FROM `{prefix}customers`")
    cust_map = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(f"SELECT id, payment_name FROM `{prefix}payment_types`")
    pay_map = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(f"SELECT id, product_name FROM `{prefix}products`")
    prod_map = {row[0]: row[1] for row in cursor.fetchall()}

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
            # ID handling: SalesPlay receipts often use receipt_number as the key
            r_id = _str(r.get("id") or r.get("receipt_number"), 64)
            shop_id = _str(r.get("shop_id"), 64)
            cust_id = _str(r.get("customer_id"), 64)
            
            # First payment method for simplicity
            payments = r.get("payments") or []
            first_pay = payments[0] if payments else {}
            pay_type_id = _str(first_pay.get("payment_type_id"), 64)

            cursor.execute(f"""
                INSERT INTO `{prefix}receipts`
                    (id, receipt_number, shop_id, shop_name, customer_id, customer_name, 
                     created_at, updated_at,
                     total_money, total_discount, total_tax,
                     points_earned, points_deducted, note,
                     receipt_type, status, payment_type_id, payment_type_name, payment_amount)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    total_money=VALUES(total_money), total_discount=VALUES(total_discount),
                    total_tax=VALUES(total_tax), status=VALUES(status),
                    points_earned=VALUES(points_earned), points_deducted=VALUES(points_deducted),
                    shop_name=VALUES(shop_name), customer_name=VALUES(customer_name),
                    payment_type_id=VALUES(payment_type_id), payment_type_name=VALUES(payment_type_name),
                    payment_amount=VALUES(payment_amount),
                    updated_at=VALUES(updated_at), synced_at=NOW()
            """, (
                r_id,
                _str(r.get("receipt_number"), 100),
                shop_id,
                shop_map.get(shop_id),
                cust_id,
                cust_map.get(cust_id),
                _dt(r.get("receipt_date_time") or r.get("created_at")),
                _dt(r.get("receipt_date_time") or r.get("updated_at")),
                _dec(r.get("total_money"), 0),
                _dec(r.get("total_discount"), 0),
                _dec(r.get("total_tax"), 0),
                _dec(r.get("points_earned"), 0),
                _dec(r.get("points_deducted"), 0),
                _str(r.get("note")),
                receipt_type,
                _str(r.get("status"), 30),
                pay_type_id,
                pay_map.get(pay_type_id),
                _dec(first_pay.get("money_amount")),
            ))
            total += 1

            # Line items (API uses 'line_products')
            line_items = r.get("line_products") or r.get("line_items") or []
            for item in line_items:
                prod_id = _str(item.get("product_id") or item.get("item_id"), 64)
                item_id = _str(item.get("id"), 64) or f"{r_id}_{item.get('product_line_no') or item.get('variant_id','')}"
                
                # Use provided name or lookup from product map
                prod_name = _str(item.get("product_name") or item.get("name"), 500) or prod_map.get(prod_id)

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
                        product_name=VALUES(product_name),
                        cost=VALUES(cost), synced_at=NOW()
                """, (
                    item_id,
                    r_id,
                    prod_id,
                    _str(item.get("variant_id"), 64),
                    prod_name,
                    _str(item.get("product_code") or item.get("sku"), 100),
                    _dec(item.get("quantity"), 0),
                    _dec(item.get("price"), 0),
                    _dec(item.get("gross_total_money"), 0),
                    _dec(item.get("total_discount"), 0),
                    _dec(item.get("total_money"), 0),
                    _dec(item.get("cost")),
                    _dt(r.get("receipt_date_time") or r.get("created_at")),
                ))
                total += 1

    log.debug("Synced receipts", prefix=prefix, total=total)
    return total
