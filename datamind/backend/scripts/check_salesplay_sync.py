"""
check_salesplay_sync.py
=======================
Diagnostic tool: hit SalesPlay API directly with an external token, print raw counts
per endpoint, then query our DB tables and compare what we actually synced.

Usage (from datamind/backend/):
  python scripts/check_salesplay_sync.py --token "eyJ..." [--prefix sp_<your_prefix>]

If --prefix is omitted the script lists all tenant prefixes found in sp_receipts
and lets you pick one, or uses the first one found.
"""

import argparse
import os
import sys
import json
import time
import io
from datetime import datetime, timedelta

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Force UTF-8 output on Windows so special chars don't crash
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Load .env so DB creds are available
from pathlib import Path
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

BASE_URL  = os.getenv("SALESPLAY_BASE_URL", "https://api.salesplaypos.com/v1.0")
PAGE_SIZE = 250
DT_FMT    = "%Y-%m-%d %H:%M:%S"
SEP       = "-" * 60


# ── Colours ───────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
GREY   = "\033[90m"

def ok(s):    return f"{GREEN}{s}{RESET}"
def warn(s):  return f"{YELLOW}{s}{RESET}"
def err(s):   return f"{RED}{s}{RESET}"
def hdr(s):   return f"{BOLD}{CYAN}{s}{RESET}"
def grey(s):  return f"{GREY}{s}{RESET}"


# ── Minimal API client (mirrors sync.py logic) ────────────────────────────────

class RawSalesPlayClient:
    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers.update({
            "Token":        f"Bearer {token}",
            "Content-Type": "application/json",
        })
        self.session.verify = False

    def get_raw(self, endpoint: str, body: dict = None) -> tuple:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}"
        body = body or {}
        print(f"\n  {GREY}-> GET {url}{RESET}")
        print(f"     {GREY}body: {json.dumps(body)}{RESET}")
        try:
            resp = self.session.get(url, json=body, timeout=30, verify=False)
        except Exception as e:
            print(f"     {RED}Exception: {e}{RESET}")
            return -1, str(e)
        print(f"     {GREY}status: {resp.status_code}{RESET}")
        print(f"     {GREY}response headers: {dict(resp.headers)}{RESET}")
        print(f"     {GREY}raw body (first 3000 chars):{RESET}")
        print(f"     {GREY}{resp.text[:3000]}{RESET}")
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, resp.text

    def paginate(self, endpoint: str, key: str, body: dict) -> list:
        results = []
        seen = set()
        b = dict(body)
        b["limit"] = PAGE_SIZE
        prev_cursor = None
        page = 0
        duplicate_pages = 0

        while True:
            page += 1
            status, data = self.get_raw(endpoint, b)
            if status != 200 or not isinstance(data, dict):
                print(f"     {RED}Non-200 or non-JSON, stopping pagination{RESET}")
                break

            items = data.get(key, [])
            new_items = []
            for item in items:
                item_id = item.get("id") or item.get("receipt_number")
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    new_items.append(item)

            results.extend(new_items)
            print(f"     {GREY}Page {page}: {len(items)} items, {len(new_items)} new, total unique: {len(results)}{RESET}")

            if len(new_items) == 0 and len(items) > 0:
                duplicate_pages += 1
                if duplicate_pages >= 3:
                    print(f"     {YELLOW}3 duplicate pages in a row — stopping{RESET}")
                    break

            new_cursor = data.get("cursor")
            if not new_cursor or not items or new_cursor == prev_cursor:
                break
            prev_cursor = new_cursor
            b["cursor"] = new_cursor
            time.sleep(1.1)

        return results


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_db_conn():
    import mysql.connector
    return mysql.connector.connect(
        host=os.getenv("DATAMIND_DB_HOST", "localhost"),
        port=int(os.getenv("DATAMIND_DB_PORT", "3306")),
        database=os.getenv("DATAMIND_DB_NAME", "datamind_db"),
        user=os.getenv("DATAMIND_DB_USER", "datamind_user"),
        password=os.getenv("DATAMIND_DB_PASSWORD", "datamind_pass"),
        connect_timeout=10,
    )


def db_count(conn, table: str, prefix: str):
    cur = conn.cursor()
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s", (prefix,))
        return cur.fetchone()[0]
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        cur.close()


def db_sample(conn, table: str, prefix: str, n: int = 3) -> list:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(f"SELECT * FROM {table} WHERE tenant_id = %s LIMIT %s", (prefix, n))
        return cur.fetchall()
    except Exception:
        return []
    finally:
        cur.close()


def list_prefixes(conn) -> list:
    cur = conn.cursor()
    prefixes = set()
    for tbl in ("sp_receipts", "sp_customers", "sp_products", "sp_shops"):
        try:
            cur.execute(f"SELECT DISTINCT tenant_id FROM {tbl} LIMIT 50")
            for row in cur.fetchall():
                prefixes.add(row[0])
        except Exception:
            pass
    cur.close()
    return sorted(prefixes)


def _compare(db_n, api_n) -> str:
    if isinstance(db_n, str) or isinstance(api_n, str):
        return warn("cannot compare")
    if db_n == 0 and api_n == 0:
        return grey("both empty")
    if db_n == 0 and api_n > 0:
        return err("NOT SYNCED")
    if db_n < api_n:
        pct = int(100 * db_n / api_n)
        return warn(f"partial ({pct}%)")
    if db_n >= api_n:
        return ok("OK (synced)")
    return warn("mismatch")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="SalesPlay API + sync cross-check")
    parser.add_argument("--token", required=True, help="SalesPlay external API token")
    parser.add_argument("--prefix", default=None, help="Tenant prefix in DB (e.g. sp_abc123)")
    parser.add_argument("--days", type=int, default=90, help="Lookback days for date-filtered endpoints")
    parser.add_argument("--skip-api", action="store_true", help="Skip live API calls, only check DB")
    args = parser.parse_args()

    since = (datetime.now() - timedelta(days=args.days)).strftime(DT_FMT)
    now   = datetime.now().strftime(DT_FMT)

    client = RawSalesPlayClient(args.token)

    # ── 1. Validate token ─────────────────────────────────────────────────────
    print(hdr(f"\n{SEP}"))
    print(hdr("  STEP 1: Validate token -> GET /merchant"))
    print(hdr(f"  BASE_URL = {BASE_URL}"))
    print(hdr(SEP))

    status, merchant = client.get_raw("/merchant", {})
    if status != 200:
        print(err(f"\n  Token validation FAILED (HTTP {status})."))
        print(err(f"  Check token and BASE_URL = {BASE_URL}"))
        sys.exit(1)
    print(ok("\n  Token valid! Merchant info:"))
    print(json.dumps(merchant, indent=4, default=str))

    api_results = {}

    if not args.skip_api:
        # ── 2. Shops ──────────────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr("  STEP 2: Fetch shops -> GET /shops"))
        print(hdr(SEP))
        status, shops_data = client.get_raw("/shops", {})
        shops = shops_data.get("shops", []) if isinstance(shops_data, dict) else []
        api_results["shops"] = shops
        print(ok(f"\n  API returned {len(shops)} shops"))
        if shops:
            print("  First 2 shops:")
            print(json.dumps(shops[:2], indent=4, default=str))

        # ── 3. Categories ─────────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr("  STEP 3: Fetch categories -> GET /category (paginated)"))
        print(hdr(SEP))
        categories = client.paginate("/category", "categories",
                                     {"category_ids": "", "created_at_min": since})
        api_results["categories"] = categories
        print(ok(f"\n  API returned {len(categories)} categories total"))
        if categories:
            print("  First 2 categories:")
            print(json.dumps(categories[:2], indent=4, default=str))

        # ── 4. Payment types ──────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr("  STEP 4: Fetch payment types -> GET /payment_types"))
        print(hdr(SEP))
        payment_types = client.paginate("/payment_types", "payment_types",
                                        {"payment_type_ids": "", "created_at_min": since})
        api_results["payment_types"] = payment_types
        print(ok(f"\n  API returned {len(payment_types)} payment types total"))
        if payment_types:
            print("  First 2 payment types:")
            print(json.dumps(payment_types[:2], indent=4, default=str))

        # ── 5. Customers ──────────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr(f"  STEP 5: Fetch customers -> GET /customers (since {since})"))
        print(hdr(SEP))
        customers = client.paginate("/customers", "customers",
                                    {"customer_ids": "", "email": "", "created_at_min": since})
        api_results["customers"] = customers
        print(ok(f"\n  API returned {len(customers)} customers total"))
        if customers:
            print("  First 2 customers:")
            print(json.dumps(customers[:2], indent=4, default=str))

        # ── 6. Products ───────────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr(f"  STEP 6: Fetch products -> GET /products (since {since})"))
        print(hdr(SEP))
        products = client.paginate("/products", "products",
                                   {"product_ids": "", "created_at_min": since})
        api_results["products"] = products
        print(ok(f"\n  API returned {len(products)} products total"))
        if products:
            print("  First 2 products:")
            print(json.dumps(products[:2], indent=4, default=str))

        # ── 7. Receipts ───────────────────────────────────────────────────────
        print(hdr(f"\n{SEP}"))
        print(hdr(f"  STEP 7: Fetch receipts -> GET /receipts (since {since})"))
        print(hdr(SEP))
        receipts = client.paginate("/receipts", "receipts", {
            "receipt_numbers": "", "shop_id": "",
            "created_at_min": since, "created_at_max": now,
        })
        api_results["receipts"] = receipts
        line_item_count = sum(len(r.get("line_products") or []) for r in receipts)
        print(ok(f"\n  API returned {len(receipts)} receipts, {line_item_count} line items total"))
        if receipts:
            print("  First receipt (full):")
            print(json.dumps(receipts[:1], indent=4, default=str))

    # ── 8. DB cross-check ─────────────────────────────────────────────────────
    print(hdr(f"\n{SEP}"))
    print(hdr("  STEP 8: Cross-check with our DB"))
    print(hdr(SEP))

    try:
        conn = get_db_conn()
    except Exception as e:
        print(err(f"\n  Could not connect to DB: {e}"))
        print(warn("  Skipping DB cross-check."))
        return

    prefixes = list_prefixes(conn)
    print(f"\n  Tenant prefixes found in DB: {prefixes or ['(none)']}")

    prefix = args.prefix
    if not prefix:
        if not prefixes:
            print(warn("  No synced data found in DB yet. Run a sync first."))
            conn.close()
            return
        prefix = prefixes[0]
        print(warn(f"  No --prefix specified, using first found: {prefix}"))

    print(f"\n  Checking prefix: {BOLD}{prefix}{RESET}")

    TABLE_MAP = {
        "sp_shops":              "shops",
        "sp_categories":         "categories",
        "sp_payment_types":      "payment_types",
        "sp_customers":          "customers",
        "sp_products":           "products",
        "sp_receipts":           "receipts",
        "sp_receipt_line_items": "_line_items",
    }

    print(f"\n  {'Table':<32} {'DB rows':>10}  {'API rows':>10}  Status")
    print(f"  {'-'*32} {'-'*10}  {'-'*10}  {'-'*20}")

    for table, api_key in TABLE_MAP.items():
        db_n = db_count(conn, table, prefix)
        if args.skip_api:
            api_n = "skipped"
            status_str = grey("api skipped")
        elif api_key == "_line_items":
            if "receipts" in api_results:
                api_n = sum(len(r.get("line_products") or []) for r in api_results["receipts"])
            else:
                api_n = "n/a"
            status_str = _compare(db_n, api_n)
        elif api_key in api_results:
            api_n = len(api_results[api_key])
            status_str = _compare(db_n, api_n)
        else:
            api_n = "n/a"
            status_str = grey("no api data")
        print(f"  {table:<32} {str(db_n):>10}  {str(api_n):>10}  {status_str}")

    # ── 9. Sample rows from DB ────────────────────────────────────────────────
    print(hdr(f"\n{SEP}"))
    print(hdr("  STEP 9: Sample rows from DB (up to 3 per table)"))
    print(hdr(SEP))

    for table in ("sp_shops", "sp_customers", "sp_receipts"):
        rows = db_sample(conn, table, prefix)
        print(f"\n  {BOLD}{table}{RESET}:")
        if rows:
            print(json.dumps(rows, indent=4, default=str))
        else:
            print(f"  {YELLOW}  (empty){RESET}")

    conn.close()

    print(hdr(f"\n{SEP}"))
    print(hdr("  Done."))
    print(hdr(SEP + "\n"))


if __name__ == "__main__":
    main()
