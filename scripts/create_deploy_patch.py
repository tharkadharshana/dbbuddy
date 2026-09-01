#!/usr/bin/env python3
"""
Build a deployable patch zip containing:
  <patch_name>/frontend/          - production frontend build (vite build)
  <patch_name>/backend/           - full backend source, minus EXCLUDE_NAMES only.
                                    Overwrite the server's backend/ wholesale.
  <patch_name>/PATCH_NOTES.txt    - optional hand-written prose from
                                    scripts/PATCH_NOTES.md, plus auto-generated
                                    sections: env-key diff, changed files, commits.
  <patch_name>/BUILD_INFO.json    - version / commit / branch / build time.
  <patch_name>/MANIFEST.sha256    - sha256 of every packaged file.

Versioning: ./VERSION is the last version BUILT. Every run bumps its patch
component (3.1.0 -> 3.1.1) and writes it back, so two builds never share a
version. --version pins an exact one (and still writes it back); --no-bump
rebuilds the current VERSION untouched.

Usage:
  python scripts/create_deploy_patch.py                        # bump patch, build
  python scripts/create_deploy_patch.py --version 3.2.0        # pin, e.g. a minor release
  python scripts/create_deploy_patch.py --no-bump              # rebuild same version
  python scripts/create_deploy_patch.py --no-build --no-tag    # CI: build + tag done already

Output: archives/<patch-name>-<UTC yyyymmdd-hhmm>.zip
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

# Files/dirs to skip wherever they're found. The backend is copied wholesale minus
# these - an allowlist used to silently drop every newly added backend module.
EXCLUDE_NAMES = {
    "__pycache__", ".env", ".env.production", ".env.local",
    "data", "logs", "dist", "docs", "scratch", "tests", ".pytest_cache",
}

# Files copied from the frontend build output that shouldn't ship to prod.
# two-brand-test.html embeds live partner keys in its page source -- it is a
# local harness, never a deployable page.
FRONTEND_EXCLUDE_FILES = {"iframe_test.html", "two-brand-test.html"}

# No brand name may appear in the embed bundle: one build serves every brand, so
# anything baked in is wrong for all but one of them. The usual cause is a stale
# gitignored frontend env file (.env / .env.front.*) still setting VITE_APP_NAME
# or VITE_APP_URL -- which the env-key diff below cannot catch, because those
# files are not in git. Only embed-* is scanned; the main app legitimately
# carries its own name.
BRAND_WORDS_RE = re.compile(r"sales\s*play|sellmo|nvision|datamind", re.I)

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


def build_frontend(mode=None):
    """mode selects the vite env file: --mode front.dev -> .env.front.dev.
    Defaults to vite's own default (production mode -> .env.production)."""
    print(f"==> Building frontend (npm run build{f' --mode {mode}' if mode else ''})...")
    dist_dir = FRONTEND_DIR / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    cmd = ["npm", "run", "build"] + (["--", "--mode", mode] if mode else [])
    subprocess.run(cmd, cwd=FRONTEND_DIR, check=True, shell=True)
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


def verify_no_brand_baked_in(dest):
    """Refuse to package an embed bundle with a brand name compiled into it."""
    print("==> Checking the embed bundle for baked-in brand names...")
    offenders = []
    for path in sorted((dest / "assets").glob("embed-*")):
        if path.suffix not in (".js", ".css"):
            continue
        found = set(BRAND_WORDS_RE.findall(path.read_text(encoding="utf-8", errors="ignore")))
        if found:
            offenders.append((path.name, sorted(found)))
    if offenders:
        detail = "\n".join(f"    {name}: {', '.join(words)}" for name, words in offenders)
        sys.exit(
            "ERROR: brand names are compiled into the embed bundle:\n" + detail +
            "\n  A brand value was baked in at build time. Check the frontend env file"
            "\n  this build used (.env, and .env.front.* when --frontend-env is passed)"
            "\n  and remove VITE_APP_NAME / VITE_APP_URL; brand values now arrive at"
            "\n  runtime from GET /embed/context."
        )


def copy_backend(dest):
    """Copy the whole backend tree minus EXCLUDE_NAMES. Nothing else is special-cased -
    overwrite the server's backend/ directory wholesale."""
    print("==> Copying backend source...")
    shutil.copytree(BACKEND_DIR, dest, ignore=ignore_excluded)


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


SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def bump_patch(version: str) -> str:
    """3.1.0 -> 3.1.1. Refuse anything that is not a bare three-part semver rather
    than guessing at what to increment in e.g. '3.1.0-rc1'."""
    m = SEMVER_RE.match(version)
    if not m:
        sys.exit(
            f"ERROR: VERSION is '{version}', which is not a plain X.Y.Z semver.\n"
            "       Pass --version explicitly, or fix ./VERSION."
        )
    major, minor, patch = (int(g) for g in m.groups())
    return f"{major}.{minor}.{patch + 1}"


def write_version(version: str):
    """Persist the version just built, so the next run bumps from it."""
    write_text(VERSION_FILE, version + "\n")


def resolve_version(args) -> str:
    """--version pins, --no-bump reuses, otherwise bump VERSION's patch component."""
    if args.version:
        return args.version
    current = read_version()
    return current if args.no_bump else bump_patch(current)


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
    ap.add_argument("--version", help="pin an exact release version (default: ./VERSION with its patch bumped)")
    ap.add_argument("--no-bump", action="store_true",
                    help="rebuild ./VERSION as-is instead of bumping its patch component")
    ap.add_argument("--no-build", action="store_true",
                    help="reuse the existing frontend dist/ instead of running npm run build")
    ap.add_argument("--frontend-env", help="vite --mode for the frontend build, e.g. front.dev -> .env.front.dev")
    ap.add_argument("--no-tag", action="store_true",
                    help="don't create a git tag, and skip the clean-tree/branch guards (CI)")
    args = ap.parse_args()

    version = resolve_version(args)
    # Stamped in UTC, matching BUILD_INFO's built_at. The stamp is on the zip
    # only, not the directory inside it -- the unpacked tree keeps a stable
    # name so deploy steps do not have to know the build time.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    patch_name = args.name or f"datamind-v{version}"
    zip_stem = f"{patch_name}-{stamp}"
    patch_dir = ARCHIVES_DIR / patch_name

    if not args.no_tag:
        check_clean_tree()

    if patch_dir.exists():
        shutil.rmtree(patch_dir)

    baseline = last_release_tag()
    print(f"==> Version {version}, baseline {baseline or '(none)'}")

    dist_dir = FRONTEND_DIR / "dist" if args.no_build else build_frontend(args.frontend_env)
    copy_frontend(dist_dir, patch_dir / "frontend")
    verify_no_brand_baked_in(patch_dir / "frontend")
    copy_backend(patch_dir / "backend")
    write_patch_notes(patch_dir, version, baseline)
    write_build_info(patch_dir, version, baseline)
    write_manifest(patch_dir)

    print("==> Zipping...")
    ARCHIVES_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = shutil.make_archive(
        str(ARCHIVES_DIR / zip_stem), "zip", root_dir=ARCHIVES_DIR, base_dir=patch_name
    )
    # Keep PATCH_NOTES.txt outside the zip too - CI uses it as the release body.
    shutil.copy2(patch_dir / "PATCH_NOTES.txt", ARCHIVES_DIR / "PATCH_NOTES.txt")
    shutil.rmtree(patch_dir)

    # Only after the zip exists: a build that dies half way must not burn a
    # version number that was never shipped.
    if not args.version and not args.no_bump:
        write_version(version)
        print(f"==> VERSION -> {version} (commit it; the tag below predates the bump)")

    if not args.no_tag:
        tag_release(version)

    print(f"\nDone: {zip_path}")
    print("\nWARNING: apply the 'ENV KEYS' section of PATCH_NOTES.txt to the server .env.production.")


if __name__ == "__main__":
    main()
