# Branching and releases

## Branches

| Branch | Role | Cut from | Merges into |
|---|---|---|---|
| `main` | **Production.** The only source of release tags. Protected. | — | — |
| `dev` | Integration / testing. Unstable is fine here. | `main` | never merged into `main` wholesale |
| `feature/*`, `fix/*`, `chore/*` | One change each. | **`main`** | `dev` first, then `main` when proven |
| `beta` | Frozen. Superseded by `dev`. | — | — |

The one rule that matters: **cut every branch from `main`, not from `dev`.**

Because a feature branch contains only its own commits on top of production, any subset
of features can ship independently. If features 1, 2, 3 are all in `dev` and only 1 and 2
are stable, you merge branches 1 and 2 into `main` and leave 3 in `dev`. No cherry-picking.

```bash
# start work
git checkout main && git pull
git checkout -b feature/thing

# integration test
git checkout dev && git merge feature/thing && git push

# when it is proven stable, PR feature/thing -> main
```

Keep `dev` current with `git checkout dev && git merge main`. Never merge `dev` into `main`.

## Cutting a release

1. Merge the stable feature branches into `main` via PR.
2. Optionally add PROBLEM/FIX prose to [scripts/PATCH_NOTES.md](scripts/PATCH_NOTES.md).
   Everything else in the notes is generated from git.
3. Bump [VERSION](VERSION) (semver), commit on `main`, then tag and push:

```bash
git checkout main && git pull
# edit VERSION
git commit -am "chore(release): 3.2.0"
git push
git tag -a v3.2.0 -m "Release v3.2.0"
git push origin v3.2.0
```

The `Release` workflow ([.github/workflows/release.yml](.github/workflows/release.yml))
then builds the frontend, packages the patch and attaches
`archives/datamind-v3.2.0.zip` to a GitHub Release. It refuses to run if the tag is
not on `main`.

To build the same zip locally instead: `python scripts/create_deploy_patch.py`
(requires a clean tree on an up-to-date `main`; it also creates the tag for you).
Add `--no-tag` for a throwaway test build from any branch.

## Zip contents

```
datamind-v3.2.0/
  frontend/          full production build - delete the live frontend, drop this in
  backend/           full backend source   - overwrite the live files, no exceptions
  PATCH_NOTES.txt    env-key changes, grouped commits, changed files
  BUILD_INFO.json    version, commit sha, branch, build time
  MANIFEST.sha256    checksum per file
```

`backend/` is everything under `datamind/backend/` except `__pycache__`, `data`,
`logs`, `dist`, `docs`, `scratch`, `tests`, and any `.env`/`.env.production`. Every
backend file, including `main.py`, `llm.py` and `qa_routes.py`, ships and gets
overwritten wholesale — no allowlist, no manual-deploy split, nothing to remember.
`qa_routes.py` stays inert without `QA_ROUTES_ENABLED` + a real allowlist in the
server's own `.env.production`; never set those in production.

## Applying on the server

1. Upload and unzip. Verify: `sha256sum -c MANIFEST.sha256`.
2. Read the **ENV KEYS** section of `PATCH_NOTES.txt`. Add every `+` key to the
   server's `datamind/backend/.env.production` before restarting.
3. Delete the live frontend directory, copy `frontend/` in its place.
4. Overwrite the live backend with `backend/` wholesale.
5. Restart the backend (`python start.py --prod`) and check `/health`.

The frontend needs no server-side env: `VITE_*` values are baked in at build time
from the tracked [datamind/frontend/.env.production](datamind/frontend/.env.production).
Changing a prod URL means editing that file, committing, and cutting a new release.
