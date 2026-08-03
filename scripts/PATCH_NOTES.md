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

C. feat(markdown): heading support and correct ordered-list rendering
   PROBLEM : Answers containing headings rendered as literal '#' text and
             ordered lists restarted numbering at each item.
   FIX     : Added heading parsing and fixed ordered-list numbering.
   FILES   : datamind/frontend/src/components/Markdown.jsx

D. fix(ui): "rows" renamed to "records", sidebar sync line removed
   PROBLEM : User-facing copy said "rows", which reads as database jargon
             to a merchant, and the sidebar showed an "N records synced"
             line that was frequently stale.
   FIX     : Renamed the wording throughout and removed the sidebar line.
   FILES   : datamind/frontend/src/components/Sidebar.jsx, UI.jsx,
             pages/*.jsx

E. style(embed): flattened SalesPlay chat header icons
   PROBLEM : The header's minimize and open-app controls were bordered
             pills that crowded the narrow embed header.
   FIX     : Borderless 28px icon buttons with a hover highlight.
   FILES   : datamind/frontend/src/embed/EmbedChat.jsx, embed.css

F. QA harness for billing state (development only — inert on the server)
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

G. fix(billing): stop silently auto-granting trials at account creation
   PROBLEM : start_trial() ran unconditionally at registration and at every
             Salesplay onboarding, so a brand-new account already had an
             active trial before the user picked anything on the package
             screen. Refreshing or reopening the tab dropped them straight
             into the app, and the Salesplay "already added a payment method
             or paid?" button appeared to work on an account with no card.
   FIX     : A subscription is only ever created by an explicit user action.
             New endpoints POST /billing/trial and
             POST /embed/salesplay/start-trial, wired to the actual
             "Start trial" buttons. Until one is pressed the account reads
             no_subscription and the access check blocks correctly.
   FILES   : datamind/backend/main.py, datamind/backend/embed.py,
             datamind/frontend/src/embed/EmbedApp.jsx,
             datamind/frontend/src/embed/EmbedSalesplayPlans.jsx,
             datamind/frontend/src/embed/embedApi.js,
             datamind/frontend/src/embed/embedSalesplaySubscription.js,
             datamind/frontend/src/pages/OnboardingWizard.jsx,
             datamind/frontend/src/utils/api.js

H. fix(billing): explain token exhaustion in chat instead of a bare limit message
   PROBLEM : Users who ran out of tokens mid-cycle got a flat "You've used
             all your tokens for this billing period." with no next step.
   FIX     : Chat now says the period's tokens are expired and names the two
             real options — upgrade the plan, or contact support for an
             add-on. Every query path already routes through
             check_ai_limit(), so one message covers all of them.
   FILES   : datamind/backend/billing.py

I. fix(embed): show Salesplay's show_price_text verbatim
   PROBLEM : Plan prices were built from product_price plus a currency
             symbol. Both inputs were wrong: product_price is Salesplay's
             base amount (5/10/25) rather than what the merchant is charged,
             and product_currency_symbol reads "$" even on LKR accounts. An
             LKR merchant saw "LKR10/mo" for a plan Salesplay itself prices
             at "LKR 1,654.93".
   FIX     : Render Salesplay's own preformatted show_price_text string and
             nothing else — no symbol logic of ours anywhere in the path.
   FILES   : datamind/frontend/src/embed/EmbedSalesplayPlans.jsx,
             datamind/frontend/src/embed/EmbedApp.jsx

J. fix(embed): allow payment only when is_valid_card_added is true
   PROBLEM : The card check OR-ed in billing_details_added, which flips true
             as soon as a merchant saves a billing address with no card
             attached. Those merchants saw "Subscribe", skipped the
             card_add_url redirect and hit a charge that could only fail
             (AUTHENTICATION_REQUIRED, and on some accounts a raw PHP fault
             from Salesplay). A merchant with no card and no card_add_url
             fell through to the payment screen entirely.
   FIX     : is_valid_card_added alone gates the flow — it is the only field
             that predicts whether /subscriptions/payment can succeed. No
             usable card means the card-add redirect (or a clear message if
             Salesplay sends no card_add_url), and the flag is re-checked
             inside the function that actually charges the card.
   FILES   : datamind/frontend/src/embed/embedSalesplaySubscription.js,
             datamind/frontend/src/embed/EmbedSalesplayPlans.jsx

K. fix(embed): show Salesplay's own error text, not our generic line
   PROBLEM : Every Salesplay proxy swallowed the upstream response body on a
             non-2xx and raised a flat "Could not reach Salesplay API.
             Please try again." A merchant hit by a real Salesplay fault had
             nothing to report and we had nothing to debug.
   FIX     : New _salesplay_error() pulls message / error.message /
             error.code from the response and falls back only when the body
             carries nothing usable. Wired through the profile,
             create-token, onboard, subscription-info and payment proxies.
             HTML fault pages stay suppressed so a stack trace can't render
             inside the widget, and 401 keeps its actionable "session
             expired, refresh the page" wording. Salesplay embed only.
   FILES   : datamind/backend/embed.py,
             datamind/backend/tests/test_salesplay_error.py,
             datamind/frontend/src/embed/EmbedSalesplayPlans.jsx

L. fix(embed): stop "Check again" flashing after a successful payment
   PROBLEM : On success the receipt screen set paidPending before awaiting
             the confirm, and that button's label keyed off a flag only the
             manual re-check sets. For a frame between the charge landing
             and the switch to chat, the merchant saw an idle "Check again",
             which reads as if the payment needed retrying.
   FIX     : Key the label off the shared busy flag so the button reads
             "Checking…" for that window and only offers "Check again" once
             everything has settled.
   FILES   : datamind/frontend/src/embed/EmbedSalesplayPlans.jsx

DB CHANGES  : None

.ENV CHANGES: YES — backend .env

  ADD (required — feature is inactive without it):
    AI_FLOW=agent

  ADD (optional — these match their code defaults, add for documentation):
    AI_FLOW_TEST_EMAILS=
    AGENT_MAX_ATTEMPTS=2
    REPORT_CACHE_REFINALIZE_MONTHS=2
    REPORT_CACHE_DEEP_REFINALIZE_DAYS=7

  KEEP (the Salesplay AI POS paid-plans layer is live — do NOT remove these):
    SALESPLAY_SUBSCRIPTION_BASE_URL            (backend .env)
    VITE_SALESPLAY_ACTIVATION_POLL_ATTEMPTS    (frontend .env)
    VITE_SALESPLAY_ACTIVATION_POLL_INTERVAL_MS (frontend .env)
    VITE_SALESPLAY_CARD_POLL_INTERVAL_MS       (frontend .env)
  An earlier draft of these notes listed the four keys above under REMOVE.
  That came from the beta line, where the paid-plans layer was reverted. It
  is NOT reverted on main/dev — removing them breaks the embed payment flow.

  DO NOT ADD:
    QA_ROUTES_ENABLED / QA_ROUTES_EMAILS — development only, see F.
