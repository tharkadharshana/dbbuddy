#!/usr/bin/env python3
"""
Build a deployable patch zip containing:
  <patch_name>/frontend/        - production frontend build (vite build)
  <patch_name>/backend/         - backend source files (no docs/data/logs/__pycache__)
  <patch_name>/manual_deploy/   - files git-ignored on prod (main.py, llm.py);
                                  copy these manually — do NOT overwrite blindly.
  <patch_name>/PATCH_NOTES.txt  - scripts/PATCH_NOTES.md (hand-written PROBLEM/FIX
                                  prose) + an auto-generated "files changed since
                                  last patch" / commit log section.

Patch notes:
  Edit scripts/PATCH_NOTES.md by hand before running this script — describe
  each change as PROBLEM/FIX like before. Do NOT edit this script for notes.
  After a successful build, a lightweight git tag "patch-<patch-name>" is
  created so the next run's auto-generated section knows its baseline.

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
PATCH_NOTES_FILE = ROOT / "scripts" / "PATCH_NOTES.md"
PATCH_TAG_PREFIX = "patch-"

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


def copy_manual_files(dest, baseline):
    """Copy git-ignored prod files to manual_deploy/ — must be applied by hand on the server.

    Only includes files that actually changed since the last patch tag, so an
    admin doesn't get main.py/llm.py in every patch when only one of them (or
    neither) actually differs from what's already deployed.
    """
    print("==> Copying manual deploy files (only those changed since last patch)...")
    dest.mkdir(parents=True, exist_ok=True)
    changed = None
    if baseline is not None:
        try:
            changed = set(_git("diff", "--name-only", f"{baseline}..HEAD").splitlines())
        except subprocess.CalledProcessError:
            changed = None  # fall back to "include all" if git diff fails

    found = []
    for name in MANUAL_DEPLOY_FILES:
        src = BACKEND_DIR / name
        rel_path = str((BACKEND_DIR / name).relative_to(ROOT)).replace("\\", "/")
        if not src.exists():
            print(f"    (skip, not found) {name}")
            continue
        if changed is not None and rel_path not in changed:
            print(f"    (skip, unchanged since {baseline}) {name}")
            continue
        shutil.copy2(src, dest / name)
        found.append(name)
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


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def last_patch_tag():
    """Most recent patch-* tag reachable from HEAD, or None if this is the first patch."""
    try:
        tags = _git("tag", "--list", f"{PATCH_TAG_PREFIX}*", "--sort=-creatordate")
    except subprocess.CalledProcessError:
        return None
    for tag in tags.splitlines():
        try:
            _git("merge-base", "--is-ancestor", tag, "HEAD")
            return tag
        except subprocess.CalledProcessError:
            continue
    return None


def generate_git_section(baseline):
    """Build the auto-generated 'files changed' + commit log section since baseline."""
    if baseline is None:
        return (
            "AUTO-GENERATED (git) — no previous patch tag found\n"
            "────────────────────────────────────────────────────\n"
            "This is the first patch built with auto-generated notes; nothing to diff against.\n"
        )

    head = _git("rev-parse", "--short", "HEAD")
    files = _git("diff", "--name-only", f"{baseline}..HEAD")
    commits = _git("log", "--oneline", f"{baseline}..HEAD")

    lines = [
        f"AUTO-GENERATED (git) — changes since {baseline} ({head})",
        "────────────────────────────────────────────────────",
        "",
        "Files changed:",
    ]
    lines += [f"  {f}" for f in files.splitlines()] if files else ["  (none)"]
    lines += ["", "Commits:"]
    lines += [f"  {c}" for c in commits.splitlines()] if commits else ["  (none)"]
    lines.append("")
    return "\n".join(lines)


def write_patch_notes(dest_dir: Path, patch_name: str, baseline: str):
    print("==> Writing PATCH_NOTES.txt...")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    header = (
        "PATCH NOTES\n"
        "===========\n"
        f"Branch : {branch}\n"
        f"Date   : {date.today().isoformat()}\n\n"
    )
    hand_written = PATCH_NOTES_FILE.read_text(encoding="utf-8").strip() if PATCH_NOTES_FILE.exists() else (
        f"(scripts/PATCH_NOTES.md not found — create it with PROBLEM/FIX notes for this patch)"
    )
    git_section = generate_git_section(baseline)
    full_notes = header + hand_written + "\n\n" + git_section
    (dest_dir / "PATCH_NOTES.txt").write_text(full_notes.strip() + "\n", encoding="utf-8")


def tag_patch(patch_name: str):
    tag = f"{PATCH_TAG_PREFIX}{patch_name}"
    try:
        _git("tag", tag)
        print(f"==> Tagged HEAD as {tag} (baseline for next patch's auto-generated notes)")
    except subprocess.CalledProcessError as e:
        print(f"    (warning) could not create tag {tag}: {e.stderr.strip()}")


def main():
    patch_name = sys.argv[1] if len(sys.argv) > 1 else f"datamind-deploy-{date.today().isoformat()}"
    patch_dir = ARCHIVES_DIR / patch_name

    if patch_dir.exists():
        shutil.rmtree(patch_dir)

    baseline = last_patch_tag()

    dist_dir = build_frontend()
    copy_frontend(dist_dir, patch_dir / "frontend")
    copy_backend(patch_dir / "backend")
    copy_manual_files(patch_dir / "manual_deploy", baseline)
    write_patch_notes(patch_dir, patch_name, baseline)

    print("==> Zipping...")
    zip_base = ARCHIVES_DIR / patch_name
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=ARCHIVES_DIR, base_dir=patch_name)
    shutil.rmtree(patch_dir)

    tag_patch(patch_name)

    print(f"\nDone: {zip_path}")
    print(f"\nWARNING: Remember to manually apply files in manual_deploy/ on the server.")
    print(f"WARNING: Edit scripts/PATCH_NOTES.md with PROBLEM/FIX notes before the *next* patch.")


if __name__ == "__main__":
    main()
