# Salesplay embed QA suite

`scripts/qa_salesplay.py` — end-to-end QA for the Salesplay web-embed across N
backend instances that share **one** database.

It starts the backends, drives the real HTTP endpoints the widget calls, and
asserts against real database rows. Nothing is mocked and nothing is stubbed.
If it passes, the flow it describes actually works.

---

## Why this exists

We wanted to run two deployments against one database:

| Instance | `SUBSCRIPTION_FREE` | Who it serves |
|---|---|---|
| **A** | `false` | Paid group — trial or pay |
| **B** | `true` | Beta group — free trial only, no subscription |

…and, after roughly two weeks, flip B to `false` so both run in paid mode.

That plan rests on claims that are cheap to assert and expensive to get wrong:
each instance must serve its *own* flag; concurrent bootstraps must not fight
over the shared `subscription_plans` rows; a mid-trial merchant must be
untouched by the flip; and an expired beta merchant must be able to pay once
the free period ends.

One of those claims was **false** when this suite was first run. See
[Findings](#findings).

---

## Requirements

- The backend's own Python interpreter (needs `mysql-connector-python`,
  `python-dotenv`).
- `datamind/backend/.env` — database credentials and Salesplay base URLs are
  read from it. **The suite never edits this file.**
- One free TCP port per instance.
- One Salesplay app access token (`aat`) per concurrent merchant state.
  Five is the minimum; seven gives two spares.

### Why the `.env` is never edited

`start.py` calls `load_dotenv()`, which defaults to `override=False`, so a
shell environment variable **wins** over the file. The suite sets
`SUBSCRIPTION_FREE` and `UVICORN_PORT` per child process:

```bash
# Instance A — reads .env as-is
python start.py

# Instance B — same .env, same database, opposite mode
SUBSCRIPTION_FREE=true UVICORN_PORT=8001 python start.py
```

Your `.env` stays at whatever it says. Nothing to remember to put back.

---

## Configuration

Copy the template and fill in real tokens:

```bash
cp scripts/qa_salesplay.example.json scripts/qa_salesplay.local.json
```

`qa_*.local.json` is gitignored — it holds **live credentials**, never commit it.

```json
{
  "partner_key": "sp_dev_test",
  "backend_dir": "datamind/backend",
  "python": "C:/Python312/python.exe",

  "instances": [
    { "name": "A", "port": 8000, "subscription_free": false },
    { "name": "B", "port": 8001, "subscription_free": true }
  ],

  "aats": { "aat1": "...", "aat2": "...", "…": "…" },

  "scenarios": {
    "B1_fresh_beta":   { "aat": "aat1", "instance": "B" },
    "B2_trial_beta":   { "aat": "aat2", "instance": "B" },
    "B3_expired_beta": { "aat": "aat3", "instance": "B" },
    "A1_fresh_paid":   { "aat": "aat4", "instance": "A" },
    "A3_paid_active":  { "aat": "aat5", "instance": "A" }
  }
}
```

**Instances come from this file, not from the code.** Three or more works by
adding an entry with a free port — the runner iterates the list, and every
infra check counts instances rather than assuming two.

---

## Commands

```bash
python scripts/qa_salesplay.py check     # read-only preflight — changes nothing
python scripts/qa_salesplay.py run       # the full suite
python scripts/qa_salesplay.py reset     # QA-only: recycle the aats
python scripts/qa_salesplay.py up        # start instances and leave them
python scripts/qa_salesplay.py status
python scripts/qa_salesplay.py down
```

Flags for `run`:

| Flag | Effect |
|---|---|
| `--payment` | **Also** run the manual card step. Off by default — it needs a real Salesplay charge |
| `--skip-sweep` | Skip the cross-instance restart check (it restarts an instance) |
| `--keep-up` | Leave instances running after the suite finishes |
| `--config PATH` | Use a different config file |

`--yes` skips the confirmation prompt on `reset`.

Exit code is `0` only if every check passed.

### Always run `check` first

It resolves every aat against Salesplay's `/profile`, prints the merchant email
each maps to, and reports which ones already exist in DataMind. It touches
nothing. An expired token shows up here in seconds instead of halfway through a
run.

---

## What it asserts

### Infrastructure

| Check | Why it matters |
|---|---|
| All instances up simultaneously | The actual deployment topology, not one-at-a-time simulation |
| `Standard` survives concurrent bootstraps | Both instances run `bootstrap_billing_tables` on every start |
| Each instance serves its **own** `SUBSCRIPTION_FREE` | Proves the flag is per-process, not last-writer-wins |
| Exactly one instance wins the scheduler lock | Otherwise every integration syncs once per instance |
| The scheduler lock is **still held** | Winning it and dropping it is as broken as two winners |
| Restarting one instance sweeps the other's `syncing` rows | Documents a known defect — see [Findings](#findings) |

### Merchant scenarios

| Tag | State | Asserts |
|---|---|---|
| **B1** | Fresh on beta | `subscribe`, Salesplay `preview`, Salesplay `payment` all return **403** |
| **B2** | Trial on beta | Row is `trial` on `Standard`; API reports `subscription_free: true` |
| **B3** | Expired on beta | `period_end` in the past, status `expired`, `can_use_ai: false` |
| **A1** | Fresh on paid | Anonymous `subscription/info` is **not 401**; `subscription_free: false` |
| **A3** | Paid on paid | With `--payment`: plan resolves to `Standard`/`25000`/`active` |

### The flip

After Phase 1, every beta instance is restarted with `SUBSCRIPTION_FREE=false`:

- The flipped instance reports `subscription_free: false`
- **Mid-trial merchant untouched** — same status, same `period_end`. The flag
  never wrote to the database
- Expired merchant stays expired
- **The payment route is reachable again** (no 403) — the point of the flip
- A never-trialled merchant now sees paid mode

---

## Findings

### 1. The scheduler lock was not held — FIXED

`_try_acquire_scheduler_lock` ([integrations.py](../datamind/backend/integrations.py))
borrowed a **pooled** connection and deliberately never called `.close()`, with
a comment saying so. But it kept **no reference** to that connection, so Python
garbage-collected it straight back into the pool, the session reset, and
`GET_LOCK` silently dropped.

Observed with both instances live:

- **Both** logged `Scheduler: acquired DB advisory lock`
- `SELECT IS_USED_LOCK('datamind_scheduler')` returned `NULL` — nobody holding it

Consequence for two backends on one database: both run the scheduler, so every
merchant's integrations sync **twice concurrently** — duplicate Salesplay API
calls, doubled rate-limit consumption, two writers racing on
`integration_records` upserts.

Fixed by parking the connection at module scope. The reference *is* the
mechanism: `GET_LOCK` lives on a session, and with a pooled connection, not
closing it is not enough.

Regression guard: the two `INFRA` scheduler checks above. They read each
instance's log for the acquisition message **and** query `IS_USED_LOCK`,
because winning-then-dropping and two-winners fail differently.

### 2. Cross-instance restart sweep — KNOWN, NOT FIXED

`bootstrap_integration_tables` runs

```sql
UPDATE user_integrations SET status='error' WHERE status='syncing'
```

with **no tenant filter**, so restarting instance A errors instance B's
in-flight syncs. Self-heals on the next scheduler tick (errored rows are
retried with a backoff multiplier).

The suite asserts this happens, so it is documented behaviour rather than a
production surprise. **Coordinate restart windows across instances.**

### 3. `get_ai_pos_info` returns 404 on predev2 — ENVIRONMENT

Salesplay's `subscriptions/get_ai_pos_info` is **not deployed** on
`predev2backoffice.nvision.lk`; a direct call returns HTTP 404. The proxy wraps
any non-ok upstream as 502.

This is why the 401 regression test asserts **"not 401"** rather than **"200"**.
The fix is about *authorization*: before it, the endpoint returned 401 without
ever reaching Salesplay. Anything other than 401 proves an anonymous caller is
no longer blocked. Whether Salesplay answers is a separate concern, and must
not be reported as an auth regression.

`subscriptions/order/preview` **does** work on that base — so verify
`SALESPLAY_SUBSCRIPTION_BASE_URL` before go-live.

---

## `reset` is QA-only

It deletes every DataMind row for the configured merchants so the same aats can
be reused for a fresh onboarding test. Salesplay accounts are untouched.

**This is not, and must never become, an operational procedure.** Production
never deletes a merchant — a trial user moves into the subscription flow and
their history stays intact, which is exactly the path the FLIP scenarios
exercise. `reset` exists only because testing a *brand-new* onboarding needs a
merchant who has never onboarded, and aats are scarce.

**The rest of the suite is idempotent, so a rerun does not need it.** Expiry
checks assert resulting state rather than `UPDATE` rowcounts, and the trial
check accepts a row a previous pass already expired.

It prompts for confirmation, and it only ever touches emails resolved from the
configured aats.

---

## How many aats

One aat maps to exactly one DataMind account: `salesplay_onboard` reads the
merchant's email from Salesplay's `/profile` and that email **is** the account.

Two things make them recyclable:

1. **Onboarding does not start a trial** — that only happens on an explicit
   `POST /embed/salesplay/start-trial`. Account creation and subscription state
   are controlled separately.
2. Deleting the DataMind rows makes the aat fresh again (`reset`).

So the real question is how many accounts must be alive *at the same time*:

| Count | Covers |
|---|---|
| **5** | Every state in the matrix above, all concurrent |
| **7** | The same, plus two spares — state is one-shot, and a mistimed step burns an aat |
| **3** | Bare minimum if you `reset` between rounds |

---

## Troubleshooting

**Every aat shows `UNRESOLVED` in `check`.**
Salesplay sits behind Cloudflare, which rejects urllib's default
`Python-urllib/x.y` agent with `error code: 1010` before the request reaches
their app. The suite sends `python-requests/2.32.3` to match the backend's own
client. If this reappears, Cloudflare's rules changed.

**Onboard returns 502 right after startup.**
The backend proxies to Salesplay over the public internet and kicks off a data
sync during boot, so the first calls after a start can lose a DNS lookup.
`onboard()` retries transient 502/504/connection errors three times. A blip is
not a finding about the branch, and failing the suite on one would bury the
findings that matter.

**`Field required: pk` from `/embed/context`.**
The query parameter is `pk`, not `partner_key`.

**HTTP 422 where 403 was expected.**
FastAPI validates the request body *before* the endpoint runs, so an incomplete
body never reaches `_reject_if_subscription_free` and the 403 under test is
invisible. `preview_body()` and `payment_body()` send the minimum that passes
validation.

**A port is busy when starting.**
`start()` refuses to adopt a backend it did not launch — its `SUBSCRIPTION_FREE`
was set by whoever started it, and every assertion depends on knowing that
value. It stops the existing listener and starts its own. `stop()` kills the
process tree and then verifies the port is actually free, because uvicorn's
worker child outlives a parent-only kill and would otherwise keep serving with
the **old** flag value.

---

## Adding a third instance

```json
{ "name": "C", "port": 8002, "subscription_free": true }
```

Every infra check counts the configured instances, and the flip phase restarts
*every* instance whose `subscription_free` is `true`. Point scenarios at `"C"`
by name. No code changes.

To drive a second widget against it:

```bash
VITE_BACKEND=http://localhost:8002 npm run dev -- --port 5175
```

`vite.config.js` reads `VITE_BACKEND` for all its proxy rules; it defaults to
`http://localhost:8000`.

---

## Baseline result

Two instances, one database, seven aats, `--payment` not requested:

```
33 passed, 0 failed, 1 skipped
```

The single skip is the manual card step, which is covered by manual testing.
