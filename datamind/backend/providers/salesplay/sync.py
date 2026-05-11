"""
providers/salesplay/sync.py
===========================
Fetches data from the SalesPlay REST API and upserts into DataMind tables.

VERIFIED FROM OFFICIAL POSTMAN COLLECTION:
  Base URL: https://api.salesplaypos.com/v1.0  (production)
  Auth:     Header key = "Token", value = "Bearer <token>"
            (NOT "Authorization: Bearer" — this is the #1 source of 401 errors)
  Filters:  ALL sent as JSON body on GET requests (SalesPlay's quirk)
  Date fmt: "YYYY-MM-DD HH:MM:SS"  (e.g. "2026-01-02 00:00:00")
  Pagination: cursor in body, max limit 250

Endpoints used (analytics/predictions only):
  GET /merchant     → validate token (no body needed)
  GET /shops        → {shop_ids, updated_at_min, updated_at_max, limit, cursor}
  GET /category     → {category_ids, created_at_min, created_at_max, limit, cursor}
  GET /payment_types→ {payment_type_ids, created_at_min, created_at_max, limit, cursor}
  GET /products     → {product_ids, created_at_min, created_at_max, limit, cursor}
  GET /customers    → {customer_ids, email, created_at_min, created_at_max, limit, cursor}
  GET /receipts     → {receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor}

Default sync range: last 90 days on first connect, delta on subsequent syncs.
"""

import requests
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from logger import get_logger

log = get_logger(__name__)

# Production API - NOT the developer sandbox
BASE_URL      = "https://api.salesplaypos.com/v1.0"
PAGE_SIZE     = 250      # max per page per docs
RATE_SLEEP    = 1.1      # 300 req/300s limit → 1 req/sec
DEFAULT_DAYS  = 90       # default lookback for first full sync
DT_FMT        = "%Y-%m-%d %H:%M:%S"   # SalesPlay date format


# ── API Client ────────────────────────────────────────────────────────────────

class SalesPlayAPIClient:
    """
    Thin wrapper around SalesPlay REST API.

    Critical quirks:
      1. Auth header key is "Token" (NOT "Authorization")
      2. All filters go in the JSON body — even on GET requests
      3. /merchant endpoint for lightweight token validation (no body needed)
    """

    def __init__(self, api_token: str):
        self.session = requests.Session()
        # IMPORTANT: header key is "Token", value is "Bearer <token>"
        self.session.headers.update({
            "Token":        f"Bearer {api_token}",
            "Content-Type": "application/json",
        })
        self._token = api_token

    def _get(self, endpoint: str, body: dict = None) -> dict:
        """
        GET request with JSON body (SalesPlay filter pattern).
        Retries up to 3 times on network errors or 429.
        """
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.info("SalesPlay API GET", url=url)

        for attempt in range(3):
            try:
                resp = self.session.request(
                    "GET", url,
                    json=body or {},
                    timeout=30,
                )
            except requests.exceptions.ConnectionError as exc:
                log.warning("SalesPlay connection error", error=str(exc), attempt=attempt)
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.warning("SalesPlay timeout", url=url, attempt=attempt)
                time.sleep(3)
                continue

            log.debug("SalesPlay response", status=resp.status_code, url=url)

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                log.warning("SalesPlay rate limited", wait_seconds=wait)
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                raise Exception(
                    "SalesPlay API token is invalid or expired. "
                    "Go to SalesPlay Backoffice → Integrations → Access Token → Generate Token. "
                    "Note: the Auth header key must be 'Token', not 'Authorization'."
                )

            if resp.status_code == 403:
                raise Exception("SalesPlay API: access forbidden. Check account permissions.")

            if not resp.ok:
                body_preview = resp.text[:300] if resp.text else "(empty)"
                raise Exception(f"SalesPlay API HTTP {resp.status_code}: {body_preview}")

            try:
                return resp.json()
            except ValueError:
                raise Exception(f"SalesPlay returned non-JSON: {resp.text[:200]}")

        raise Exception("SalesPlay API: failed after 3 retries.")

    def paginate(self, endpoint: str, key: str,
                 body: dict = None,
                 since: Optional[datetime] = None,
                 until: Optional[datetime] = None) -> List[Dict]:
        """
        Fetch all pages of a paginated endpoint.
        Date range goes in the JSON body as created_at_min / created_at_max.
        """
        results: List[Dict] = []
        b = dict(body or {})
        b["limit"] = PAGE_SIZE

        if since:
            b["created_at_min"] = since.strftime(DT_FMT)
        if until:
            b["created_at_max"] = until.strftime(DT_FMT)

        cursor     = None
        prev_cursor = None
        page       = 0

        while True:
            page += 1
            if cursor:
                b["cursor"] = cursor

            data  = self._get(endpoint, b)
            items = data.get(key, [])
            results.extend(items)

            log.debug("SalesPlay page done", endpoint=endpoint, page=page,
                      got=len(items), total_so_far=len(results))

            new_cursor = data.get("cursor")
            # Stop when: no cursor, no items, or cursor repeats (infinite-loop guard)
            if not new_cursor or not items or new_cursor == prev_cursor:
                break

            prev_cursor, cursor = cursor, new_cursor
            time.sleep(RATE_SLEEP)

        log.info("SalesPlay paginate complete", endpoint=endpoint,
                 pages=page, total=len(results))
        return results

    def validate(self) -> dict:
        """
        Validate token via /merchant endpoint (lightweight, no body needed).
        Returns merchant info dict or raises on failure.
        """
        # /merchant needs no body — just the Token header
        data = self._get("/merchant", {})
        name = (
            data.get("merchant_name") or
            data.get("name") or
            data.get("business_name") or
            "SalesPlay Account"
        )
        return {
            "business_name": name,
            "shop_count":    data.get("shop_count", 0),
            "currency":      data.get("currency", ""),
            "merchant_id":   data.get("id", ""),
        }


# ── Value helpers ─────────────────────────────────────────────────────────────

def _default_since(days: int = DEFAULT_DAYS) -> datetime:
    return datetime.now() - timedelta(days=days)


def _dt(val: Any) -> Optional[str]:
    """Parse any ISO/datetime string → MySQL DATETIME string, or None."""
    if not val:
        return None
    try:
        s = str(val).strip()
        # Handle "2024-01-15T10:30:00Z", "2024-01-15T10:30:00+05:30", "2024-01-15 10:30:00"
        s = s.replace("Z", "+00:00")
        if "T" in s or ("+" in s and s.index("+") > 10):
            dt = datetime.fromisoformat(s)
        else:
            for fmt in (DT_FMT, "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
        return dt.strftime(DT_FMT)
    except Exception:
        return None


def _dec(val: Any, default: Optional[float] = None) -> Optional[float]:
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
    """Convert truthy value to 1/0 for MySQL TINYINT."""
    if val is None:
        return default
    return 1 if val else 0


def _lookup_table(cursor, table: str, id_col: str, name_col: str) -> Dict[str, str]:
    """Build id→name lookup dict from a DB table. Returns {} on any error."""
    try:
        cursor.execute(f"SELECT `{id_col}`, `{name_col}` FROM `{table}`")
        return {str(row[0]): str(row[1] or "") for row in cursor.fetchall()}
    except Exception as exc:
        log.warning("Lookup table query failed", table=table, error=str(exc))
        return {}


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_shops(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None) -> int:
    """
    GET /shops → {prefix}shops
    Body: {shop_ids, updated_at_min, updated_at_max, limit, cursor}
    Note: shops use updated_at_min (not created_at_min) per Postman.
    """
    b: Dict = {"limit": PAGE_SIZE}
    if since:
        b["updated_at_min"] = since.strftime(DT_FMT)

    data  = client._get("/shops", b)
    shops = data.get("shops", [])

    count = 0
    for s in shops:
        sid = _str(s.get("id"), 64)
        if not sid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}shops`
                (id, shop_name, address, phone, email, currency, country, timezone, status,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                shop_name=VALUES(shop_name), address=VALUES(address),
                phone=VALUES(phone), email=VALUES(email),
                currency=VALUES(currency), country=VALUES(country),
                timezone=VALUES(timezone), status=VALUES(status),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            sid,
            _str(s.get("name") or s.get("shop_name"), 255),
            _str(s.get("address"), 500),
            _str(s.get("phone_number") or s.get("phone"), 50),
            _str(s.get("email"), 255),
            _str(s.get("currency"), 10),
            _str(s.get("country_code") or s.get("country"), 10),
            _str(s.get("timezone"), 100),
            _str(s.get("status"), 20),
            _dt(s.get("created_at")),
            _dt(s.get("updated_at")),
        ))
        count += 1

    log.info("Synced shops", prefix=prefix, count=count)
    return count


def sync_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                    since: Optional[datetime] = None) -> int:
    """
    GET /category → {prefix}categories
    Note: endpoint is /category (singular), NOT /categories.
    Response key is "categories" (plural).
    """
    items = client.paginate("/category", "categories", since=since)

    count = 0
    for c in items:
        cid = _str(c.get("id"), 64)
        if not cid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}categories`
                (id, category_name, color, shop_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                category_name=VALUES(category_name), color=VALUES(color),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            cid,
            _str(c.get("category_name") or c.get("name"), 255),
            _str(c.get("color"), 20),
            _str(c.get("shop_id"), 64),
            _dt(c.get("created_at")),
            _dt(c.get("updated_at")),
        ))
        count += 1

    log.info("Synced categories", prefix=prefix, count=count)
    return count


def sync_payment_types(client: SalesPlayAPIClient, cursor, prefix: str,
                       since: Optional[datetime] = None) -> int:
    """
    GET /payment_types → {prefix}payment_types
    Body: {payment_type_ids, created_at_min, created_at_max, limit, cursor}
    From Postman create payload: payment_type_code, payment_type_name,
                                 payment_type_category, status (1=active)
    """
    items = client.paginate("/payment_types", "payment_types", since=since)

    count = 0
    for pt in items:
        ptid = _str(pt.get("id"), 64)
        if not ptid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}payment_types`
                (id, payment_name, payment_type, is_active, shop_id, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                payment_name=VALUES(payment_name), payment_type=VALUES(payment_type),
                is_active=VALUES(is_active),
                updated_at=VALUES(updated_at), synced_at=NOW()
        """, (
            ptid,
            _str(pt.get("payment_type_name") or pt.get("name"), 255),
            _str(pt.get("payment_type_category") or pt.get("type"), 50),
            # status=1 means active per Postman create payload
            1 if (pt.get("status") == 1 or pt.get("active") or pt.get("is_active")) else 0,
            _str(pt.get("shop_id"), 64),
            _dt(pt.get("created_at")),
            _dt(pt.get("updated_at")),
        ))
        count += 1

    log.info("Synced payment_types", prefix=prefix, count=count)
    return count


def sync_products(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """
    GET /products → {prefix}products
    Body: {product_ids, created_at_min, created_at_max, limit, cursor}
    From Postman create payload:
      product_code, product_name, barcode, description, category_id,
      cost, stock_control, is_variant, variants[]
      variants[]: product_code, option1_value, barcode, default_cost,
                  shops[]: {shop_id, price, available_for_sale, safety_stock}
    """
    if since is None:
        since = _default_since()

    items = client.paginate("/products", "products", since=since)

    count = 0
    for p in items:
        pid = _str(p.get("id"), 64)
        if not pid:
            continue

        # Flatten first active variant for price/cost/sku
        variants = p.get("variants") or []
        first = next(
            (v for v in variants if not v.get("deleted_at")),
            variants[0] if variants else {}
        )

        # Price comes from first shop entry in the variant
        variant_shops = first.get("shops") or []
        price = _dec(variant_shops[0].get("price") if variant_shops else None)
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
                product_name=VALUES(product_name), description=VALUES(description),
                category_id=VALUES(category_id), is_active=VALUES(is_active),
                variant_id=VALUES(variant_id), sku=VALUES(sku),
                barcode=VALUES(barcode), cost=VALUES(cost), price=VALUES(price),
                updated_at=VALUES(updated_at), synced_at=NOW()
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
            price or 0,
            _dec(first.get("purchase_cost"), 0),
            _dt(p.get("created_at")),
            _dt(p.get("updated_at")),
        ))
        count += 1

    log.info("Synced products", prefix=prefix, count=count)
    return count


def sync_customers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None) -> int:
    """
    GET /customers → {prefix}customers
    Body: {customer_ids, email, created_at_min, created_at_max, limit, cursor}
    From Postman create payload:
      first_name, last_name, email, phone, address, city, region,
      postal_code, country_code, customer_code, description, credit_limit
    """
    if since is None:
        since = _default_since()

    items = client.paginate("/customers", "customers", since=since)

    count = 0
    for c in items:
        cid = _str(c.get("id"), 64)
        if not cid:
            continue

        # Name: combine first+last or fall back to name field
        first = (_str(c.get("first_name")) or "").strip()
        last  = (_str(c.get("last_name")) or "").strip()
        full  = f"{first} {last}".strip() or _str(c.get("name"), 255) or "Unknown"

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
            _dt(c.get("updated_at")),
        ))
        count += 1

    log.info("Synced customers", prefix=prefix, count=count)
    return count


def sync_receipts(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """
    GET /receipts → {prefix}receipts + {prefix}receipt_line_items
    Body: {receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor}
    Date format: "YYYY-MM-DD HH:MM:SS"

    From Postman create receipt / credit_note_and_refund payloads:
      Receipt fields: receipt_number, shop_id, line_products[],
                      total (or total_money), discount_amount, tax_amount,
                      customer_id, payment_type_id, created_at / receipt_date_time
      line_products[]: product_id, product_line_no, quantity, product_code,
                       product_name, price, total (or product_price), product_cost,
                       discount_amount

    Sync range default: last 90 days.
    """
    if since is None:
        since = _default_since()

    # Pre-load lookups for name enrichment (avoids per-row queries)
    shop_map = _lookup_table(cursor, f"{prefix}shops",         "id", "shop_name")
    cust_map = _lookup_table(cursor, f"{prefix}customers",     "id", "customer_name")
    pay_map  = _lookup_table(cursor, f"{prefix}payment_types", "id", "payment_name")

    log.info("Syncing receipts", since=since.strftime(DT_FMT), prefix=prefix)

    receipts = client.paginate("/receipts", "receipts", since=since)

    receipt_count = 0
    line_count    = 0

    for r in receipts:
        # Receipt ID: prefer receipt_number (human-readable) over internal id
        r_id = _str(r.get("receipt_number") or r.get("id"), 64)
        if not r_id:
            continue

        shop_id = _str(r.get("shop_id"), 64)
        cust_id = _str(r.get("customer_id"), 64) if r.get("customer_id") else None
        pay_id  = _str(r.get("payment_type_id"), 64) if r.get("payment_type_id") else None

        # Financial fields — normalize field name variants
        total_money    = _dec(r.get("total") or r.get("total_money"), 0)
        total_discount = _dec(r.get("discount_amount") or r.get("total_discount"), 0)
        total_tax      = _dec(r.get("tax_amount") or r.get("total_tax"), 0)

        # Receipt timestamp
        receipt_dt = _dt(r.get("created_at") or r.get("receipt_date_time") or r.get("transaction_date"))

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
                points_earned=VALUES(points_earned),
                points_deducted=VALUES(points_deducted),
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
            receipt_dt, receipt_dt,   # created_at = updated_at if no separate field
            total_money, total_discount, total_tax,
            _dec(r.get("points_earned"), 0),
            _dec(r.get("points_deducted"), 0),
            _str(r.get("note")),
            _str(r.get("receipt_type"), 30) or "SALE",
            _str(r.get("status"), 30) or "COMPLETED",
            pay_id, pay_map.get(pay_id, "") if pay_id else None,
            total_money,  # whole amount as payment (single payment model)
        ))
        receipt_count += 1

        # ── Line items ────────────────────────────────────────────────────
        # From Postman create receipt: field is "line_products"
        # From Postman credit_note:    field is also "line_products"
        line_items = (
            r.get("line_products") or
            r.get("line_items") or
            []
        )

        for idx, item in enumerate(line_items):
            # Line item ID: use product_line_no if available, else ordinal
            item_id = _str(
                item.get("id") or
                item.get("product_line_no") or
                f"{r_id}_{idx}"
            , 128)

            prod_id    = _str(item.get("product_id"), 64)
            unit_price = _dec(item.get("price") or item.get("product_unit_price"), 0)
            qty        = _dec(item.get("quantity") or item.get("product_qty") or item.get("qty"), 1)
            line_total = _dec(
                item.get("total") or item.get("product_price") or item.get("total_money"),
                (unit_price or 0) * (qty or 1)
            )
            line_discount = _dec(item.get("discount_amount") or item.get("product_discount"), 0)

            cursor.execute(f"""
                INSERT INTO `{prefix}receipt_line_items`
                    (id, receipt_id, product_id, variant_id, product_name, sku,
                     quantity, price, gross_total_money, total_discount,
                     total_money, cost, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    quantity=VALUES(quantity), price=VALUES(price),
                    gross_total_money=VALUES(gross_total_money),
                    total_discount=VALUES(total_discount),
                    total_money=VALUES(total_money),
                    product_name=VALUES(product_name),
                    cost=VALUES(cost),
                    synced_at=NOW()
            """, (
                item_id,
                r_id,
                prod_id,
                _str(item.get("variant_id"), 64),
                _str(item.get("product_name") or item.get("name"), 500),
                _str(item.get("product_code") or item.get("sku"), 100),
                qty,
                unit_price,
                line_total,
                line_discount,
                line_total,
                _dec(item.get("product_cost") or item.get("cost"), 0),
                receipt_dt,
            ))
            line_count += 1

    log.info("Synced receipts", prefix=prefix,
             receipts=receipt_count, line_items=line_count,
             total_rows=receipt_count + line_count)
    return receipt_count + line_count
