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
    "requirements.txt", "schema_builder.py", "start.py", "v1.py",
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
Branch : chore/double-token-quotas
PR     : -
Date   : 2026-06-22

MANUAL DEPLOY FILES
───────────────────
The following files are .gitignored on the production server and must be
applied manually from the manual_deploy/ folder. Do NOT overwrite blindly —
diff against the live server copy first.

  • manual_deploy/main.py
  • manual_deploy/llm.py

CHANGES IN THIS PATCH
─────────────────────

1. chore(billing): double token quotas for all plans during beta
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
             Starter=1mo · Growth=3mo · Pro=12mo
   FILES   : datamind/backend/providers/salesplay/analytics.py
             datamind/backend/main.py   ← MANUAL DEPLOY

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
            + "".join(f"  • {n}\n" for n in found)
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
