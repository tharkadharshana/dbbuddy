<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->
<!-- ponytail: the auto env-key diff supersedes hand-written ".ENV CHANGES"; drop
     that section once you trust it. -->

CHANGES IN THIS PATCH
─────────────────────

A. feat: pure agent architecture for the answering flow
   PROBLEM : The answering path branched between several architectures
             depending on classification, so the same question could be
             answered two different ways and produce different numbers.
             Report tools, forecasting and anomaly detection were only
             reachable from some of those branches.
   FIX     : Single agent flow behind AI_FLOW. Retries within the agent
             (AGENT_MAX_ATTEMPTS) and returns an honest transient error
             rather than falling through to a weaker path.
   FILES   : datamind/backend/main.py, mcp_server/agent.py,
             mcp_server/report_tools.py, mcp_server/safety.py,
             report_cache/answer.py, report_cache/registry.py
   ACTION  : REQUIRES AI_FLOW=agent in the server .env — see .ENV CHANGES.
             The code default is 'legacy'; without this line the whole
             feature is silently inactive.

B. feat(reporting): report metrics rework + new stock and sales endpoints
   PROBLEM : Report totals were re-summed from returned rows, which
             disagreed with the merchant's POS dashboard whenever paging
             or rounding was involved. No stock/sales endpoints existed
             for the agent to call.
   FIX     : get_report_metrics is now authoritative for totals and the
             model is instructed to trust it instead of re-summing. Added
             the stock and sales endpoints, calendar-correct billing
             history windows, and refinalization of trailing periods so
             late-posted transactions are picked up.
   FILES   : datamind/backend/billing.py, report_cache/ingest.py,
             report_cache/registry.py, mcp_server/report_tools.py
   ACTION  : REPORT_CACHE_REFINALIZE_MONTHS / REPORT_CACHE_DEEP_REFINALIZE_DAYS
             are new but their code defaults (2 / 7) match the recommended
             values — adding them to .env is documentation, not a behaviour
             change.

C. revert(embed): Salesplay AI POS paid-plans layer removed before release
   PROBLEM : The AI POS payment-plans layer (plans screen, Salesplay
             subscription proxy, card handling, Beta badge removal) was
             built and merged during this window, then pulled — SalesPlay
             merchants stay on open Beta access for now.
   FIX     : Full revert of that layer. Net effect against the currently
             deployed production build is ZERO for the payment flow: it was
             added and removed inside this same patch window, so the server
             never runs a version that has it. Beta badges are back in the
             chat header and onboarding consent screen, and the button copy
             is "Try SalesPlay AI Beta" again.
   FILES   : datamind/backend/embed.py, billing.py,
             datamind/frontend/src/embed/EmbedApp.jsx, EmbedChat.jsx,
             EmbedSalesplayAutoInit.jsx, embedApi.js
             (deleted: EmbedSalesplayPlans.jsx, embedSalesplaySubscription.js)
   ACTION  : Remove the now-dead SALESPLAY_SUBSCRIPTION_BASE_URL and
             VITE_SALESPLAY_* keys if present — see .ENV CHANGES.

D. feat(markdown): heading support and correct ordered-list rendering
   PROBLEM : Answers containing headings rendered as literal '#' text and
             ordered lists restarted numbering at each item.
   FIX     : Added heading parsing and fixed ordered-list numbering.
   FILES   : datamind/frontend/src/components/Markdown.jsx

E. fix(ui): "rows" renamed to "records", sidebar sync line removed
   PROBLEM : User-facing copy said "rows", which reads as database jargon
             to a merchant, and the sidebar showed an "N records synced"
             line that was frequently stale.
   FIX     : Renamed the wording throughout and removed the sidebar line.
   FILES   : datamind/frontend/src/components/Sidebar.jsx, UI.jsx,
             pages/*.jsx

F. style(embed): flattened SalesPlay chat header icons
   PROBLEM : The header's minimize and open-app controls were bordered
             pills that crowded the narrow embed header.
   FIX     : Borderless 28px icon buttons with a hover highlight.
   FILES   : datamind/frontend/src/embed/EmbedChat.jsx, embed.css

G. QA harness for billing state (development only — inert on the server)
   PROBLEM : Testing trial/quota/plan transitions required hand-editing
             the database.
   FIX     : Added /qa routes and a QA dashboard page that mutate billing
             state. Triple-gated: QA_ROUTES_ENABLED, refusal on any
             production signal (FORCE_HTTPS, prod-looking DB host), and a
             non-empty QA_ROUTES_EMAILS allowlist. When any check fails the
             router is never mounted and the paths 404.
   FILES   : datamind/backend/qa_routes.py,
             datamind/frontend/src/pages/QAPage.jsx
   NOTE    : qa_routes.py ships in this zip's backend/ like every other file
             (no more per-file exclusions), but stays inert unless
             QA_ROUTES_ENABLED + a real QA_ROUTES_EMAILS allowlist are set —
             which they must never be in production. Vite still strips
             QAPage from the production frontend build. Do not add
             QA_ROUTES_* to the production .env.

DB CHANGES  : None

.ENV CHANGES: YES — backend .env

  ADD (required — feature is inactive without it):
    AI_FLOW=agent

  ADD (optional — these match their code defaults, add for documentation):
    AI_FLOW_TEST_EMAILS=
    AGENT_MAX_ATTEMPTS=2
    REPORT_CACHE_REFINALIZE_MONTHS=2
    REPORT_CACHE_DEEP_REFINALIZE_DAYS=7

  REMOVE (dead after change C — harmless if left, nothing reads them):
    SALESPLAY_SUBSCRIPTION_BASE_URL            (backend .env)
    VITE_SALESPLAY_ACTIVATION_POLL_ATTEMPTS    (frontend .env)
    VITE_SALESPLAY_ACTIVATION_POLL_INTERVAL_MS (frontend .env)
    VITE_SALESPLAY_CARD_POLL_INTERVAL_MS       (frontend .env)

  DO NOT ADD:
    QA_ROUTES_ENABLED / QA_ROUTES_EMAILS — development only, see G.
