<!-- Edit this file for each new patch — describe PROBLEM/FIX per change.
     create_deploy_patch.py prepends the branch/date header and appends an
     auto-generated "Files changed since last patch" + commit log section,
     so don't duplicate that information here. -->

MANUAL DEPLOY FILES
───────────────────
The following files are .gitignored on the production server and must be
applied manually from the manual_deploy/ folder. Do NOT overwrite blindly —
diff against the live server copy first.

  * manual_deploy/main.py
  * manual_deploy/llm.py

CHANGES IN THIS PATCH
─────────────────────

A. fix(query): surface broken SalesPlay sync on stale time-scoped queries
   PROBLEM : A user asking a "last month/week/year" question against a
             tenant whose SalesPlay sync had silently failed (expired API
             token) got a bare "Found 0 results" with no indication their
             data was stale — the existing staleness-note keyword list only
             covered "this month", "last 24", "last hour", etc., not "last
             month/week/year".
   FIX     : Added the missing keywords. Also escalate to a clearer
             broken-connection message (last-synced date + reconnect
             instructions) specifically when the query returns 0 rows and
             the sync is over 24h stale, since that combination almost
             certainly means the answer is wrong rather than genuinely $0.
   FILES   : datamind/backend/main.py

DB CHANGES  : None
.ENV CHANGES: None
