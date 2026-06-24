#!/usr/bin/env python3
"""
Build a deployable patch zip containing:
  <patch_name>/frontend/        - production frontend build (vite build)
  <patch_name>/backend/         - backend source files (no docs/data/logs/__pycache__)
  <patch_name>/manual_deploy/   - files git-ignored on prod (main.py, llm.py);
                                  copy these manually — do NOT overwrite blindly.

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
    "limiter.py", "logger.py", "pool.py",
    "requirements.txt", "schema_builder.py", "start.py", "v1.py", ".env.example",
]

# These files are .gitignored on the production server, so they must be applied
# manually by an admin. They are placed in manual_deploy/ inside the zip —
# NOT in backend/ — to make this explicit.
MANUAL_DEPLOY_FILES = ["main.py", "llm.py"]

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
Branch : feat/embed-appname-and-search-bar-polish
PR     : #55
Date   : 2026-06-24

MANUAL DEPLOY FILES
───────────────────
The following files are .gitignored on the production server and must be
applied manually from the manual_deploy/ folder. Do NOT overwrite blindly —
diff against the live server copy first.

  * manual_deploy/main.py
  * manual_deploy/llm.py

CHANGES IN THIS PATCH
─────────────────────

1. refactor(logo): consolidate brand mark into a single <Logo/> component
   PROBLEM : The brand mark was hand-copied inline in ~10 places across the
             app and embed widget, so there was no single place to change it.
   FIX     : Extracted one reusable <Logo/> component (single source of truth)
             referenced everywhere. The logo itself is unchanged — the original
             4-square brand mark. Future logo changes are now a one-file edit.
   FILES   : datamind/frontend/src/components/Logo.jsx  (new)
             datamind/frontend/src/components/Sidebar.jsx
             datamind/frontend/src/pages/AuthPage.jsx
             datamind/frontend/src/pages/OnboardingWizard.jsx
             datamind/frontend/src/pages/ChatPage.jsx
             datamind/frontend/src/embed/EmbedChat.jsx
             datamind/frontend/src/embed/EmbedOnboarding.jsx
             datamind/frontend/src/embed/EmbedSalesplayAutoInit.jsx
             datamind/frontend/public/favicon.svg

2. feat(app): default to light theme when no preference is set
   PROBLEM : New users landed in dark theme by default.
   FIX     : Theme initial state falls back to 'light' instead of 'dark'.
   FILES   : datamind/frontend/src/App.jsx

3. feat(connections): hide BYODB and Loyverse, show only Salesplay POS
   PROBLEM : Integrations page advertised Bring-Your-Own-Database and Loyverse,
             which aren't offered right now.
   FIX     : Commented out the BYODB card and filtered external integrations to
             Salesplay POS only, with a note that more are coming.
   FILES   : datamind/frontend/src/pages/ConnectionsPage.jsx

4. feat(sidebar): make Forecasting and Anomaly Alerts non-clickable
   PROBLEM : Forecasting/Anomaly Alerts navigated to unfinished views.
   FIX     : Both sub-items are now display-only; the Predictions group still
             expands/collapses.
   FILES   : datamind/frontend/src/components/Sidebar.jsx

5. feat(reports): remove "LLM" label from Report Builder
   PROBLEM : The usage section was labelled "LLM".
   FIX     : Renamed the label to "Usage".
   FILES   : datamind/frontend/src/pages/ReportsPage.jsx

6. feat(chat): hide Think Mode brain icon in Ask Your Data
   PROBLEM : The brain-icon Think Mode toggle was still visible.
   FIX     : Commented out the toggle button (state/rendering kept for future
             re-enable).
   FILES   : datamind/frontend/src/pages/ChatPage.jsx

7. feat(sidebar): in-app confirmation modal for deleting chats
   PROBLEM : Deleting a historical conversation used the browser
             window.confirm() dialog.
   FIX     : Replaced with a styled in-app confirmation modal.
   FILES   : datamind/frontend/src/components/Sidebar.jsx

8. style(embed): "AI can make mistakes." + bold/underlined Clear conversation
   PROBLEM : Disclaimer was verbose ("<APP> can make mistakes. Please verify
             important information.") and "Clear conversation" was low-emphasis.
   FIX     : Disclaimer simplified to "AI can make mistakes."; "Clear
             conversation" is now bold + underlined.
   FILES   : datamind/frontend/src/embed/EmbedChat.jsx

-- PREVIOUS PATCH (PR #55 · embed branding · 2026-06-23) --------------------

9. feat(embed): source widget app name from backend APP_NAME
   PROBLEM : Embed onboarding/chat hardcoded "DataMind" (and "datamind.ai")
             in user-visible copy, ignoring APP_NAME in backend/.env. New
             SalesPlay webembed users saw "Create your DataMind account" etc.
   FIX     : /embed/context now returns app_name from APP_NAME. New frontend
             helper embedBranding.js resolves the name at runtime
             (context.app_name -> VITE_APP_NAME -> fallback); all embed name
             literals use it. Real URLs (terms/privacy, VITE_APP_URL) kept.
   FILES   : datamind/backend/embed.py
             datamind/frontend/src/embed/embedBranding.js
             datamind/frontend/src/embed/EmbedApp.jsx
             datamind/frontend/src/embed/EmbedOnboarding.jsx
             datamind/frontend/src/embed/EmbedSalesplayAutoInit.jsx
             datamind/frontend/src/embed/EmbedChat.jsx

2. feat(embed): reword input placeholder to "Ask AI about your data…"
   PROBLEM : Placeholder "Ask about your data…" didn't signal an AI assistant.
   FIX     : Updated to "Ask AI about your data…" in the collapsed search bar
             and both EmbedChat input variants.
   FILES   : datamind/frontend/src/embed/EmbedSearchBar.jsx
             datamind/frontend/src/embed/EmbedChat.jsx

3. feat(embed): use AI sparkle icon instead of chat bubble in input
   PROBLEM : The input led with a generic chat-bubble icon (collapsed search
             bar and expanded chat input), reading as plain messaging.
   FIX     : Replaced with an AI sparkle icon in both surfaces; send arrow
             button unchanged.
   FILES   : datamind/frontend/src/embed/EmbedSearchBar.jsx
             datamind/frontend/src/embed/EmbedChat.jsx

-- PREVIOUS PATCH (PR #53 · chore/double-token-quotas · 2026-06-23) ---------

4. chore(billing): double token quotas for all plans during beta
   PROBLEM : Token limits were too low for normal beta usage.
   FIX     : All plan token limits doubled in billing.py seed defaults.
   FILES   : datamind/backend/billing.py

2. fix(embed): make salesplay webembed mobile responsive
   PROBLEM : Iframe expand size was hardcoded at 420px (overflows on phones);
             header buttons and labels overflowed on narrow screens (<370px).
   FIX     : getExpandedSize() caps width at device screen width; EmbedChat
             tracks viewport width and applies isNarrow layout adjustments —
             icon-only buttons, smaller title font, tighter padding.
   FILES   : datamind/frontend/src/embed/EmbedApp.jsx
             datamind/frontend/src/embed/EmbedChat.jsx

3. fix(analytics): change Daily Revenue Trend to monthly and fix avg_ticket KPI math
   PROBLEM : revenue_trend template grouped by day (90-day window). KPI card
             avg_ticket was computed as AVG of daily averages — wrong when
             transaction counts differ across rows (e.g. 46950/25 ≠ 1905.54).
   FIX     : Template now groups by month over last N months (plan-dependent).
             avg_ticket fixed to SUM(total_money)/COUNT(*) at SQL level and
             in the KPI card weighted-average logic.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/frontend/src/pages/DiscoverPage.jsx

4. feat(analytics): show daily data table alongside monthly revenue chart
   PROBLEM : Monthly grouping lost the per-day detail in the data table.
   FIX     : Added table_sql to revenue_trend template (daily rows, same
             history window). Backend runs both queries and returns
             table_data/table_columns separately. Frontend uses table_data
             for the DataTable when present.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/frontend/src/pages/DiscoverPage.jsx

5. fix(analytics): scope revenue trend window to user plan history limit
   PROBLEM : Monthly chart and daily table always used a hardcoded window
             regardless of the user's plan.
   FIX     : history_months from get_plan_history_limit() is passed into
             run_salesplay_analytics() and substituted into both sql and
             table_sql. Cache key now includes history_months so a plan
             upgrade serves fresh data immediately.
             Starter=1mo, Growth=3mo, Pro=12mo
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/main.py   <- MANUAL DEPLOY

6. feat(embed): add close button to onboarding wizard
   PROBLEM : No way to dismiss the wizard without refreshing the page.
   FIX     : X button added to EmbedSalesplayAutoInit and EmbedOnboarding.
             Fires dm:close to parent and collapses bar layout if active.
             Hidden during active sync to prevent mid-flight abandonment.
   FILES   : datamind/frontend/src/embed/EmbedApp.jsx
             datamind/frontend/src/embed/EmbedOnboarding.jsx
             datamind/frontend/src/embed/EmbedSalesplayAutoInit.jsx

-- PREVIOUS PATCH (PR #52 · fix/analytics-hub-ux · 2026-06-19) --------------

7. fix(analytics): correct product & category revenue to use net sales
   PROBLEM : top_products and category_performance summed total_money from
             sp_receipt_line_items, which includes added taxes — causing
             DataMind totals to exceed SalesPlay's reported net sales.
   FIX     : Queries now use gross_total_money - total_discount for net_sales
             and expose gross_sales, discounts, net_sales as separate columns.
   FILES   : datamind/backend/providers/salesplay/analytics.py

8. feat(analytics): rename total_orders to total_receipts in Customer Purchase Analysis
   PROBLEM : Column was labelled "total_orders" but receipts are the correct term.
             Also removed days_since_last_purchase (unreliable metric).
   FIX     : Column renamed to total_receipts; days_since_last_purchase removed.
   FILES   : datamind/backend/providers/salesplay/analytics.py

9. feat(analytics): remove Sales by Hour of Day report
   PROBLEM : Hourly performance template was low-value and cluttered the hub.
   FIX     : hourly_performance template and its UI render block removed.
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/main.py
             datamind/frontend/src/pages/DiscoverPage.jsx

10. feat(reports-ui): hide currency symbol in Analytics Hub tables
    PROBLEM : Single-currency tenants don't need the LKR/$ prefix on every
              monetary value — it adds noise without information.
    FIX     : formatCurrency() call commented out; formatNumber(v,null,2) used
              instead. Chat interface untouched.
    FILES   : datamind/frontend/src/components/UI.jsx

11. fix(locale): enforce 2 decimal places on all monetary columns
    PROBLEM : 'discounts' column showed no decimals (e.g. "0" not "0.00").
              KPI cards in Analytics Hub and Reports showed 0dp or 1dp on
              monetary values.
    FIX     : Added 'discount' to isCurrencyColumn() regex; KPI decimal logic
              updated in DiscoverPage and ReportsPage to use isCurrencyColumn.
    FILES   : datamind/frontend/src/utils/locale.js
              datamind/frontend/src/pages/DiscoverPage.jsx
              datamind/frontend/src/pages/ReportsPage.jsx

12. fix(discover-ui): Daily Revenue Trend ascending date order
    PROBLEM : Chart reversed the SQL result so dates appeared descending.
    FIX     : Removed .reverse() — SQL already returns ORDER BY date ASC.
    FILES   : datamind/frontend/src/pages/DiscoverPage.jsx

13. feat(analytics): add hoverable info icon for split-payment note
    PROBLEM : DataMind Cash/Card breakdown can differ from SalesPlay by a fixed
              amount for tenants using split-method receipts — sync stores only
              payments[0] and attributes the full receipt total to it.
    FIX     : payment_breakdown template defines a "note" field; a small info icon
              appears next to the report title on hover showing the explanation.
              Icon is data-driven — only renders when template defines "note";
              no provider-specific code in the frontend.
    FILES   : datamind/backend/providers/salesplay/analytics.py
              datamind/backend/main.py
              datamind/frontend/src/pages/DiscoverPage.jsx
              datamind/frontend/src/index.css

14. fix(table): right-align numeric column headers to match cell alignment
    PROBLEM : Numeric column headers (e.g. UNITS SOLD) were left-aligned while
              cell values were right-aligned, making values appear visually
              shifted between columns.
    FIX     : DataTable now detects numeric columns from first row and applies
              text-align:right to both headers and cells.
    FILES   : datamind/frontend/src/components/UI.jsx

15. fix(analytics): rename 'Uncategorized' to 'No Category'
    PROBLEM : Products with no category were labelled "Uncategorized" in all
              reports — replaced with cleaner "No Category" label.
    FIX     : Updated COALESCE fallback in SalesPlay and Loyverse queries.
    FILES   : datamind/backend/providers/salesplay/analytics.py
              datamind/backend/providers/loyverse/analytics.py

16. fix(discover): Refresh button overhaul — sync + re-run combined with
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


def copy_manual_files(dest):
    """Copy git-ignored prod files to manual_deploy/ — must be applied by hand on the server."""
    print("==> Copying manual deploy files (main.py, llm.py)...")
    dest.mkdir(parents=True, exist_ok=True)
    found = []
    for name in MANUAL_DEPLOY_FILES:
        src = BACKEND_DIR / name
        if src.exists():
            shutil.copy2(src, dest / name)
            found.append(name)
        else:
            print(f"    (skip, not found) {name}")
    if found:
        readme = (
            "MANUAL DEPLOY REQUIRED\n"
            "======================\n"
            "These files are .gitignored on the production server.\n"
            "Do NOT copy them blindly — diff against the live file first,\n"
            "then apply only the sections that changed.\n\n"
            "Files in this folder:\n"
            + "".join(f"  * {n}\n" for n in found)
        )
        (dest / "README.txt").write_text(readme, encoding="utf-8")


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
    copy_manual_files(patch_dir / "manual_deploy")
    write_patch_notes(patch_dir)

    print("==> Zipping...")
    zip_base = ARCHIVES_DIR / patch_name
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=ARCHIVES_DIR, base_dir=patch_name)
    shutil.rmtree(patch_dir)

    print(f"\nDone: {zip_path}")
    print(f"\nWARNING: Remember to manually apply files in manual_deploy/ on the server.")


if __name__ == "__main__":
    main()
