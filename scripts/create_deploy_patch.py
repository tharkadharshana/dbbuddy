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
Branch : feature/llm-profile-report-fixes
Date   : 2026-06-18

CHANGES IN THIS PATCH
─────────────────────

1. fix(history): Chat history now shows full rich content
   PROBLEM : Opening a past conversation showed only plain text — no charts,
             tables, or AI analysis.
   FIX     : ChatPage.jsx reconstructs `data` and `analysis` from the stored
             `data_snapshot` field on history load.
   FILES   : datamind/frontend/src/pages/ChatPage.jsx

2. feat(llm): LLM knows user profile (country, timezone) and mirrors input language
   PROBLEM : "in my country what is the best selling product" triggered a
             clarification ask ("which country?") because the classifier had
             no profile context. Report narrative also ignored currency/country.
             LLM always replied in English regardless of question language.
   FIX     : Country, timezone, and currency are extracted from settings.locale
             and injected into every LLM call — the query classifier, the SQL
             generator, AND the report narrative generator. The classifier now
             treats "my country" as the user's profile country without asking.
             Both classifier and SQL prompts instruct the LLM to respond in the
             same language the user wrote in.
   FILES   : datamind/backend/main.py, datamind/backend/llm.py
   DB/ENV  : No changes — reads existing settings.locale fields.

3. fix(reports): Rate-limit now shows friendly countdown instead of empty report
   PROBLEM : Clicking Generate Report too quickly showed a blank report with
             no message (backend returns ok=false with HTTP 200).
   FIX     : ReportsPage detects ok=false, starts a 20-second cooldown timer,
             disables the Generate button, and shows an orange warning with
             live countdown. Clicking during cooldown is a no-op.
   FILES   : datamind/frontend/src/pages/ReportsPage.jsx

4. test(qa): QA suite split into targeted sub-suites
   PROBLEM : Running the full QA (~20 min) was the only option, even for
             small focused changes.
   FIX     : qa_e2e.py now accepts a suite argument:
             python qa_e2e.py chat     — /query tests only (~4 min)
             python qa_e2e.py data     — data/product/customer queries
             python qa_e2e.py convo    — conversation chain tests
             python qa_e2e.py harmful  — harmful SQL refusal
             python qa_e2e.py reports  — analytics report templates
             python qa_e2e.py          — full run (PR mode)
   FILES   : qa_e2e.py

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
