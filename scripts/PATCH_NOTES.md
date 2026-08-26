<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->
<!-- ponytail: the auto env-key diff supersedes hand-written ".ENV CHANGES"; drop
     that section once you trust it. -->

CHANGES IN THIS PATCH
─────────────────────

A. feat: free launch period behind SUBSCRIPTION_FREE
   PROBLEM : Launching to SalesPlay merchants meant showing prices and a
             card form on day one. We wanted a two-week period where every
             merchant simply gets the trial, with no pricing anywhere and
             no way to be charged — and we wanted to end that period
             without a code change, a rebuild or a redeploy.
   FIX     : One backend .env boolean, read at import in billing.py and
             served to both clients on endpoints they already call
             (GET /embed/context and GET /v1/billing/subscription).
             While on:
               - the widget consent screen shows a single "Try <app>"
                 button; tier cards and the explore accordion are not
                 rendered and the pricing fetch is skipped
               - a merchant without access is never routed to the plans
                 screen. Never subscribed -> the trial is granted outright
                 and they land in chat. Otherwise they get an explanation
                 screen keyed on blockReason, with a retry button when the
                 access check itself failed
               - the main app billing page shows a trial button instead of
                 plan tiers and the add-on cart
               - the BETA badge shows in the sidebar, the widget chat
                 header and the consent screen
               - the embed payment proxy, the embed order-preview proxy
                 and POST /v1/billing/subscribe all return 403
   FILES   : datamind/backend/billing.py, embed.py, main.py,
             datamind/frontend/src/embed/EmbedApp.jsx,
             EmbedSalesplayAutoInit.jsx, EmbedChat.jsx,
             EmbedFreeBlocked.jsx (new),
             datamind/frontend/src/components/BetaBadge.jsx (new),
             Sidebar.jsx, App.jsx, pages/BillingPage.jsx
   ACTION  : REQUIRES SUBSCRIPTION_FREE=true in the server .env — see
             ENV KEYS below. The code default is false; without this line
             the patch deploys as a no-op and the normal paid flow runs.
   ENDING  : To end the free period, set SUBSCRIPTION_FREE=false and
             restart the backend. No rebuild, no redeploy, no SQL. Users
             mid-trial are unaffected — their trial expires into the
             normal paid flow on its own.

B. fix(billing): paid activations were landing on a retired 200-token plan
   PROBLEM : Every merchant who completed a payment was activated on
             'Starter' (is_active=0, 200 tokens) instead of 'Standard'
             (25,000 tokens) — a 125x shortfall, paid for. Two faults
             combined. The frontend mapped tier position to a hardcoded
             subscription_plans.id ([1,2,3]) and shipped it as
             internal_plan_id; on the current database those ids are the
             retired tiers, not Standard. And subscribe_to_plan looked the
             id up with no is_active filter, so a retired plan activated
             silently after a real charge. This affected new paying
             customers as much as existing ones — only the TRIAL path was
             correct, because it resolves by name.
   FIX     : subscribe_to_plan's plan_id is now optional; omitted, the live
             plan is resolved BY NAME exactly as start_trial does. Both
             lookup branches now require is_active = 1, so a retired plan
             raises loudly instead of activating silently. The frontend
             constant is deleted and internal_plan_id is no longer sent —
             it remains accepted-and-ignored on the request model so an
             iframe cached from before this patch still completes its
             charge instead of failing validation after the card was hit.
             internal_period_days is unchanged; the billing cycle really is
             the merchant's choice.
   FILES   : datamind/backend/billing.py, embed.py,
             datamind/frontend/src/embed/EmbedSalesplayPlans.jsx
   ACTION  : None required. No schema migration and no data change.
   NOTE    : This fix MUST be deployed before SUBSCRIPTION_FREE is set back
             to false, or the first paying customer after the free period
             lands on Starter again.

C. fix(embed): the pre-onboarding plan preview always returned 401
   PROBLEM : GET /embed/salesplay/subscription/info depended on
             current_user, so the consent screen's "Explore plans" toggle
             401'd every time. That call is made with only partner_key +
             aat, BEFORE any DataMind account or dm_embed_token exists —
             a merchant looking at prices has not signed up yet.
   FIX     : New optional_current_user in auth.py returns None when no
             Authorization header is present, and still raises on a
             present-but-invalid token. Safe because the bearer scheme is
             HTTPBearer(auto_error=False). The endpoint's Salesplay-expired
             sync-down (cancel_subscription) is now guarded by `if user`,
             so an anonymous caller gets pricing and no account state is
             touched.
   FILES   : datamind/backend/auth.py, embed.py
   ACTION  : None required.

D. fix(sync): the scheduler advisory lock was not actually held
   PROBLEM : Only one process may own the integration sync scheduler —
             GET_LOCK('datamind_scheduler') is what guarantees that. It
             guaranteed nothing. _try_acquire_scheduler_lock borrowed a
             POOLED connection and deliberately never closed it, but kept
             no reference to it, so Python garbage-collected the connection
             straight back into the pool, the session reset, and the lock
             silently dropped. Observed with two backends live on one
             database: BOTH logged "acquired DB advisory lock", and
             IS_USED_LOCK('datamind_scheduler') returned NULL.
             Consequence: every backend sharing a database runs its own
             scheduler and syncs every integration once per instance —
             duplicate provider API calls, multiplied rate-limit
             consumption, and concurrent writers racing on
             integration_records upserts.
   FIX     : The lock connection is parked at module scope so it is never
             collected. Two follow-on hazards fixed in the same function:
             re-acquire is re-entrant (the watchdog's relaunch would
             otherwise lose GET_LOCK to our OWN parked session, exit, and
             stop syncing for the life of the process while no other
             instance could take over), and the tick loop pings the session
             every 60s so MySQL's wait_timeout cannot close it and free the
             lock unnoticed. The ping uses reconnect=False deliberately: a
             reconnect opens a new session that does NOT hold the lock.
   FILES   : datamind/backend/integrations.py
   ACTION  : None required. Matters most if you ever run more than one
             backend against one database.
   COST    : The parked connection never returns to the pool, so the
             scheduler-owning worker runs at DB_POOL_SIZE - 1.

E. refactor(ui): one source of truth for the token display multiplier
   PROBLEM : TDM = 10_000 and fmtTok were copy-pasted, byte for byte, into
             five files. Changing the display scale in one place would have
             left four screens disagreeing about how many Tokens a plan
             grants.
   FIX     : Extracted to datamind/frontend/src/formatTokens.js and
             imported by all five. Behaviour is unchanged — TDM is still
             10_000 and every rendered number is identical.
   FILES   : datamind/frontend/src/formatTokens.js (new),
             components/UI.jsx, pages/UsagePage.jsx, pages/BillingPage.jsx,
             embed/EmbedSalesplayAutoInit.jsx, embed/EmbedOnboarding.jsx
   ACTION  : None required.

F. chore(dev): dev-server proxy target is configurable
   PROBLEM : vite.config.js hardcoded http://localhost:8000 in all seven
             proxy rules, so a second dev server could not be pointed at a
             second backend.
   FIX     : The rules read VITE_BACKEND, defaulting to localhost:8000.
   FILES   : datamind/frontend/vite.config.js
   ACTION  : None. This CANNOT affect this patch's bundle: `vite build`
             ignores the `server` block entirely, and production resolves
             its backend through VITE_API_URL in the per-target env file.
             Verified — building with and without VITE_BACKEND set produces
             identical content-hashed asset filenames.

G. feat(branding): multi-brand — one deployment serves many partners
   PROBLEM : Brand identity was compiled into the frontend bundle and the
             backend env, so a second partner meant a second build and a
             second deployment. Account identity was the email alone, so
             the same address could not belong to two merchants under two
             different partners.
   FIX     : A brand is now one embed_partners row. Identity moved from
             `email` to `(email, partner_key)` with a MySQL-generated
             `account_key` that every child table references. The widget
             renders entirely from the brand row (name, logos, colours,
             links, free/paid), and per-brand `api_config` lets one
             deployment talk to different provider instances.
             `table_prefix` / tenant_id is NEVER rewritten, so no merchant
             data moves and nothing resyncs.
   FILES   : datamind/backend/embed.py, auth.py, main.py, partner_api.py,
             integrations.py, v1.py, brands/*.json,
             scripts/add_brand.py, scripts/census_user_brands.py,
             scripts/migrate_multi_brand_identity.py,
             datamind/frontend/src/embed/* (BrandLogo.jsx, embedBranding.js,
             embedApi.js, embedStorage.js and every screen)
   ACTION  : REQUIRES THE DATABASE MIGRATION. See DATABASE_CHANGES.md in
             this patch — it is not optional and it needs a maintenance
             window. Deploying this code against an unmigrated database
             breaks login: auth.py selects users.account_key, which does
             not exist until the migration runs.

H. fix(embed): logos render at their own aspect ratio
   PROBLEM : BrandLogo forced width AND height to the same value, so every
             logo was squeezed into a square. A wide wordmark came out as a
             sliver (a 4:1 mark in the 24px slot rendered about 24x6).
             Separately, screens printed the product name as text beside a
             logo that already contains it, so the name appeared twice.
   FIX     : `size` is now a height and the width follows the artwork;
             `square` is opt-in for the fixed tiles that need it. Titles
             adjacent to a logo are hidden when the brand supplies one, and
             still shown for brands that do not (Sellmo, Loyverse), which
             are otherwise unaffected. The fallback initial tile floors its
             own corner radius so a wordmark's radius=0 cannot flatten it.
             The MAIN APP was a separate bug with the same symptom:
             components/Logo.jsx had the old mark hardcoded inline as SVG
             path data, so replacing the .svg file changed the widget and
             nothing else. It now renders the same file the widget does, so
             a future logo change is one file replacement. favicon.svg was a
             third copy of that artwork and now matches the square mark (a
             wordmark is unreadable at 16px).
   FILES   : datamind/frontend/src/embed/BrandLogo.jsx, EmbedApp.jsx,
             EmbedChat.jsx, EmbedOnboarding.jsx, EmbedSalesplayAutoInit.jsx,
             EmbedSalesplayPlans.jsx,
             datamind/frontend/src/components/Logo.jsx, Sidebar.jsx,
             pages/AuthPage.jsx, ChatPage.jsx, OnboardingWizard.jsx,
             datamind/frontend/public/favicon.svg,
             datamind/frontend/public/brand/salesplay-ai-logo.svg (new
             wordmark), salesplay-mark.svg (new — the previous square,
             preserved for the chat avatar and the favicon)
   ACTION  : ONE SQL UPDATE per environment — logo_mark_url must move to
             the new file. See DATABASE_CHANGES.md section 3. Salesplay
             only; do not touch other brands' rows.

DEPLOYING MORE THAN ONE INSTANCE ON ONE DATABASE
────────────────────────────────────────────────

Running a paid instance (SUBSCRIPTION_FREE=false) beside a beta instance
(true) against a shared database was validated end to end by
scripts/qa_salesplay.py — 33 checks, including the flip. Two things to know:

  1. Item D above is a prerequisite. Without it both instances run the sync
     scheduler and every integration syncs twice.

  2. bootstrap_integration_tables clears 'syncing' with NO tenant filter, so
     restarting either instance marks the OTHER instance's in-flight syncs
     as errored. It self-heals on the next scheduler tick (errored rows are
     retried with a backoff multiplier), but restart windows should be
     coordinated across instances rather than treated as independent.

Full runbook: docs/qa-salesplay-embed-suite.md

OPTIONAL SQL — neither is required for this deploy
──────────────────────────────────────────────────

1. Plan id tidy. On this database subscription_plans.id is varchar(50)
   with no auto-increment (the bootstrap declares INT AUTO_INCREMENT, but
   CREATE TABLE IF NOT EXISTS never corrects an existing table), so
   'Standard' was inserted with an empty-string id. New rows therefore
   store plan_id = 0 and the join only matches because MySQL coerces ''
   to 0. Functional but fragile; the code fix above does not depend on it.

     START TRANSACTION;
     UPDATE subscription_plans SET id = '4' WHERE name = 'Standard' AND id = '';
     UPDATE user_subscriptions SET plan_id = 4 WHERE id = 106;
     COMMIT;

   Both statements or neither — changing the plan id alone orphans that
   user, because 0 stops matching.

2. Two more weeks for already-expired trials. Gate on the DATE, not the
   status label: lapsed rows are only relabelled 'expired' when something
   reads them. The subquery restricts the update to each user's latest row
   so older rows are not resurrected.

     CREATE TABLE user_subscriptions_bak_20260820 AS SELECT * FROM user_subscriptions;

     UPDATE user_subscriptions us
     JOIN (SELECT user_email, MAX(id) AS id FROM user_subscriptions GROUP BY user_email) l
       ON l.id = us.id
     SET us.status       = 'trial',
         us.period_start = CURDATE(),
         us.period_end   = CURDATE() + INTERVAL 14 DAY
     WHERE us.period_end < CURDATE()
       AND us.status <> 'cancelled';

   Two deliberate side effects: moving period_start to today resets token
   counters (subscription_usage is keyed on user_email + period_start), and
   users who actively cancelled are excluded. Drop the last clause if they
   should be included.

VERIFY AFTER DEPLOY
───────────────────

  curl -s "https://<host>/embed/context?pk=<partner_key>" | grep subscription_free

Expect "subscription_free": true. Then open the widget as a BRAND-NEW
merchant — an existing account will not exercise the paths that changed.
Expect one "Try <app>" button, a BETA badge, no tier cards, and a landing
straight in chat.

FULL WRITE-UP
─────────────

docs/2026-08-20-subscription-free-launch-period.md (and the same content as
a rendered page at docs/2026-08-20-subscription-free-runbook.html) covers
the design decisions, the commit-by-commit file changes, the live database
evidence behind fix B, the runtime test results, and the shared-database
approach this replaced.
