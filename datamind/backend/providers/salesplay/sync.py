"""
providers/salesplay/sync.py
===========================
Fetches data from SalesPlay REST API and upserts into the shared sp_* tables
and the integration_records table via providers/upsert.py.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONNECTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Base URL : https://spdeveloperapi.nvision.lk/v1.0  (QA: spqaapi.nvision.lk)
  Auth     : Header key = "Token", value = "Bearer <token>"  (NOT Authorization)
  Filters  : JSON body on GET requests (not query params)
  Date fmt : "YYYY-MM-DD HH:MM:SS" (24-hour)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ENDPOINT FILTER KEYS (exact from live API)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
                        NOTE: created_at_min + created_at_max are REQUIRED — 401 without them

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API → DB FIELD MAPPING  (verified against live API responses, 2026-06-18)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

── /shops → sp_shops ────────────────────────────────────────────────────────
  API field         DB field       Notes
  id                id
  shop_name         shop_name
  address           address
  phone_number      phone
  email             email
  is_enable         status         Stored as "1"/"0" string
  updated_date      updated_at
  city              (not stored)   Available in API but omitted
  longitude/lat     (not stored)   Available in API but omitted
  terminal_list     (not stored)   Available in API but omitted
  (no API field)    currency       Always NULL — API doesn't return at shop level
  (no API field)    country        Always NULL — API doesn't return at shop level
  (no API field)    timezone       Always NULL — API doesn't return at shop level

── /category → sp_categories ────────────────────────────────────────────────
  API field              DB field       Notes
  id                     id
  category_name          category_name
  created_at             created_at
  updated_date           updated_at
  item_classification_code (not stored) Available in API but omitted
  (no API field)         color          Always NULL
  (no API field)         shop_id        Always NULL

── /payment_types → sp_payment_types ────────────────────────────────────────
  API field           DB field        Notes
  id                  id
  payment_type_name   payment_name
  payment_type_code   payment_type    Was missing before fix; now stored correctly
  created_date        created_at
  updated_date        updated_at
  shops[]             (not stored)    Available in API but omitted
  (no API field)      is_active       Always 1 (DB default) — all returned types are active
  (no API field)      shop_id         Always NULL

── /customers → sp_customers ────────────────────────────────────────────────
  API field           DB field         Notes
  id                  id
  name                customer_name    API returns full name in "name" (no first/last split);
                                       sync falls back: first_name+last_name → name → "Unknown"
  email               email
  phone_number        phone_number     Also tries c.get("phone") as fallback
  customer_code       customer_code
  description         note
  total_visits        total_visits     API returns as string — cast to int
  total_spent         total_spent      API returns as string — cast to float via _dec()
  total_points        points_balance   API key is "total_points" (not "points_balance");
                                       sync tries points_balance first, falls back to total_points
  created_at          created_at
  updated_at          updated_at       Also tries updated_date as fallback
  (computed)          last_purchase_date  Set post-sync via refresh_customer_last_purchase()
                                         from MAX(sp_receipts.created_at) per customer
  address/city/etc    (not stored)     Available in API but omitted

── /products → sp_products ──────────────────────────────────────────────────
  API field                      DB field       Notes
  id                             id
  product_name / name            product_name
  description                    description
  category_id                    category_id    Often absent for no-variant products;
                                                category_name filled via sp_categories lookup
                                                or direct p.get("category") fallback
  product_code (top-level)       sku            Tried on first variant, then top-level p
  barcode (on variant)           barcode
  cost / default_cost (variant   cost           Fallback chain: variant.default_cost →
    or top-level)                               variant.cost → p.cost
  shops[0].price / variant       price          Fallback chain: variant.shops[0].price →
    shops[0].price / variant                    p.shops[0].price → variant.price → 0
    price                                       IMPORTANT: use _dec(..., None) not _dec(...)
                                                so None sentinel propagates through if-checks.
                                                _dec(None) without a default returns 0.0,
                                                which breaks the fallback chain silently.
  created_at / created_date      created_at
  updated_at / updated_date      updated_at
  category / sp_categories       category_name  Prefer sp_categories JOIN lookup by category_id;
    lookup                                      fall back to inline p.get("category") string
                                                if no category_id (no-variant products)

── /receipts → sp_receipts ──────────────────────────────────────────────────
  API field               DB field           Notes
  receipt_number          id + receipt_number  IS the PK — API has no separate "id" field
  receipt_type            receipt_type        Defaults to "SALE" if absent
  receipt_date_time       created_at          Also used for updated_at (no separate updated field)
  total_money             total_money
  total_discount          total_discount      Use decimal field — ignore total_discounts[] array
  total_tax               total_tax
  note                    note
  receipt_delete_status   status              false → "COMPLETED", true → "VOID"
  shop_id                 shop_id
  shop_id → lookup        shop_name           Resolved from shop_map (synced before receipts)
  customer_id             customer_id
  customer_id → lookup    customer_name       Resolved from cust_map (synced before receipts)
  payments[0].payment_type_id   payment_type_id
  payment_type_id → lookup      payment_type_name  Resolved from pay_map
  payments[0].money_amount      payment_amount     Falls back to total_money if absent
  (not stored)            refund_for          Available in API — relevant for refunds
  (not stored)            employee_id         Available in API but omitted
  (not stored)            order_type          Available in API but omitted
  (hardcoded 0)           points_earned       API doesn't return at receipt level
  (hardcoded 0)           points_deducted     API doesn't return at receipt level

── /receipts[].line_products → sp_receipt_line_items ────────────────────────
  API field             DB field           Notes
  product_line_no       id                 No "id" field on line items; product_line_no is PK
  product_id            product_id
  product_name / name   product_name
  product_code / sku    sku
  quantity              quantity
  price                 price              Selling price at time of sale (always correct even
                                           if sp_products.price was stale/zero)
  gross_total_money     gross_total_money  Pre-discount line total; falls back to price×qty
  total_discount        total_discount     Tries discount_amount, product_discount, total_discount
  total_money           total_money        Post-discount line total; falls back to gross_total_money
  cost                  cost               Tries product_cost, then cost
  category_name         category_name      Uses inline API value if present and not "No category";
                                           falls back to sp_products lookup by product_id
  cost_total            (not stored)       Total cost for line (cost × qty) — omitted
  variant_id            variant_id         API doesn't return at line level; always NULL
  (receipt field)       created_at         Denormalized from receipt.receipt_date_time
"""

import requests
import time
import os
import urllib3
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from logger import get_logger
from providers.upsert import upsert_record, upsert_to_shared, lookup_map

log = get_logger(__name__)

BASE_URL        = os.getenv("SALESPLAY_BASE_URL", "https://api.salesplaypos.com/v1.0")
PAGE_SIZE       = int(os.getenv("SALESPLAY_PAGE_SIZE", "250"))
RATE_SLEEP      = float(os.getenv("SALESPLAY_RATE_SLEEP", "1.1"))
DEFAULT_DAYS    = int(os.getenv("SALESPLAY_DEFAULT_LOOKBACK_DAYS", "90"))
_HTTP_TIMEOUT   = int(os.getenv("SALESPLAY_HTTP_TIMEOUT", "30"))
_RETRY_ATTEMPTS = int(os.getenv("SALESPLAY_RETRY_ATTEMPTS", "3"))
_VERIFY_SSL     = os.getenv("SALESPLAY_VERIFY_SSL", "false").lower() not in ("false", "0", "no")
DT_FMT          = "%Y-%m-%d %H:%M:%S"

if not _VERIFY_SSL:
    # Suppress urllib3 warnings only when SSL verification is intentionally disabled
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


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
        self.session.verify = _VERIFY_SSL

    def _get(self, endpoint: str, body: dict = None) -> dict:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        request_body = body or {}

        log.debug("SalesPlay API Request", url=url, method="GET",
                  headers=dict(self.session.headers), body=request_body)

        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = self.session.get(url, json=request_body, timeout=_HTTP_TIMEOUT, verify=_VERIFY_SSL)
            except requests.exceptions.ConnectionError as exc:
                log.error("Connection error", error=str(exc), attempt=attempt, url=url)
                time.sleep(3 * (attempt + 1))
                continue
            except requests.exceptions.Timeout:
                log.error("Timeout", url=url, attempt=attempt)
                time.sleep(3)
                continue

            log.debug("SalesPlay API Response",
                      url=url, status=resp.status_code,
                      response_headers=dict(resp.headers),
                      raw_body=resp.text[:5000])

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                log.warning("Rate limited", wait_seconds=wait)
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                log.error("SalesPlay 401 Unauthorized", url=url, raw_body=resp.text[:500])
                raise Exception("SalesPlay API token is invalid or expired.")

            if resp.status_code == 403:
                log.error("SalesPlay 403 Forbidden", url=url, raw_body=resp.text[:500])
                raise Exception("SalesPlay API: access forbidden.")

            if not resp.ok:
                preview = resp.text[:300] if resp.text else "(empty)"
                log.error("SalesPlay API error", url=url, status=resp.status_code,
                          raw_body=resp.text[:1000])
                raise Exception(f"SalesPlay API HTTP {resp.status_code}: {preview}")

            try:
                parsed = resp.json()
                log.debug("SalesPlay API parsed response",
                          url=url, top_level_keys=list(parsed.keys()) if isinstance(parsed, dict) else type(parsed).__name__,
                          record_counts={k: len(v) for k, v in parsed.items() if isinstance(v, list)} if isinstance(parsed, dict) else {})
                return parsed
            except ValueError:
                log.error("SalesPlay non-JSON response", url=url, raw=resp.text[:500])
                raise Exception(f"SalesPlay returned non-JSON: {resp.text[:200]}")

        raise Exception(f"SalesPlay API: failed after {_RETRY_ATTEMPTS} retries.")

    def _paginate(self, endpoint: str, key: str, body: dict) -> List[Dict]:
        """
        Cursor-based pagination with duplicate detection.
        SalesPlay API bug: sometimes returns same data with different cursors.
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

            if len(new_items) == 0 and len(items) > 0:
                duplicate_pages += 1
                if duplicate_pages >= 3:
                    log.warning("Stopping — API returning duplicates",
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
        data = self._get("/merchant", {})
        # API wraps the merchant object in a list: {"merchant": [{...}]}
        merchant = (data.get("merchant") or [{}])[0]
        currency_raw = merchant.get("currency") or {}
        currency = currency_raw.get("code", "") if isinstance(currency_raw, dict) else str(currency_raw)
        return {
            "business_name": (
                merchant.get("merchant_name") or merchant.get("name") or
                merchant.get("business_name") or "SalesPlay Account"
            ),
            "shop_count":  merchant.get("shop_count", 0),
            "currency":    currency,
            "merchant_id": merchant.get("id", ""),
        }


# ── Value helpers ─────────────────────────────────────────────────────────────

def _default_since(days: int = DEFAULT_DAYS) -> datetime:
    return datetime.now() - timedelta(days=days)


def _dt(val: Any) -> Optional[str]:
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


# ── Sync functions ────────────────────────────────────────────────────────────
# Each function now takes (client, conn, prefix, user_email, since, budget)
# and writes to integration_records via upsert_record().

def sync_shops(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
               since: Optional[datetime] = None, budget=None) -> int:
    """GET /shops — uses updated_at_min (not created_at_min)"""
    body: Dict = {}
    if since:
        body["updated_at_min"] = since.strftime(DT_FMT)

    data  = client._get("/shops", body)
    shops = data.get("shops", [])

    count = 0
    for s in shops:
        sid = _str(s.get("id"), 64)
        if not sid:
            continue
        record = {
            "id":         sid,
            "shop_name":  _str(s.get("shop_name"), 255),
            "address":    _str(s.get("address"), 500),
            "phone":      _str(s.get("phone_number"), 50),
            "email":      _str(s.get("email"), 255),
            "status":     _str(s.get("is_enable"), 20),
            "updated_at": _dt(s.get("updated_date")),
        }
        if upsert_record(conn, prefix, user_email, "salesplay", "shop", record,
                         id_field="id", ext_updated_field="updated_at", budget=budget):
            count += 1
        upsert_to_shared(conn, "sp_shops", prefix, record)

    log.info("Synced shops", prefix=prefix, count=count)
    return count


def sync_categories(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                    since: Optional[datetime] = None, budget=None) -> int:
    """GET /category"""
    body: Dict = {"category_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)

    items = client._paginate("/category", "categories", body)

    count = 0
    for c in items:
        cid = _str(c.get("id"), 64)
        if not cid:
            continue
        record = {
            "id":            cid,
            "category_name": _str(c.get("category_name"), 255),
            "created_at":    _dt(c.get("created_at")),
            "updated_at":    _dt(c.get("updated_date")),
        }
        if upsert_record(conn, prefix, user_email, "salesplay", "category", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
        upsert_to_shared(conn, "sp_categories", prefix, record)
        # Back-fill category_name into sp_products so JOIN queries don't need it
        if record.get("category_name") and record.get("id"):
            _cur = conn.cursor()
            _cur.execute(
                "UPDATE sp_products SET category_name=%s WHERE tenant_id=%s AND category_id=%s",
                (record["category_name"], prefix, record["id"]),
            )
            _cur.close()

    log.info("Synced categories", prefix=prefix, count=count)
    return count


def sync_sub_categories(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                        since: Optional[datetime] = None) -> int:
    """GET /sub_category — no dedicated record_type, skip gracefully."""
    body: Dict = {"sub_category_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
    try:
        items = client._paginate("/sub_category", "sub_categories", body)
        log.info("Sub-categories fetched (skipped — no record_type)",
                 prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Sub-categories sync skipped", error=str(exc))
    return 0


def sync_measurements(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                      since: Optional[datetime] = None) -> int:
    """GET /measurements — no dedicated record_type, skip gracefully."""
    body: Dict = {"measurement_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
    try:
        items = client._paginate("/measurements", "measurements", body)
        log.info("Measurements fetched (skipped)", prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Measurements sync skipped", error=str(exc))
    return 0


def sync_suppliers(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                   since: Optional[datetime] = None) -> int:
    """GET /suppliers — no dedicated record_type, skip gracefully."""
    body: Dict = {"supplier_ids": ""}
    if since:
        body["updated_at_min"] = since.strftime(DT_FMT)
    try:
        items = client._paginate("/suppliers", "suppliers", body)
        log.info("Suppliers fetched (skipped)", prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Suppliers sync skipped", error=str(exc))
    return 0


def sync_taxes(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
               since: Optional[datetime] = None) -> int:
    """GET /taxes — no dedicated record_type, skip gracefully."""
    body: Dict = {"tax_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
    try:
        items = client._paginate("/taxes", "taxes", body)
        log.info("Taxes fetched (skipped)", prefix=prefix, count=len(items))
    except Exception as exc:
        log.warning("Taxes sync skipped", error=str(exc))
    return 0


def sync_payment_types(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                       since: Optional[datetime] = None, budget=None) -> int:
    """GET /payment_types"""
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
        # A payment type is considered active if ANY shop has it enabled.
        shops = pt.get("shops") or []
        is_active = 1 if any(s.get("payment_type_status") == 1 for s in shops) else 0

        record = {
            "id":           ptid,
            "payment_name": _str(pt.get("payment_type_name"), 255),
            "payment_type": _str(pt.get("payment_type_code"), 50),
            "is_active":    is_active,
            "created_at":   _dt(pt.get("created_date")),
            "updated_at":   _dt(pt.get("updated_date")),
        }
        if upsert_record(conn, prefix, user_email, "salesplay", "payment_type", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
        upsert_to_shared(conn, "sp_payment_types", prefix, record)

    log.info("Synced payment_types", prefix=prefix, count=count)
    return count


def sync_customers(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                   since: Optional[datetime] = None, budget=None) -> int:
    """GET /customers"""
    if since is None:
        since = _default_since()

    body: Dict = {
        "customer_ids":   "",
        "email":          "",
        "created_at_min": since.strftime(DT_FMT),
        "updated_at_min": since.strftime(DT_FMT),
    }
    items = client._paginate("/customers", "customers", body)

    count = 0
    for c in items:
        cid = _str(c.get("id"), 64)
        if not cid:
            continue

        first = (_str(c.get("first_name")) or "").strip()
        last  = (_str(c.get("last_name"))  or "").strip()
        full  = f"{first} {last}".strip() or _str(c.get("name"), 255) or "Unknown"

        record = {
            "id":            cid,
            "customer_name": _str(full, 255),
            "email":         _str(c.get("email"), 255),
            "phone_number":  _str(c.get("phone") or c.get("phone_number"), 50),
            "customer_code": _str(c.get("customer_code"), 100),
            "note":          _str(c.get("description") or c.get("note")),
            "total_visits":  int(c.get("total_visits") or 0),
            "total_spent":   _dec(c.get("total_money_spent") or c.get("total_spent"), 0),
            "points_balance": _dec(c.get("points_balance") or c.get("total_points"), 0),
            "created_at":    _dt(c.get("created_at")),
            "updated_at":    _dt(c.get("updated_at") or c.get("updated_date")),
        }
        if upsert_record(conn, prefix, user_email, "salesplay", "customer", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
        upsert_to_shared(conn, "sp_customers", prefix, record)

    log.info("Synced customers", prefix=prefix, count=count)
    return count


def sync_products(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                  since: Optional[datetime] = None, budget=None) -> int:
    """GET /products — always fetch all; products are a small reference table needed
    for analytics JOINs regardless of the plan history window."""
    body: Dict = {"product_ids": ""}
    if since:
        body["created_at_min"] = since.strftime(DT_FMT)
    # No default date cutoff — fetch all products so analytics always have the full catalogue

    items = client._paginate("/products", "products", body)

    count = 0
    for p in items:
        pid = _str(p.get("id"), 64)
        if not pid:
            continue

        variants      = p.get("variants") or []
        first         = next((v for v in variants if not v.get("deleted_at")),
                             variants[0] if variants else {})
        variant_shops = first.get("shops") or []
        # Price fallback: variant shop → product-level shops → variant price → 0
        # Use None sentinel (not 0.0) so _dec(None, None) propagates correctly.
        price = _dec(variant_shops[0].get("price") if variant_shops else None, None)
        if price is None:
            top_shops = p.get("shops") or []
            price = _dec(top_shops[0].get("price") if top_shops else None, None)
        if price is None:
            price = _dec(first.get("price"), 0)

        record = {
            "id":           pid,
            "product_name": _str(p.get("product_name") or p.get("name"), 500),
            "description":  _str(p.get("description")),
            "category_id":  _str(p.get("category_id"), 64),
            "sku":          _str(first.get("product_code") or first.get("sku") or p.get("product_code"), 100),
            "barcode":      _str(first.get("product_barcode") or first.get("barcode"), 100),
            "cost":         _dec(first.get("default_cost") or first.get("cost") or p.get("cost"), 0),
            "price":        price or 0.0,
            "created_at":   _dt(p.get("created_at") or p.get("created_date")),
            "updated_at":   _dt(p.get("updated_at") or p.get("updated_date")),
        }
        # Enrich with category_name: prefer sp_categories lookup, fall back to inline name from API
        cat_id = record.get("category_id")
        if cat_id:
            _cur = conn.cursor()
            _cur.execute(
                "SELECT category_name FROM sp_categories WHERE tenant_id=%s AND id=%s",
                (prefix, cat_id),
            )
            cat_row = _cur.fetchone()
            _cur.close()
            record["category_name"] = cat_row[0] if cat_row else None
        else:
            cat_name = p.get("category")
            record["category_name"] = _str(cat_name, 255) if cat_name and cat_name != "No category" else None

        if upsert_record(conn, prefix, user_email, "salesplay", "product", record,
                         id_field="id", ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            count += 1
        upsert_to_shared(conn, "sp_products", prefix, record)

    log.info("Synced products", prefix=prefix, count=count)
    return count


def sync_receipts(client: SalesPlayAPIClient, conn, prefix: str, user_email: str,
                  since: Optional[datetime] = None, budget=None) -> int:
    """
    GET /receipts

    CRITICAL:
      - created_at_min + created_at_max REQUIRED — 401 without date range
      - receipt_number IS primary key (no separate id field)
      - total_discount = decimal (use this), total_discounts = array (ignore)
      - line_products[] is array key (NOT line_items)
      - receipt_delete_status bool → VOID / COMPLETED

    Uses lookup_map() to enrich receipt records with shop/customer/payment names.
    lookup_map() reads from integration_records (shops/customers/payment_types
    are already synced before receipts in the provider.sync() step order).
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
    log.info("Syncing receipts", created_at_min=since_str, created_at_max=now_str,
             prefix=prefix)

    # Look up names from already-synced records (shops/customers/payment_types
    # are synced in earlier steps so integration_records already has them)
    shop_map = lookup_map(conn, prefix, "salesplay", "shop",         "id", "shop_name")
    cust_map = lookup_map(conn, prefix, "salesplay", "customer",     "id", "customer_name")
    pay_map  = lookup_map(conn, prefix, "salesplay", "payment_type", "id", "payment_name")

    receipts = client._paginate("/receipts", "receipts", body)

    receipt_count = 0
    line_count    = 0

    for r in receipts:
        if budget is not None and budget.exhausted:
            break

        r_id = _str(r.get("receipt_number"), 64)
        if not r_id:
            continue

        shop_id    = _str(r.get("shop_id"), 64)
        cust_id    = _str(r.get("customer_id"), 64) if r.get("customer_id") else None
        receipt_dt = _dt(r.get("receipt_date_time") or r.get("created_at"))

        total_money    = _dec(r.get("total_money"), 0)
        total_discount = _dec(r.get("total_discount"), 0)
        total_tax      = _dec(r.get("total_tax"), 0)

        payments   = r.get("payments") or []
        pay_id     = None
        pay_amount = total_money
        if payments:
            fp         = payments[0]
            pay_id     = _str(fp.get("payment_type_id"), 64)
            pay_amount = _dec(fp.get("money_amount") or fp.get("amount"), total_money)

        is_deleted = bool(r.get("receipt_delete_status"))

        receipt_record = {
            "id":                r_id,
            "receipt_number":    r_id,
            "shop_id":           shop_id,
            "shop_name":         shop_map.get(shop_id, ""),
            "customer_id":       cust_id,
            "customer_name":     cust_map.get(cust_id, "") if cust_id else None,
            "created_at":        receipt_dt,
            "updated_at":        receipt_dt,
            "total_money":       total_money,
            "total_discount":    total_discount,
            "total_tax":         total_tax,
            "points_earned":     0.0,
            "points_deducted":   0.0,
            "note":              _str(r.get("note")),
            "receipt_type":      _str(r.get("receipt_type"), 30) or "SALE",
            "status":            "VOID" if is_deleted else "COMPLETED",
            "payment_type_id":   pay_id,
            "payment_type_name": pay_map.get(pay_id, "") if pay_id else None,
            "payment_amount":    pay_amount,
        }
        if upsert_record(conn, prefix, user_email, "salesplay", "receipt",
                         receipt_record, id_field="id",
                         ext_created_field="created_at",
                         ext_updated_field="updated_at", budget=budget):
            receipt_count += 1
        upsert_to_shared(conn, "sp_receipts", prefix, receipt_record)

        # Line items ("line_products" per API docs — NOT "line_items")
        for idx, item in enumerate(r.get("line_products") or []):
            item_id = _str(
                item.get("id") or item.get("product_line_no") or f"{r_id}_{idx}", 128
            )
            prod_id    = _str(item.get("product_id"), 64)
            unit_price        = _dec(item.get("price") or item.get("product_unit_price"), 0)
            qty               = _dec(item.get("quantity") or item.get("product_qty") or
                                     item.get("qty"), 1)
            gross_total_money = _dec(item.get("gross_total_money"),
                                     (unit_price or 0) * (qty or 1))
            line_total        = _dec(item.get("total") or item.get("total_money"),
                                     gross_total_money)
            line_disc  = _dec(item.get("discount_amount") or item.get("product_discount") or
                              item.get("total_discount"), 0)

            li_record = {
                "id":                item_id,
                "receipt_id":        r_id,
                "product_id":        prod_id,
                "variant_id":        _str(item.get("variant_id"), 64),
                "product_name":      _str(item.get("product_name") or item.get("name"), 500),
                "sku":               _str(item.get("product_code") or item.get("sku"), 100),
                "quantity":          qty,
                "price":             unit_price,
                "gross_total_money": gross_total_money,
                "total_discount":    line_disc,
                "total_money":       line_total,
                "cost":             _dec(item.get("product_cost") or item.get("cost"), 0),
                "created_at":       receipt_dt,  # denormalized from receipt for date filtering
            }

            # category_name: use value from API line item directly; fall back to sp_products lookup
            api_cat = _str(item.get("category_name"), 255)
            if api_cat and api_cat != "No category":
                li_record["category_name"] = api_cat
            elif prod_id:
                _cur = conn.cursor()
                _cur.execute(
                    "SELECT category_name FROM sp_products WHERE tenant_id=%s AND id=%s",
                    (prefix, prod_id),
                )
                prod_row = _cur.fetchone()
                _cur.close()
                li_record["category_name"] = prod_row[0] if prod_row else None
            else:
                li_record["category_name"] = None

            # Line items don't consume budget separately — counted via receipt budget above
            upsert_record(conn, prefix, user_email, "salesplay", "receipt_line_item",
                          li_record, id_field="id", ext_created_field="created_at")
            upsert_to_shared(conn, "sp_receipt_line_items", prefix, li_record)
            line_count += 1

    log.info("Synced receipts", prefix=prefix,
             receipts=receipt_count, line_items=line_count,
             total_rows=receipt_count + line_count)
    return receipt_count + line_count


def refresh_customer_last_purchase(conn, prefix: str) -> int:
    """
    Compute last_purchase_date for each customer from sp_receipts and write it
    back to sp_customers. Called after sync_receipts so customer rows always
    have accurate recency without the LLM needing to JOIN receipts.
    Returns the number of sp_customers rows updated.
    """
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE sp_customers c
        INNER JOIN (
            SELECT customer_id, MAX(created_at) AS last_dt
            FROM   sp_receipts
            WHERE  tenant_id = %s
              AND  status    = 'COMPLETED'
              AND  customer_id IS NOT NULL
              AND  customer_id != ''
            GROUP BY customer_id
        ) r ON r.customer_id = c.id
        SET c.last_purchase_date = r.last_dt
        WHERE c.tenant_id = %s
    """, (prefix, prefix))
    updated = cursor.rowcount
    log.info("Refreshed last_purchase_date", prefix=prefix, updated=updated)
    return updated
