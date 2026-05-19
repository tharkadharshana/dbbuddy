"""
providers/salesplay/sync.py
===========================
Fetches data from SalesPlay REST API and upserts into DataMind tables.

VERIFIED FROM OFFICIAL API DOCS (spdeveloper.nvision.lk) + POSTMAN COLLECTION:

  Base URL : https://api.salesplaypos.com/v1.0
  Auth     : Header key = "Token", value = "Bearer <token>"  (NOT Authorization)
  Filters  : JSON body on GET requests
  Date fmt : "YYYY-MM-DD HH:MM:SS" (24-hour)

ENDPOINT FILTER KEYS (exact from API docs + Postman):
  GET /shops          → shop_ids, updated_at_min, updated_at_max, limit, cursor
  GET /category       → category_ids, created_at_min, created_at_max, limit, cursor
  GET /sub_category   → sub_category_ids, created_at_min, created_at_max, limit, cursor
  GET /measurements   → measurement_ids, created_at_min, created_at_max, limit, cursor
  GET /suppliers      → supplier_ids, updated_at_min, updated_at_max, limit, cursor
  GET /taxes          → tax_ids, created_at_min, created_at_max, limit, cursor
  GET /payment_types  → payment_type_ids, created_at_min, created_at_max,
                        updated_at_min, updated_at_max, limit, cursor
  GET /customers      → customer_ids, email, created_at_min, created_at_max, limit, cursor
  GET /products       → product_ids, created_at_min, created_at_max, limit, cursor
  GET /receipts       → receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor

ACTUAL RESPONSE FIELD NAMES (from live API sample responses in docs):
  /shops         → id, shop_name, address, phone_number, city, email, is_enable, updated_date
  /category      → id, category_name, created_at, updated_date
  /payment_types → id, payment_type_name, shops[], created_date, updated_date
  /receipts      → receipt_number (IS the PK — no separate id),
                   receipt_type, receipt_date_time, total_money,
                   customer_id, total_discount (decimal), total_discounts (array — ignore),
                   employee_id, shop_id, note, total_tax, total_charge,
                   line_products[], payments[], receipt_delete_status
"""

import requests
import time
import os
import urllib3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from logger import get_logger

log = get_logger(__name__)

# Suppress SSL warnings for spdeveloperapi.nvision.lk cert issues
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Support both production and dev API endpoints
BASE_URL     = os.getenv(
    "SALESPLAY_BASE_URL",
    "https://spdeveloperapi.nvision.lk/v1.0"
)
PAGE_SIZE    = 250
RATE_SLEEP   = 1.1
DEFAULT_DAYS = 90
DT_FMT       = "%Y-%m-%d %H:%M:%S"


# ── API Client ────────────────────────────────────────────────────────────────

class SalesPlayAPIClient:
    """
    Auth: "Token: Bearer <token>"  — NOT "Authorization: Bearer"
    All filters sent as JSON body on GET requests.
    """

    def __init__(self, api_token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Token":        f"Bearer {api_token}",
            "Content-Type": "application/json",
        })
        # Disable SSL verification for spdeveloperapi.nvision.lk cert issues
        self.session.verify = False

    def _get(self, endpoint: str, body: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        request_body = body or {}
        
        log.info("SalesPlay API Request", url=url, params=request_body)
        log.debug("API Request Details", 
                 url=url, 
                 body=request_body,
                 headers={k: v for k, v in self.session.headers.items() 
                         if k.lower() != 'token'})

        for attempt in range(3):
            try:
                resp = self.session.get(url, json=request_body, timeout=30, verify=False)
            except requests.exceptions.ConnectionError as exc:
                log.error("Connection error", error=str(exc), attempt=attempt)
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.error("Timeout", url=url, attempt=attempt)
                time.sleep(3)
                continue

            log.debug("API Response", 
                     status_code=resp.status_code,
                     headers=dict(resp.headers),
                     body_preview=resp.text[:500] if resp.text else "(empty)")

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                log.warning("Rate limited", wait_seconds=wait)
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                raise Exception("SalesPlay API token is invalid or expired.")

            if resp.status_code == 403:
                raise Exception("SalesPlay API: access forbidden.")

            if not resp.ok:
                preview = resp.text[:300] if resp.text else "(empty)"
                raise Exception(f"SalesPlay API HTTP {resp.status_code}: {preview}")

            try:
                response_data = resp.json()
                log.debug("API Response JSON", 
                         endpoint=endpoint,
                         data=response_data)
                return response_data
            except ValueError:
                raise Exception(f"SalesPlay returned non-JSON: {resp.text[:200]}")

        raise Exception("SalesPlay API: failed after 3 retries.")

    def _paginate(self, endpoint: str, key: str, body: dict) -> List[Dict]:
        """
        Caller provides full body dict with date filters.
        Injects limit + cursor and loops until exhausted.
        
        CRITICAL: Detects duplicate records to prevent infinite loops
        (SalesPlay API bug returns same data with different cursors)
        """
        results: List[Dict] = []
        seen_ids: set = set()
        b           = dict(body)
        b["limit"]  = PAGE_SIZE
        prev_cursor = None
        page        = 0
        duplicate_pages = 0

        while True:
            page += 1
            data  = self._get(endpoint, b)
            items = data.get(key, [])
            
            # Track duplicates by ID
            new_items = []
            for item in items:
                item_id = item.get("id") or item.get("receipt_number")
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    new_items.append(item)
            
            results.extend(new_items)
            
            log.info(f"Page {page}", endpoint=endpoint,
                     items=len(items), new_items=len(new_items), 
                     total=len(results), cursor=data.get("cursor"))
            
            # Stop if no new items (all duplicates)
            if len(new_items) == 0 and len(items) > 0:
                duplicate_pages += 1
                log.warning("Duplicate page detected", 
                           endpoint=endpoint, page=page,
                           duplicate_count=duplicate_pages)
                if duplicate_pages >= 3:
                    log.warning("Stopping pagination - API returning duplicates",
                               endpoint=endpoint, total_unique=len(results))
                    break

            new_cursor = data.get("cursor")
            if not new_cursor or not items or new_cursor == prev_cursor:
                break

            prev_cursor = new_cursor
            b["cursor"] = new_cursor
            time.sleep(RATE_SLEEP)

        return results

    def validate(self) -> dict:
        """Validate token via /merchant (no body needed)."""
        data = self._get("/merchant", {})
        return {
            "business_name": (
                data.get("merchant_name") or
                data.get("name") or
                data.get("business_name") or
                "SalesPlay Account"
            ),
            "shop_count":  data.get("shop_count", 0),
            "currency":    data.get("currency", ""),
            "merchant_id": data.get("id", ""),
        }


# ── Value helpers ─────────────────────────────────────────────────────────────

def _default_since(days: int = DEFAULT_DAYS) -> datetime:
    return datetime.now() - timedelta(days=days)


def _dt(val: Any) -> Optional[str]:
    """Any date string → MySQL "YYYY-MM-DD HH:MM:SS". None on failure."""
    if not val:
        return None
    try:
        s = str(val).strip()
        if "T" in s or s.endswith("Z"):
            s = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s).strftime(DT_FMT)
        for fmt in (DT_FMT, "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).strftime(DT_FMT)
            except ValueError:
                continue
        return None
    except Exception:
        return None


def _dec(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _str(val: Any, maxlen: int = None) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return s[:maxlen] if maxlen else s


def _bool_int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    return 1 if val else 0


def _lookup_table(cursor, table: str, id_col: str, name_col: str) -> Dict[str, str]:
    try:
        cursor.execute(f"SELECT `{id_col}`, `{name_col}` FROM `{table}`")
        return {str(row[0]): str(row[1] or "") for row in cursor.fetchall()}
    except Exception as exc:
        log.warning("Lookup failed", table=table, error=str(exc))
        return {}


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_shops(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /shops
    Filters : shop_ids, updated_at_min, updated_at_max, limit, cursor
    Response: shops[] → id, shop_name, address, phone_number, city, email,
                        is_enable, updated_date
    NOTE: uses updated_at_min NOT created_at_min
    """
    body: Dict = {}
    if since:
        body["updated_at_min"] = since.strftime(DT_FMT)

    data  = client._get("/shops", body)
    shops = data.get("shops", [])

    count = 0
    for s in shops:
        if budget is not None and not budget.request():
            break
        sid = _str(s.get("id"), 64)
        if not sid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}shops`
                (id, shop_name, address, phone, email, currency, country,
                 timezone, status, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                shop_name=VALUES(shop_name),
                address=VALUES(address),
                phone=VALUES(phone),
                email=VALUES(email),
                status=VALUES(status),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            sid,
            _str(s.get("shop_name"), 255),
            _str(s.get("address"), 500),
            _str(s.get("phone_number"), 50),
            _str(s.get("email"), 255),
            None,                               # not in response
            None,                               # not in response
            None,                               # not in response
            _str(s.get("is_enable"), 20),
            None,                               # not in response
            _dt(s.get("updated_date")),
        ))
        count += 1

    log.info("Synced shops", prefix=prefix, count=count)
    return count


def sync_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                    since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /category  (singular endpoint, plural response key)
    Filters : category_ids, created_at_min, created_at_max, limit, cursor
    Response: categories[] → id, category_name, created_at, updated_date
    """
    body: Dict = {"category_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    items = client._paginate("/category", "categories", body)

    count = 0
    for c in items:
        if budget is not None and not budget.request():
            break
        cid = _str(c.get("id"), 64)
        if not cid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}categories`
                (id, category_name, color, shop_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                category_name=VALUES(category_name),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            cid,
            _str(c.get("category_name"), 255),
            None,                               # not in response
            None,                               # not in response
            _dt(c.get("created_at")),
            _dt(c.get("updated_date")),
        ))
        count += 1

    log.info("Synced categories", prefix=prefix, count=count)
    return count


def sync_sub_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                        since: Optional[datetime] = None) -> int:
    """
    GET /sub_category
    Filters : sub_category_ids, created_at_min, created_at_max, limit, cursor
    Response: sub_categories[]
    No dedicated DB table — skip gracefully (returns 0).
    """
    body: Dict = {"sub_category_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    try:
        items = client._paginate("/sub_category", "sub_categories", body)
        log.info("Sub-categories fetched (no table — skipped)",
                 prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Sub-categories sync skipped", error=str(exc))

    return 0


def sync_measurements(client: SalesPlayAPIClient, cursor, prefix: str,
                      since: Optional[datetime] = None) -> int:
    """
    GET /measurements
    Filters : measurement_ids, created_at_min, created_at_max, limit, cursor
    Response: measurements[]
    No dedicated DB table — skip gracefully (returns 0).
    """
    body: Dict = {"measurement_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    try:
        items = client._paginate("/measurements", "measurements", body)
        log.info("Measurements fetched (no table — skipped)",
                 prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Measurements sync skipped", error=str(exc))

    return 0


def sync_suppliers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None) -> int:
    """
    GET /suppliers
    Filters : supplier_ids, updated_at_min, updated_at_max, limit, cursor
    Response: suppliers[]
    NOTE: uses updated_at_min NOT created_at_min (same as shops)
    No dedicated DB table — skip gracefully (returns 0).
    """
    body: Dict = {"supplier_ids": ""}
    if since:
        body["updated_at_min"] = since.strftime(DT_FMT)

    try:
        items = client._paginate("/suppliers", "suppliers", body)
        log.info("Suppliers fetched (no table — skipped)",
                 prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Suppliers sync skipped", error=str(exc))

    return 0


def sync_taxes(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None) -> int:
    """
    GET /taxes
    Filters : tax_ids, created_at_min, created_at_max, limit, cursor
    Response: taxes[]
    No dedicated DB table — skip gracefully (returns 0).
    """
    body: Dict = {"tax_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    try:
        items = client._paginate("/taxes", "taxes", body)
        log.info("Taxes fetched (no table — skipped)",
                 prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Taxes sync skipped", error=str(exc))

    return 0


def sync_payment_types(client: SalesPlayAPIClient, cursor, prefix: str,
                       since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /payment_types
    Filters : payment_type_ids, created_at_min, created_at_max,
              updated_at_min, updated_at_max, limit, cursor
    Response: payment_types[] → id, payment_type_name, shops[], created_date, updated_date
    """
    body: Dict = {"payment_type_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
        body["updated_at_min"] = since.strftime(DT_FMT)

    items = client._paginate("/payment_types", "payment_types", body)

    count = 0
    for pt in items:
        ptid = _str(pt.get("id"), 64)
        if not ptid:
            continue
        if budget is not None and not budget.request():
            break
        cursor.execute(f"""
            INSERT INTO `{prefix}payment_types`
                (id, payment_name, payment_type, is_active, shop_id,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                payment_name=VALUES(payment_name),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            ptid,
            _str(pt.get("payment_type_name"), 255),
            None,                               # payment_type_category not in response
            1,                                  # assume active if returned
            None,                               # no direct shop_id on record
            _dt(pt.get("created_date")),        # API: "created_date"
            _dt(pt.get("updated_date")),        # API: "updated_date"
        ))
        count += 1

    log.info("Synced payment_types", prefix=prefix, count=count)
    return count


def sync_customers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /customers
    Filters : customer_ids, email, created_at_min, created_at_max, limit, cursor
    Response: customers[]
    """
    if since is None:
        since = _default_since()

    body: Dict = {
        "customer_ids":   "",
        "email":          "",
        "created_at_min": since.strftime(DT_FMT),
    }
    items = client._paginate("/customers", "customers", body)

    count = 0
    for c in items:
        if budget is not None and not budget.request():
            break
        cid = _str(c.get("id"), 64)
        if not cid:
            continue

        first = (_str(c.get("first_name")) or "").strip()
        last  = (_str(c.get("last_name"))  or "").strip()
        full  = f"{first} {last}".strip() or _str(c.get("name"), 255) or "Unknown"

        cursor.execute(f"""
            INSERT INTO `{prefix}customers`
                (id, customer_name, email, phone_number, customer_code, note,
                 total_visits, total_spent, points_balance, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                customer_name=VALUES(customer_name),
                email=VALUES(email),
                phone_number=VALUES(phone_number),
                note=VALUES(note),
                total_visits=VALUES(total_visits),
                total_spent=VALUES(total_spent),
                points_balance=VALUES(points_balance),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            cid,
            _str(full, 255),
            _str(c.get("email"), 255),
            _str(c.get("phone") or c.get("phone_number"), 50),
            _str(c.get("customer_code"), 100),
            _str(c.get("description") or c.get("note")),
            int(c.get("total_visits") or 0),
            _dec(c.get("total_money_spent") or c.get("total_spent"), 0),
            _dec(c.get("points_balance"), 0),
            _dt(c.get("created_at")),
            _dt(c.get("updated_at") or c.get("updated_date")),
        ))
        count += 1

    log.info("Synced customers", prefix=prefix, count=count)
    return count


def sync_products(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /products
    Filters : product_ids, created_at_min, created_at_max, limit, cursor
    Response: products[]
    """
    if since is None:
        since = _default_since()

    body: Dict = {
        "product_ids":    "",
        "created_at_min": since.strftime(DT_FMT),
    }
    items = client._paginate("/products", "products", body)

    count = 0
    for p in items:
        if budget is not None and not budget.request():
            break
        pid = _str(p.get("id"), 64)
        if not pid:
            continue

        variants      = p.get("variants") or []
        first         = next((v for v in variants if not v.get("deleted_at")),
                             variants[0] if variants else {})
        variant_shops = first.get("shops") or []
        price         = _dec(variant_shops[0].get("price") if variant_shops else None)
        if price is None:
            price = _dec(first.get("price"), 0)

        cursor.execute(f"""
            INSERT INTO `{prefix}products`
                (id, product_name, description, category_id, reference_id,
                 sold_by_weight, is_active, primary_supplier_id, track_stock,
                 variant_id, sku, barcode, cost, price, purchase_cost,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                product_name=VALUES(product_name),
                description=VALUES(description),
                category_id=VALUES(category_id),
                is_active=VALUES(is_active),
                variant_id=VALUES(variant_id),
                sku=VALUES(sku),
                barcode=VALUES(barcode),
                cost=VALUES(cost),
                price=VALUES(price),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            pid,
            _str(p.get("product_name") or p.get("name"), 500),
            _str(p.get("description")),
            _str(p.get("category_id"), 64),
            _str(p.get("product_code") or p.get("reference_id"), 100),
            _bool_int(p.get("sold_by_weight")),
            0 if p.get("deleted_at") else 1,
            _str(p.get("primary_supplier_id"), 64),
            _bool_int(p.get("stock_control") or p.get("track_stock")),
            _str(first.get("id"), 64),
            _str(first.get("product_code") or first.get("sku"), 100),
            _str(first.get("barcode"), 100),
            _dec(first.get("default_cost") or first.get("cost"), 0),
            price or 0.0,
            _dec(first.get("purchase_cost"), 0),
            _dt(p.get("created_at")),
            _dt(p.get("updated_at") or p.get("updated_date")),
        ))
        count += 1

    log.info("Synced products", prefix=prefix, count=count)
    return count


def sync_receipts(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /receipts
    Filters : receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor
    Response: receipts[]

    CRITICAL:
      - created_at_min + created_at_max REQUIRED — 401 without date range
      - receipt_number IS primary key (no separate id field)
      - receipt_date_time = "2022-07-20 17:21:26" is the timestamp
      - total_discount = decimal (use this)
      - total_discounts = array (ignore)
      - line_products[] is array key (NOT line_items)
      - payments[] has payment info
      - receipt_delete_status bool → VOID / COMPLETED
    """
    if since is None:
        since = _default_since()

    since_str = since.strftime(DT_FMT)
    now_str   = datetime.now().strftime(DT_FMT)

    body: Dict = {
        "receipt_numbers": "",
        "shop_id":         "",
        "created_at_min":  since_str,
        "created_at_max":  now_str,
    }

    log.info("Syncing receipts",
             created_at_min=since_str, created_at_max=now_str, prefix=prefix)

    shop_map = _lookup_table(cursor, f"{prefix}shops",         "id", "shop_name")
    cust_map = _lookup_table(cursor, f"{prefix}customers",     "id", "customer_name")
    pay_map  = _lookup_table(cursor, f"{prefix}payment_types", "id", "payment_name")

    receipts = client._paginate("/receipts", "receipts", body)

    receipt_count = 0
    line_count    = 0

    for r in receipts:
        if budget is not None and not budget.request():
            break
        r_id = _str(r.get("receipt_number"), 64)   # receipt_number IS the PK
        if not r_id:
            continue

        shop_id    = _str(r.get("shop_id"), 64)
        cust_id    = _str(r.get("customer_id"), 64) if r.get("customer_id") else None
        receipt_dt = _dt(r.get("receipt_date_time") or r.get("created_at"))

        total_money    = _dec(r.get("total_money"), 0)
        total_discount = _dec(r.get("total_discount"), 0)   # decimal field
        total_tax      = _dec(r.get("total_tax"), 0)

        # Extract first payment from payments[] array
        payments   = r.get("payments") or []
        pay_id     = None
        pay_amount = total_money
        if payments:
            fp         = payments[0]
            pay_id     = _str(fp.get("payment_type_id"), 64)
            pay_amount = _dec(
                fp.get("money_amount") or fp.get("amount"), total_money
            )

        is_deleted = bool(r.get("receipt_delete_status"))

        cursor.execute(f"""
            INSERT INTO `{prefix}receipts`
                (id, receipt_number, shop_id, shop_name,
                 customer_id, customer_name,
                 created_at, updated_at,
                 total_money, total_discount, total_tax,
                 points_earned, points_deducted, note,
                 receipt_type, status,
                 payment_type_id, payment_type_name, payment_amount)
            VALUES (%s,%s,%s,%s, %s,%s, %s,%s, %s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s)
            ON DUPLICATE KEY UPDATE
                total_money=VALUES(total_money),
                total_discount=VALUES(total_discount),
                total_tax=VALUES(total_tax),
                status=VALUES(status),
                shop_name=VALUES(shop_name),
                customer_id=VALUES(customer_id),
                customer_name=VALUES(customer_name),
                payment_type_id=VALUES(payment_type_id),
                payment_type_name=VALUES(payment_type_name),
                payment_amount=VALUES(payment_amount),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            r_id, r_id,
            shop_id, shop_map.get(shop_id, ""),
            cust_id, cust_map.get(cust_id, "") if cust_id else None,
            receipt_dt, receipt_dt,
            total_money, total_discount, total_tax,
            0.0, 0.0,                               # not in API response
            _str(r.get("note")),
            _str(r.get("receipt_type"), 30) or "SALE",
            "VOID" if is_deleted else "COMPLETED",
            pay_id, pay_map.get(pay_id, "") if pay_id else None,
            pay_amount,
        ))
        receipt_count += 1

        # "line_products" per API docs + live sample (NOT line_items)
        for idx, item in enumerate(r.get("line_products") or []):
            item_id = _str(
                item.get("id") or
                item.get("product_line_no") or
                f"{r_id}_{idx}",
                128
            )
            prod_id       = _str(item.get("product_id"), 64)
            unit_price    = _dec(item.get("price") or item.get("product_unit_price"), 0)
            qty           = _dec(
                item.get("quantity") or item.get("product_qty") or item.get("qty"), 1
            )
            line_total    = _dec(
                item.get("total") or item.get("product_price") or item.get("total_money"),
                (unit_price or 0) * (qty or 1)
            )
            line_discount = _dec(
                item.get("discount_amount") or item.get("product_discount") or
                item.get("total_discount"), 0
            )

            cursor.execute(f"""
                INSERT INTO `{prefix}receipt_line_items`
                    (id, receipt_id, product_id, variant_id, product_name, sku,
                     quantity, price, gross_total_money, total_discount,
                     total_money, cost, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    quantity=VALUES(quantity),
                    price=VALUES(price),
                    gross_total_money=VALUES(gross_total_money),
                    total_discount=VALUES(total_discount),
                    total_money=VALUES(total_money),
                    product_name=VALUES(product_name),
                    cost=VALUES(cost),
                    synced_at=NOW()
            """, (
                item_id, r_id,
                prod_id,
                _str(item.get("variant_id"), 64),
                _str(item.get("product_name") or item.get("name"), 500),
                _str(item.get("product_code") or item.get("sku"), 100),
                qty, unit_price, line_total, line_discount, line_total,
                _dec(item.get("product_cost") or item.get("cost"), 0),
                receipt_dt,
            ))
            line_count += 1

    log.info("Synced receipts", prefix=prefix,
             receipts=receipt_count, line_items=line_count,
             total_rows=receipt_count + line_count)
    return receipt_count + line_count