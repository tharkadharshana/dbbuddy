#!/usr/bin/env python3
"""
Build a deployable patch zip containing:
  <patch_name>/frontend/   - production frontend build (vite build)
  <patch_name>/backend/    - backend source files (no docs/data/logs/__pycache__)

Usage:
  python scripts/create_deploy_patch.py <patch-name>
  python scripts/create_deploy_patch.py            # defaults to datamind-deploy-YYYY-MM-DD

Output: archives/<patch-name>.zip
"""
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "datamind" / "frontend"
BACKEND_DIR = ROOT / "datamind" / "backend"
ARCHIVES_DIR = ROOT / "archives"

# Backend top-level files to include in the patch.
BACKEND_FILES = [
    "analytics.py", "auth.py", "billing.py", "cache.py", "conversations.py",
    "credits.py", "db.py", "Dockerfile", "embed.py", "integrations.py",
    "limiter.py", "llm.py", "logger.py", "main.py", "pool.py",
    "requirements.txt", "schema_builder.py", "start.py", "v1.py", ".env.example",
]

# Backend directories to include (copied recursively, minus EXCLUDE_NAMES).
BACKEND_DIRS = ["providers", "scripts"]

# Files/dirs to skip wherever they're found.
EXCLUDE_NAMES = {"__pycache__", ".env", "data", "logs", "dist", "scratch"}

# Files copied from the frontend build output that shouldn't ship to prod.
FRONTEND_EXCLUDE_FILES = {"iframe_test.html"}

# ── Patch notes ───────────────────────────────────────────────────────────────
# Update this block whenever a new patch is built.
PATCH_NOTES = """
PATCH NOTES
===========
Branch : fix/analytics-hub-ux
PR     : #52
Date   : 2026-06-19

CHANGES IN THIS PATCH
─────────────────────

1. fix(analytics): correct product & category revenue to use net sales
   PROBLEM : top_products and category_performance summed total_money from
             sp_receipt_line_items, which includes added taxes — causing
             DataMind totals to exceed SalesPlay's reported net sales.
   FIX     : Queries now use gross_total_money - total_discount for net_sales
             and expose gross_sales, discounts, net_sales as separate columns.
   FILES   : datamind/backend/providers/salesplay/analytics.py

2. feat(analytics): rename total_orders → total_receipts in Customer Purchase Analysis
   PROBLEM : Column was labelled "total_orders" but receipts are the correct term.
             Also removed days_since_last_purchase (unreliable metric).
   FIX     : Column renamed to total_receipts; days_since_last_purchase removed.
   FILES   : datamind/backend/providers/salesplay/analytics.py

3. feat(analytics): remove Sales by Hour of Day report
   PROBLEM : Hourly performance template was low-value and cluttered the hub.
   FIX     : hourly_performance template and its UI render block removed.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/main.py
             datamind/frontend/src/pages/DiscoverPage.jsx

4. feat(reports-ui): hide currency symbol in Analytics Hub tables
   PROBLEM : Single-currency tenants don't need the LKR/$ prefix on every
             monetary value — it adds noise without information.
   FIX     : formatCurrency() call commented out; formatNumber(v,null,2) used
             instead. Chat interface untouched.
   FILES   : datamind/frontend/src/components/UI.jsx

5. fix(locale): enforce 2 decimal places on all monetary columns
   PROBLEM : 'discounts' column showed no decimals (e.g. "0" not "0.00").
             KPI cards in Analytics Hub and Reports showed 0dp or 1dp on
             monetary values.
   FIX     : Added 'discount' to isCurrencyColumn() regex; KPI decimal logic
             updated in DiscoverPage and ReportsPage to use isCurrencyColumn.
   FILES   : datamind/frontend/src/utils/locale.js
             datamind/frontend/src/pages/DiscoverPage.jsx
             datamind/frontend/src/pages/ReportsPage.jsx

6. fix(discover-ui): Daily Revenue Trend ascending date order
   PROBLEM : Chart reversed the SQL result so dates appeared descending.
   FIX     : Removed .reverse() — SQL already returns ORDER BY date ASC.
   FILES   : datamind/frontend/src/pages/DiscoverPage.jsx

7. feat(analytics): add hoverable info icon for split-payment note
   PROBLEM : DataMind Cash/Card breakdown can differ from SalesPlay by a fixed
             amount for tenants using split-method receipts — sync stores only
             payments[0] and attributes the full receipt total to it.
   FIX     : payment_breakdown template defines a "note" field; a small ⓘ icon
             appears next to the report title on hover showing the explanation.
             Icon is data-driven — only renders when template defines "note";
             no provider-specific code in the frontend.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/main.py
             datamind/frontend/src/pages/DiscoverPage.jsx
             datamind/frontend/src/index.css

8. fix(table): right-align numeric column headers to match cell alignment
   PROBLEM : Numeric column headers (e.g. UNITS SOLD) were left-aligned while
             cell values were right-aligned, making values appear visually
             shifted between columns.
   FIX     : DataTable now detects numeric columns from first row and applies
             text-align:right to both headers and cells.
   FILES   : datamind/frontend/src/components/UI.jsx

9. fix(analytics): rename 'Uncategorized' to 'No Category'
   PROBLEM : Products with no category were labelled "Uncategorized" in all
             reports — replaced with cleaner "No Category" label.
   FIX     : Updated COALESCE fallback in SalesPlay and Loyverse queries.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/providers/loyverse/analytics.py

10. fix(discover): Refresh button overhaul — sync + re-run combined with
    visual progress feedback
    PROBLEM : (a) connection_id missing on catalogue fast-path so sync never
              fired. (b) Clicking a report card triggered a full sync.
              (c) Sync had no visual feedback in the hub.
    FIX     : Fast-path now merges live connection data onto catalogue items.
              Card click runs query only; Refresh button syncs (with spinner
              + blue progress banner) then loads the report. Rate-limit
              countdown shown on button when backend throttles.
    FILES   : datamind/frontend/src/pages/DiscoverPage.jsx

DB CHANGES  : None
.ENV CHANGES: None
"""


def ignore_excluded(_dir, names):
    return [n for n in names if n in EXCLUDE_NAMES or n.endswith(".pyc")]


def build_frontend():
    print("==> Building frontend (npm run build)...")
    dist_dir = FRONTEND_DIR / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True, shell=True)
    return dist_dir


def copy_frontend(dist_dir, dest):
    print("==> Copying frontend build...")
    shutil.copytree(dist_dir, dest, ignore=ignore_excluded)
    for name in FRONTEND_EXCLUDE_FILES:
        f = dest / name
        if f.exists():
            f.unlink()


def copy_backend(dest):
    print("==> Copying backend source...")
    dest.mkdir(parents=True, exist_ok=True)
    for name in BACKEND_FILES:
        src = BACKEND_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
        else:
            print(f"    (skip, not found) {name}")
    for name in BACKEND_DIRS:
        src = BACKEND_DIR / name
        if src.exists():
            shutil.copytree(src, dest / name, ignore=ignore_excluded)


def write_patch_notes(dest_dir: Path):
    print("==> Writing PATCH_NOTES.txt...")
    (dest_dir / "PATCH_NOTES.txt").write_text(PATCH_NOTES.strip(), encoding="utf-8")


def main():
    patch_name = sys.argv[1] if len(sys.argv) > 1 else f"datamind-deploy-{date.today().isoformat()}"
    patch_dir = ARCHIVES_DIR / patch_name

    if patch_dir.exists():
        shutil.rmtree(patch_dir)

    dist_dir = build_frontend()
    copy_frontend(dist_dir, patch_dir / "frontend")
    copy_backend(patch_dir / "backend")
    write_patch_notes(patch_dir)

    print("==> Zipping...")
    zip_base = ARCHIVES_DIR / patch_name
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=ARCHIVES_DIR, base_dir=patch_name)
    shutil.rmtree(patch_dir)

    print(f"\nDone: {zip_path}")


if __name__ == "__main__":
    main()
