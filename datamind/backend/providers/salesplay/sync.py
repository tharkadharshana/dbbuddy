"""
providers/salesplay/sync.py
===========================
<<<<<<< HEAD
Fetches data from SalesPlay REST API and upserts into DataMind tables.

VERIFIED FROM OFFICIAL API DOCS (spdeveloper.nvision.lk) + POSTMAN COLLECTION:

  Base URL : https://api.salesplaypos.com/v1.0
  Auth     : Header key = "Token", value = "Bearer <token>"  (NOT Authorization)
  Filters  : JSON body on GET requests
  Date fmt : "YYYY-MM-DD HH:MM:SS" (24-hour)

ENDPOINT FILTER KEYS (exact from API docs):
  GET /shops          → shop_ids, updated_at_min, updated_at_max, limit, cursor
  GET /category       → category_ids, created_at_min, created_at_max, limit, cursor
  GET /payment_types  → payment_type_ids, created_at_min, created_at_max,
                        updated_at_min, updated_at_max, limit, cursor
  GET /products       → product_ids, created_at_min, created_at_max, limit, cursor
  GET /customers      → customer_ids, email, created_at_min, created_at_max, limit, cursor
  GET /receipts       → receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor

ACTUAL RESPONSE FIELD NAMES (from live API sample responses in docs):
  /shops        → id, shop_name, address, phone_number, city, email,
                  is_enable, updated_date
  /category     → id, category_name, created_at, updated_date
  /payment_types→ id, payment_type_name, shops[], created_date, updated_date
  /receipts     → receipt_number (IS the PK — no separate id field),
                  receipt_type, receipt_date_time, total_money,
                  customer_id, total_discount, total_discounts[],
                  employee_id, shop_id, pos_device_id,
                  order_type, order_number, note,
                  total_tax, total_charge,
                  line_products[], payments[],
                  receipt_delete_status, receipt_delete_date_time

BUGS FIXED vs previous version:
  1. sync_receipts had rogue probe client._get("/receipts", {'limit': 1})
     → No date range → API returns 401. REMOVED.
  2. sync_shops was passing created_at_min via paginate() helper
     → /shops only accepts updated_at_min. Fixed.
  3. Response field names corrected per live API samples:
     shop_name (not name), updated_date (not updated_at),
     is_enable (not status), created_date (not created_at) for payment_types,
     receipt_number as PK (not id), receipt_date_time (not created_at),
     total_discount decimal (total_discounts is array — unused),
     line_products[] (not line_items[])
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
"""

import requests
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from logger import get_logger

log = get_logger(__name__)

<<<<<<< HEAD
BASE_URL     = "https://api.salesplaypos.com/v1.0"
PAGE_SIZE    = 250
RATE_SLEEP   = 1.1
DEFAULT_DAYS = 90
DT_FMT       = "%Y-%m-%d %H:%M:%S"
=======
# Production API - NOT the developer sandbox
BASE_URL      = "https://api.salesplaypos.com/v1.0"
PAGE_SIZE     = 250      # max per page per docs
RATE_SLEEP    = 1.1      # 300 req/300s limit → 1 req/sec
DEFAULT_DAYS  = 90       # default lookback for first full sync
DT_FMT        = "%Y-%m-%d %H:%M:%S"   # SalesPlay date format
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f


# ── API Client ────────────────────────────────────────────────────────────────

class SalesPlayAPIClient:
    """
<<<<<<< HEAD
    Auth: "Token: Bearer <token>"  — NOT "Authorization: Bearer"
    All filters sent as JSON body on GET requests.
=======
    Thin wrapper around SalesPlay REST API.

    Critical quirks:
      1. Auth header key is "Token" (NOT "Authorization")
      2. All filters go in the JSON body — even on GET requests
      3. /merchant endpoint for lightweight token validation (no body needed)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    """

    def __init__(self, api_token: str):
        self.session = requests.Session()
<<<<<<< HEAD
=======
        # IMPORTANT: header key is "Token", value is "Bearer <token>"
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        self.session.headers.update({
            "Token":        f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def _get(self, endpoint: str, body: dict = None) -> dict:
<<<<<<< HEAD
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.info("SalesPlay API Request", url=url, params=body)

        for attempt in range(3):
            try:
                resp = self.session.request("GET", url, json=body or {}, timeout=30)
            except requests.exceptions.ConnectionError as exc:
                log.error("Connection error", error=str(exc), attempt=attempt)
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.error("Timeout", url=url, attempt=attempt)
=======
        """
        GET request with JSON body (SalesPlay filter pattern).
        Retries up to 3 times on network errors or 429.
        """
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        log.info(f"🔵 API GET request", endpoint=endpoint, url=url, body_keys=list((body or {}).keys()))

        for attempt in range(3):
            try:
                resp = self.session.request(
                    "GET", url,
                    json=body or {},
                    timeout=30,
                )
                log.info(f"📡 Response received", status=resp.status_code, size=len(resp.text))
            except requests.exceptions.ConnectionError as exc:
                log.error(f"❌ Connection error", error=str(exc), attempt=attempt)
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.error(f"❌ Timeout", url=url, attempt=attempt)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
                time.sleep(3)
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
<<<<<<< HEAD
                log.warning("Rate limited", wait_seconds=wait)
=======
                log.warning(f"⏸️ Rate limited", wait_seconds=wait)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
                time.sleep(wait)
                continue

            if resp.status_code == 401:
<<<<<<< HEAD
                raise Exception("SalesPlay API token is invalid or expired.")

            if resp.status_code == 403:
                raise Exception("SalesPlay API: access forbidden.")

            if not resp.ok:
                preview = resp.text[:300] if resp.text else "(empty)"
                raise Exception(f"SalesPlay API HTTP {resp.status_code}: {preview}")

            try:
                return resp.json()
            except ValueError:
                raise Exception(f"SalesPlay returned non-JSON: {resp.text[:200]}")

        raise Exception("SalesPlay API: failed after 3 retries.")

    def _paginate(self, endpoint: str, key: str, body: dict) -> List[Dict]:
        """
        Caller provides full body dict with date filters.
        This method injects limit + cursor and loops until exhausted.
        """
        results: List[Dict] = []
        b           = dict(body)
        b["limit"]  = PAGE_SIZE
        prev_cursor = None
        page        = 0

        while True:
            page += 1
            data  = self._get(endpoint, b)
            items = data.get(key, [])
            results.extend(items)
            log.info(f"Page {page}", endpoint=endpoint,
                     items=len(items), total=len(results))

            new_cursor = data.get("cursor")
=======
                log.error(f"❌ Auth failed - token invalid or expired")
                raise Exception(
                    "SalesPlay API token is invalid or expired. "
                    "Go to SalesPlay Backoffice → Integrations → Access Token → Generate Token. "
                    "Note: the Auth header key must be 'Token', not 'Authorization'."
                )

            if resp.status_code == 403:
                log.error(f"❌ Access forbidden")
                raise Exception("SalesPlay API: access forbidden. Check account permissions.")

            if not resp.ok:
                body_preview = resp.text[:300] if resp.text else "(empty)"
                log.error(f"❌ HTTP error", status=resp.status_code, response_preview=body_preview)
                raise Exception(f"SalesPlay API HTTP {resp.status_code}: {body_preview}")

            try:
                data = resp.json()
                log.info(f"✅ Parsed response", keys=list(data.keys()) if isinstance(data, dict) else "array", items=len(data.get('shops', [])) if isinstance(data, dict) and 'shops' in data else len(data.get('categories', [])) if isinstance(data, dict) and 'categories' in data else "?")
                return data
            except ValueError as e:
                log.error(f"❌ Failed to parse JSON", error=str(e), response_preview=resp.text[:200])
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
        log.info(f"📖 Starting pagination", endpoint=endpoint, key=key, since=since, until=until)

        while True:
            page += 1
            if cursor:
                b["cursor"] = cursor
                log.info(f"📄 Fetching page {page}", endpoint=endpoint, cursor=cursor[:30])
            else:
                log.info(f"📄 Fetching page {page}", endpoint=endpoint)

            data  = self._get(endpoint, b)
            items = data.get(key, [])
            results.extend(items)
            log.info(f"✅ Page {page} complete", items=len(items), total_so_far=len(results), endpoint=endpoint)

            new_cursor = data.get("cursor")
            # Stop when: no cursor, no items, or cursor repeats (infinite-loop guard)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
            if not new_cursor or not items or new_cursor == prev_cursor:
                if not new_cursor:
                    log.info(f"🏁 Pagination complete (no cursor)", endpoint=endpoint, pages=page, total=len(results))
                elif not items:
                    log.info(f"🏁 Pagination complete (no items)", endpoint=endpoint, pages=page, total=len(results))
                else:
                    log.info(f"🏁 Pagination complete (cursor repeat)", endpoint=endpoint, pages=page, total=len(results))
                break

<<<<<<< HEAD
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
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        }


# ── Value helpers ─────────────────────────────────────────────────────────────

def _default_since(days: int = DEFAULT_DAYS) -> datetime:
    return datetime.now() - timedelta(days=days)


def _dt(val: Any) -> Optional[str]:
<<<<<<< HEAD
    """Any date string → MySQL "YYYY-MM-DD HH:MM:SS". None on failure."""
=======
    """Parse any ISO/datetime string → MySQL DATETIME string, or None."""
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    if not val:
        return None
    try:
        s = str(val).strip()
<<<<<<< HEAD
        if "T" in s or s.endswith("Z"):
            s = s.replace("Z", "+00:00")
            return datetime.fromisoformat(s).strftime(DT_FMT)
        for fmt in (DT_FMT, "%Y-%m-%d"):
            try:
                return datetime.strptime(s, fmt).strftime(DT_FMT)
            except ValueError:
                continue
        return None
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    except Exception:
        return None


<<<<<<< HEAD
def _dec(val: Any, default: float = 0.0) -> float:
=======
def _dec(val: Any, default: Optional[float] = None) -> Optional[float]:
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
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
<<<<<<< HEAD
=======
    """Convert truthy value to 1/0 for MySQL TINYINT."""
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    if val is None:
        return default
    return 1 if val else 0


def _lookup_table(cursor, table: str, id_col: str, name_col: str) -> Dict[str, str]:
<<<<<<< HEAD
=======
    """Build id→name lookup dict from a DB table. Returns {} on any error."""
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    try:
        cursor.execute(f"SELECT `{id_col}`, `{name_col}` FROM `{table}`")
        return {str(row[0]): str(row[1] or "") for row in cursor.fetchall()}
    except Exception as exc:
<<<<<<< HEAD
        log.warning("Lookup failed", table=table, error=str(exc))
=======
        log.warning("Lookup table query failed", table=table, error=str(exc))
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        return {}


# ── Sync functions ────────────────────────────────────────────────────────────

def sync_shops(client: SalesPlayAPIClient, cursor, prefix: str,
               since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /shops
    Filters : shop_ids, updated_at_min, updated_at_max, limit, cursor
    Response: shops[]
      id, shop_name, address, phone_number, city, email,
      longitude, latitude, terminal_list, is_enable, updated_date
    """
    body: Dict = {}
    if since:
        body["updated_at_min"] = since.strftime(DT_FMT)

    data  = client._get("/shops", body)
=======
    GET /shops → {prefix}shops
    Body: {shop_ids, updated_at_min, updated_at_max, limit, cursor}
    Note: shops use updated_at_min (not created_at_min) per Postman.
    """
    b: Dict = {"limit": PAGE_SIZE}
    if since:
        b["updated_at_min"] = since.strftime(DT_FMT)

    data  = client._get("/shops", b)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    shops = data.get("shops", [])

    count = 0
    for s in shops:
        sid = _str(s.get("id"), 64)
        if not sid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}shops`
<<<<<<< HEAD
                (id, shop_name, address, phone, email, currency, country,
                 timezone, status, created_at, updated_at)
=======
                (id, shop_name, address, phone, email, currency, country, timezone, status,
                 created_at, updated_at)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
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
<<<<<<< HEAD
            _str(s.get("shop_name"), 255),      # API: "shop_name"
            _str(s.get("address"), 500),
            _str(s.get("phone_number"), 50),     # API: "phone_number"
            _str(s.get("email"), 255),
            None,                               # not in response
            None,                               # not in response
            None,                               # not in response
            _str(s.get("is_enable"), 20),        # API: "is_enable"
            None,                               # not in response
            _dt(s.get("updated_date")),          # API: "updated_date"
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        ))
        count += 1

    log.info("Synced shops", prefix=prefix, count=count)
    return count


def sync_categories(client: SalesPlayAPIClient, cursor, prefix: str,
                    since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /category   (endpoint singular, response key plural)
    Filters : category_ids, created_at_min, created_at_max, limit, cursor
    Response: categories[]
      id, category_name, item_classification_code, created_at, updated_date
    """
    body: Dict = {"category_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    items = client._paginate("/category", "categories", body)
=======
    GET /category → {prefix}categories
    Note: endpoint is /category (singular), NOT /categories.
    Response key is "categories" (plural).
    """
    items = client.paginate("/category", "categories", since=since)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

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
                category_name=VALUES(category_name),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            cid,
<<<<<<< HEAD
            _str(c.get("category_name"), 255),   # API: "category_name"
            None,                               # not in response
            None,                               # not in response
            _dt(c.get("created_at")),            # API: "created_at"
            _dt(c.get("updated_date")),          # API: "updated_date"
=======
            _str(c.get("category_name") or c.get("name"), 255),
            _str(c.get("color"), 20),
            _str(c.get("shop_id"), 64),
            _dt(c.get("created_at")),
            _dt(c.get("updated_at")),
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        ))
        count += 1

    log.info("Synced categories", prefix=prefix, count=count)
    return count


def sync_payment_types(client: SalesPlayAPIClient, cursor, prefix: str,
                       since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /payment_types
    Filters : payment_type_ids, created_at_min, created_at_max,
              updated_at_min, updated_at_max, limit, cursor
    Response: payment_types[]
      id, payment_type_name, shops[], created_date, updated_date
    """
    body: Dict = {"payment_type_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
        body["updated_at_min"] = since.strftime(DT_FMT)

    items = client._paginate("/payment_types", "payment_types", body)
=======
    GET /payment_types → {prefix}payment_types
    Body: {payment_type_ids, created_at_min, created_at_max, limit, cursor}
    From Postman create payload: payment_type_code, payment_type_name,
                                 payment_type_category, status (1=active)
    """
    items = client.paginate("/payment_types", "payment_types", since=since)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

    count = 0
    for pt in items:
        ptid = _str(pt.get("id"), 64)
        if not ptid:
            continue
        cursor.execute(f"""
            INSERT INTO `{prefix}payment_types`
<<<<<<< HEAD
                (id, payment_name, payment_type, is_active, shop_id,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                payment_name=VALUES(payment_name),
                updated_at=VALUES(updated_at),
                synced_at=NOW()
        """, (
            ptid,
            _str(pt.get("payment_type_name"), 255),  # API: "payment_type_name"
            None,                                   # payment_type_category not in response
            1,                                      # assume active if returned by API
            None,                                   # individual shop_id not on record
            _dt(pt.get("created_date")),             # API: "created_date"
            _dt(pt.get("updated_date")),             # API: "updated_date"
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        ))
        count += 1

    log.info("Synced payment_types", prefix=prefix, count=count)
    return count


def sync_products(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /products
    Filters : product_ids, created_at_min, created_at_max, limit, cursor
    Response: products[]
=======
    GET /products → {prefix}products
    Body: {product_ids, created_at_min, created_at_max, limit, cursor}
    From Postman create payload:
      product_code, product_name, barcode, description, category_id,
      cost, stock_control, is_variant, variants[]
      variants[]: product_code, option1_value, barcode, default_cost,
                  shops[]: {shop_id, price, available_for_sale, safety_stock}
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    """
    if since is None:
        since = _default_since()

<<<<<<< HEAD
    body: Dict = {
        "product_ids":    "",
        "created_at_min": since.strftime(DT_FMT),
    }
    items = client._paginate("/products", "products", body)
=======
    items = client.paginate("/products", "products", since=since)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

    count = 0
    for p in items:
        pid = _str(p.get("id"), 64)
        if not pid:
            continue
<<<<<<< HEAD

        variants      = p.get("variants") or []
        first         = next((v for v in variants if not v.get("deleted_at")),
                             variants[0] if variants else {})
        variant_shops = first.get("shops") or []
        price         = _dec(variant_shops[0].get("price") if variant_shops else None)
        if price is None:
            price = _dec(first.get("price"), 0)

=======

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

>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
        cursor.execute(f"""
            INSERT INTO `{prefix}products`
                (id, product_name, description, category_id, reference_id,
                 sold_by_weight, is_active, primary_supplier_id, track_stock,
                 variant_id, sku, barcode, cost, price, purchase_cost,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
<<<<<<< HEAD
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
=======
                product_name=VALUES(product_name), description=VALUES(description),
                category_id=VALUES(category_id), is_active=VALUES(is_active),
                variant_id=VALUES(variant_id), sku=VALUES(sku),
                barcode=VALUES(barcode), cost=VALUES(cost), price=VALUES(price),
                updated_at=VALUES(updated_at), synced_at=NOW()
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
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
<<<<<<< HEAD
            price or 0.0,
=======
            price or 0,
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
            _dec(first.get("purchase_cost"), 0),
            _dt(p.get("created_at")),
            _dt(p.get("updated_at") or p.get("updated_date")),
        ))
        count += 1

    log.info("Synced products", prefix=prefix, count=count)
    return count


def sync_customers(client: SalesPlayAPIClient, cursor, prefix: str,
                   since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /customers
    Filters : customer_ids, email, created_at_min, created_at_max, limit, cursor
    Response: customers[]
=======
    GET /customers → {prefix}customers
    Body: {customer_ids, email, created_at_min, created_at_max, limit, cursor}
    From Postman create payload:
      first_name, last_name, email, phone, address, city, region,
      postal_code, country_code, customer_code, description, credit_limit
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    """
    if since is None:
        since = _default_since()

<<<<<<< HEAD
    body: Dict = {
        "customer_ids":   "",
        "email":          "",
        "created_at_min": since.strftime(DT_FMT),
    }
    items = client._paginate("/customers", "customers", body)
=======
    items = client.paginate("/customers", "customers", since=since)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

    count = 0
    for c in items:
        cid = _str(c.get("id"), 64)
        if not cid:
            continue

<<<<<<< HEAD
        first = (_str(c.get("first_name")) or "").strip()
        last  = (_str(c.get("last_name"))  or "").strip()
=======
        # Name: combine first+last or fall back to name field
        first = (_str(c.get("first_name")) or "").strip()
        last  = (_str(c.get("last_name")) or "").strip()
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
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


def sync_receipts(client: SalesPlayAPIClient, cursor, prefix: str,
                  since: Optional[datetime] = None) -> int:
    """
<<<<<<< HEAD
    GET /receipts
    Filters : receipt_numbers, shop_id, created_at_min, created_at_max, limit, cursor
    Response: receipts[]

    CRITICAL:
      - created_at_min + created_at_max REQUIRED — 401 without date range
      - receipt_number IS the primary key (no separate "id" field in response)
      - receipt_date_time = "2022-07-20 17:21:26" is the timestamp
      - total_discount = decimal  /  total_discounts = array (use decimal)
      - line_products[] is array key (NOT line_items)
      - payments[] has payment info per receipt
      - receipt_delete_status = bool → status VOID/COMPLETED
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
    """
    if since is None:
        since = _default_since()

<<<<<<< HEAD
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
=======
    # Pre-load lookups for name enrichment (avoids per-row queries)
    shop_map = _lookup_table(cursor, f"{prefix}shops",         "id", "shop_name")
    cust_map = _lookup_table(cursor, f"{prefix}customers",     "id", "customer_name")
    pay_map  = _lookup_table(cursor, f"{prefix}payment_types", "id", "payment_name")

    log.info("Syncing receipts", since=since.strftime(DT_FMT), prefix=prefix)

    receipts = client.paginate("/receipts", "receipts", since=since)
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

    receipt_count = 0
    line_count    = 0

    for r in receipts:
<<<<<<< HEAD
        r_id = _str(r.get("receipt_number"), 64)   # receipt_number IS the PK
        if not r_id:
            continue

        shop_id    = _str(r.get("shop_id"), 64)
        cust_id    = _str(r.get("customer_id"), 64) if r.get("customer_id") else None
        receipt_dt = _dt(r.get("receipt_date_time") or r.get("created_at"))

        total_money    = _dec(r.get("total_money"), 0)
        total_discount = _dec(r.get("total_discount"), 0)  # decimal, not array
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
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

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
<<<<<<< HEAD
=======
                points_earned=VALUES(points_earned),
                points_deducted=VALUES(points_deducted),
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
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
<<<<<<< HEAD
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
=======
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
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f

            cursor.execute(f"""
                INSERT INTO `{prefix}receipt_line_items`
                    (id, receipt_id, product_id, variant_id, product_name, sku,
                     quantity, price, gross_total_money, total_discount,
                     total_money, cost, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
<<<<<<< HEAD
                    quantity=VALUES(quantity),
                    price=VALUES(price),
=======
                    quantity=VALUES(quantity), price=VALUES(price),
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
                    gross_total_money=VALUES(gross_total_money),
                    total_discount=VALUES(total_discount),
                    total_money=VALUES(total_money),
                    product_name=VALUES(product_name),
                    cost=VALUES(cost),
                    synced_at=NOW()
            """, (
<<<<<<< HEAD
                item_id, r_id,
=======
                item_id,
                r_id,
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
                prod_id,
                _str(item.get("variant_id"), 64),
                _str(item.get("product_name") or item.get("name"), 500),
                _str(item.get("product_code") or item.get("sku"), 100),
<<<<<<< HEAD
                qty, unit_price, line_total, line_discount, line_total,
=======
                qty,
                unit_price,
                line_total,
                line_discount,
                line_total,
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
                _dec(item.get("product_cost") or item.get("cost"), 0),
                receipt_dt,
            ))
            line_count += 1

    log.info("Synced receipts", prefix=prefix,
             receipts=receipt_count, line_items=line_count,
             total_rows=receipt_count + line_count)
<<<<<<< HEAD
    return receipt_count + line_count
=======
    return receipt_count + line_count
>>>>>>> 9aaefabc08a5914d25a9fd21048a256b2a3aed3f
