#!/usr/bin/env python3
"""
Build a deployable patch zip containing:
  <patch_name>/frontend/          - production frontend build (vite build)
  <patch_name>/backend/           - backend source (everything except EXCLUDE_NAMES,
                                    DEV_ONLY_FILES and the manual-deploy files)
  <patch_name>/manual_deploy/     - files git-ignored on prod (main.py, llm.py);
                                    copy these manually - do NOT overwrite blindly.
  <patch_name>/PATCH_NOTES.txt    - optional hand-written prose from
                                    scripts/PATCH_NOTES.md, plus auto-generated
                                    sections: env-key diff, changed files, commits.
  <patch_name>/BUILD_INFO.json    - version / commit / branch / build time.
  <patch_name>/MANIFEST.sha256    - sha256 of every packaged file.

Usage:
  python scripts/create_deploy_patch.py                        # version from ./VERSION
  python scripts/create_deploy_patch.py --version 3.1.0
  python scripts/create_deploy_patch.py --no-build --no-tag    # CI: build + tag done already

Output: archives/<patch-name>.zip
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "datamind" / "frontend"
BACKEND_DIR = ROOT / "datamind" / "backend"
ARCHIVES_DIR = ROOT / "archives"
PATCH_NOTES_FILE = ROOT / "scripts" / "PATCH_NOTES.md"
VERSION_FILE = ROOT / "VERSION"

# Release tags are semver (v3.1.0). Older date-based patch-* tags are still honoured
# as a baseline so the first semver release can diff against the last manual patch.
RELEASE_TAG_PREFIX = "v"
LEGACY_TAG_PREFIX = "patch-"

# These files are .gitignored on the production server, so they must be applied
# manually by an admin. They are placed in manual_deploy/ inside the zip -
# NOT in backend/ - to make this explicit.
MANUAL_DEPLOY_FILES = ["main.py", "llm.py"]

# Backend files that must never ship to production (dev/QA-only entry points).
DEV_ONLY_FILES = {"qa_routes.py"}

# Files/dirs to skip wherever they're found. The backend is copied wholesale minus
# these - an allowlist used to silently drop every newly added backend module.
EXCLUDE_NAMES = {
    "__pycache__", ".env", ".env.production", ".env.local",
    "data", "logs", "dist", "docs", "scratch", "tests", ".pytest_cache",
}

# Files copied from the frontend build output that shouldn't ship to prod.
FRONTEND_EXCLUDE_FILES = {"iframe_test.html"}

# Env files whose *keys* are diffed between the baseline tag and HEAD.
ENV_DIFF_FILES = [
    "datamind/backend/.env.example",
    "datamind/frontend/.env.production",
]

# Conventional-commit prefixes, in the order they're reported.
COMMIT_GROUPS = [
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("revert", "Reverts"),
    ("perf", "Performance"),
    ("refactor", "Refactors"),
    ("chore", "Chores"),
    ("docs", "Docs"),
    ("style", "Style"),
    ("test", "Tests"),
]


def write_text(path: Path, text: str):
    """Always LF. These files are read on a Linux server; CRLF breaks `sha256sum -c`."""
    path.write_text(text, encoding="utf-8", newline="\n")


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _git_ok(*args):
    """Run a git command, return True on exit 0 - for boolean checks."""
    try:
        _git(*args)
        return True
    except subprocess.CalledProcessError:
        return False


def ignore_excluded(_dir, names):
    return [n for n in names if n in EXCLUDE_NAMES or n.endswith(".pyc")]


# --------------------------------------------------------------------------- build


def build_frontend():
    print("==> Building frontend (npm run build)...")
    dist_dir = FRONTEND_DIR / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR, check=True, shell=True)
    return dist_dir


def copy_frontend(dist_dir, dest):
    print("==> Copying frontend build...")
    if not dist_dir.exists():
        sys.exit(
            f"ERROR: {dist_dir} does not exist. Run without --no-build, or run "
            f"'npm run build' in {FRONTEND_DIR} first."
        )
    shutil.copytree(dist_dir, dest, ignore=ignore_excluded)
    for name in FRONTEND_EXCLUDE_FILES:
        f = dest / name
        if f.exists():
            f.unlink()


def copy_backend(dest):
    """Copy the whole backend tree minus excludes, dev-only files and manual-deploy files."""
    print("==> Copying backend source...")
    skip_at_root = set(MANUAL_DEPLOY_FILES) | DEV_ONLY_FILES

    def ignore_backend(directory, names):
        skipped = set(ignore_excluded(directory, names))
        if Path(directory) == BACKEND_DIR:
            skipped |= {n for n in names if n in skip_at_root}
        return list(skipped)

    shutil.copytree(BACKEND_DIR, dest, ignore=ignore_backend)
    for name in sorted(skip_at_root):
        if (BACKEND_DIR / name).exists():
            print(f"    (excluded) {name}")


def copy_manual_files(dest, baseline):
    """Copy git-ignored prod files to manual_deploy/ - must be applied by hand on the server.

    Only includes files that actually changed since the baseline tag, so an admin
    doesn't get main.py/llm.py in every patch when neither actually differs from
    what's already deployed.
    """
    print("==> Copying manual deploy files (only those changed since baseline)...")
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
            "Do NOT copy them blindly - diff against the live file first,\n"
            "then apply only the sections that changed.\n\n"
            "Files in this folder:\n"
            + "".join(f"  * {n}\n" for n in found)
        )
        write_text(dest / "README.txt", readme)


# ------------------------------------------------------------------------ baseline


def _tags_by_date(pattern):
    try:
        return _git("tag", "--list", pattern, "--sort=-creatordate").splitlines()
    except subprocess.CalledProcessError:
        return []


def last_release_tag():
    """Most recent release tag reachable from HEAD, or None if there is no baseline.

    Prefers semver v* tags; falls back to the legacy date-based patch-* tags so the
    first semver release still diffs against the last manually built patch.
    """
    head = _git("rev-parse", "HEAD")
    for pattern in (f"{RELEASE_TAG_PREFIX}*", f"{LEGACY_TAG_PREFIX}*"):
        for tag in _tags_by_date(pattern):
            # Skip a tag pointing at HEAD itself - in CI the release tag is already
            # pushed, and diffing against it would always come out empty.
            if _git("rev-list", "-n", "1", tag) == head:
                continue
            if _git_ok("merge-base", "--is-ancestor", tag, "HEAD"):
                return tag
    return None


# ----------------------------------------------------------------------- env diffs


ENV_KEY_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _env_keys(text):
    """Key names only. Values are never read, stored or printed."""
    return {m.group(1) for m in (ENV_KEY_RE.match(line) for line in text.splitlines()) if m}


def _keys_at(ref, rel_path):
    try:
        return _env_keys(_git("show", f"{ref}:{rel_path}"))
    except subprocess.CalledProcessError:
        return None  # file didn't exist at that ref


def generate_env_section(baseline):
    """Report env keys added/removed since baseline, per env file.

    This replaces the hand-written '.ENV CHANGES' prose - forgetting to add a new key
    on the server was a recurring deploy failure.
    """
    lines = [
        "ENV KEYS - action required on the server",
        "--------------------------------------------------------",
        "Review each '+' key before adding it: some keys in .env.example are dev/QA",
        "only (QA_ROUTES_*, FORCE_HTTPS) and must NOT be set in production.",
    ]
    if baseline is None:
        lines.append("  (no baseline tag - cannot diff env keys)")
        return "\n".join(lines) + "\n"

    any_change = False
    for rel_path in ENV_DIFF_FILES:
        old = _keys_at(baseline, rel_path)
        new = _keys_at("HEAD", rel_path)
        if new is None:
            continue
        if old is None:
            old = set()
        added = sorted(new - old)
        removed = sorted(old - new)
        if not added and not removed:
            continue
        any_change = True
        lines.append(f"  {rel_path}")
        for k in added:
            lines.append(f"    + {k}")
        for k in removed:
            lines.append(f"    - {k}   (no longer used; safe to delete)")
    if not any_change:
        lines.append("  (no env key changes since " + baseline + ")")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------- patch notes


def _group_commits(commits):
    groups = {key: [] for key, _ in COMMIT_GROUPS}
    other = []
    for line in commits:
        # "<sha> feat(embed): thing" -> type "feat"
        m = re.match(r"^\S+\s+(\w+)(\([^)]*\))?!?:", line)
        kind = m.group(1).lower() if m else None
        (groups[kind] if kind in groups else other).append(line)
    out = []
    for key, title in COMMIT_GROUPS:
        if groups[key]:
            out.append((title, groups[key]))
    if other:
        out.append(("Other", other))
    return out


def generate_git_section(baseline):
    """Auto-generated 'files changed' + grouped commit log since baseline."""
    if baseline is None:
        return (
            "AUTO-GENERATED (git) - no previous release tag found\n"
            "--------------------------------------------------------\n"
            "This is the first patch built with auto-generated notes; nothing to diff against.\n"
        )

    head = _git("rev-parse", "--short", "HEAD")
    files = _git("diff", "--name-only", f"{baseline}..HEAD").splitlines()
    commits = _git("log", "--oneline", "--no-merges", f"{baseline}..HEAD").splitlines()

    lines = [
        f"AUTO-GENERATED (git) - changes since {baseline} ({head})",
        "--------------------------------------------------------",
        "",
        "Commits:",
    ]
    if commits:
        for title, group in _group_commits(commits):
            lines.append(f"  {title}:")
            lines += [f"    {c}" for c in group]
    else:
        lines.append("  (none)")
    lines += ["", "Files changed:"]
    lines += [f"  {f}" for f in files] if files else ["  (none)"]
    lines.append("")
    return "\n".join(lines)


def write_patch_notes(dest_dir: Path, version: str, baseline: str):
    print("==> Writing PATCH_NOTES.txt...")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    header = (
        "PATCH NOTES\n"
        "===========\n"
        f"Version: {version}\n"
        f"Branch : {branch}\n"
        f"Commit : {_git('rev-parse', '--short', 'HEAD')}\n"
        f"Date   : {datetime.now(timezone.utc).date().isoformat()}\n\n"
    )
    hand_written = ""
    if PATCH_NOTES_FILE.exists():
        raw = PATCH_NOTES_FILE.read_text(encoding="utf-8")
        # Strip <!-- --> editor instructions; they're for whoever writes the file,
        # not for whoever deploys the patch.
        hand_written = re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()
    parts = [header]
    if hand_written:
        parts.append(hand_written + "\n")
    parts.append(generate_env_section(baseline))
    parts.append(generate_git_section(baseline))
    write_text(dest_dir / "PATCH_NOTES.txt", "\n".join(parts).strip() + "\n")


# ------------------------------------------------------------- build info/manifest


def write_build_info(dest_dir: Path, version: str, baseline: str):
    info = {
        "version": version,
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "baseline_tag": baseline,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    write_text(dest_dir / "BUILD_INFO.json", json.dumps(info, indent=2) + "\n")


def write_manifest(dest_dir: Path):
    """sha256 per packaged file, in `sha256sum -c` format, so an upload can be verified."""
    lines = []
    for path in sorted(p for p in dest_dir.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rel = path.relative_to(dest_dir).as_posix()
        lines.append(f"{digest}  {rel}")
    write_text(dest_dir / "MANIFEST.sha256", "\n".join(lines) + "\n")


# ---------------------------------------------------------------------------- main


def read_version():
    if not VERSION_FILE.exists():
        sys.exit(f"ERROR: {VERSION_FILE} not found. Pass --version explicitly.")
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def check_clean_tree():
    """Local builds only: a dirty tree or a non-main branch means the zip won't match the tag."""
    if _git("status", "--porcelain"):
        sys.exit("ERROR: working tree is dirty. Commit or stash before building a release.")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch != "main":
        sys.exit(
            f"ERROR: on branch '{branch}'. Releases are built from 'main' only.\n"
            "       (use --no-tag for a throwaway test build from another branch)"
        )
    if _git_ok("rev-parse", "--verify", "origin/main"):
        behind = _git("rev-list", "--count", "HEAD..origin/main")
        if behind != "0":
            sys.exit(f"ERROR: local main is {behind} commit(s) behind origin/main. Pull first.")


def tag_release(version: str):
    tag = f"{RELEASE_TAG_PREFIX}{version}"
    try:
        _git("tag", "-a", tag, "-m", f"Release {tag}")
        print(f"==> Tagged HEAD as {tag}. Push it to trigger the release workflow:")
        print(f"    git push origin {tag}")
    except subprocess.CalledProcessError as e:
        print(f"    (warning) could not create tag {tag}: {e.stderr.strip()}")


def main():
    ap = argparse.ArgumentParser(description="Build a deployable patch zip.")
    ap.add_argument("name", nargs="?", help="patch name (default: datamind-v<version>)")
    ap.add_argument("--version", help="release version (default: contents of ./VERSION)")
    ap.add_argument("--no-build", action="store_true",
                    help="reuse the existing frontend dist/ instead of running npm run build")
    ap.add_argument("--no-tag", action="store_true",
                    help="don't create a git tag, and skip the clean-tree/branch guards (CI)")
    args = ap.parse_args()

    version = args.version or read_version()
    patch_name = args.name or f"datamind-v{version}"
    patch_dir = ARCHIVES_DIR / patch_name

    if not args.no_tag:
        check_clean_tree()

    if patch_dir.exists():
        shutil.rmtree(patch_dir)

    baseline = last_release_tag()
    print(f"==> Version {version}, baseline {baseline or '(none)'}")

    dist_dir = FRONTEND_DIR / "dist" if args.no_build else build_frontend()
    copy_frontend(dist_dir, patch_dir / "frontend")
    copy_backend(patch_dir / "backend")
    copy_manual_files(patch_dir / "manual_deploy", baseline)
    write_patch_notes(patch_dir, version, baseline)
    write_build_info(patch_dir, version, baseline)
    write_manifest(patch_dir)

    print("==> Zipping...")
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = shutil.make_archive(
        str(ARCHIVES_DIR / patch_name), "zip", root_dir=ARCHIVES_DIR, base_dir=patch_name
    )
    # Keep PATCH_NOTES.txt outside the zip too - CI uses it as the release body.
    shutil.copy2(patch_dir / "PATCH_NOTES.txt", ARCHIVES_DIR / "PATCH_NOTES.txt")
    shutil.rmtree(patch_dir)

    if not args.no_tag:
        tag_release(version)

    print(f"\nDone: {zip_path}")
    print("\nWARNING: apply manual_deploy/ files by hand on the server (diff first).")
    print("WARNING: apply the 'ENV KEYS' section of PATCH_NOTES.txt to the server .env.production.")


if __name__ == "__main__":
    main()
