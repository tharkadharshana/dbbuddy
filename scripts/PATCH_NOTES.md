<!-- Edit this file for each new patch — describe PROBLEM/FIX per change.
     create_deploy_patch.py prepends the version/branch/date header and appends
     auto-generated ENV KEYS / files-changed / commit-log sections, so don't
     duplicate that information here. -->

CHANGES IN THIS PATCH
─────────────────────

A. fix(billing): stop silently auto-granting trials at account creation
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

B. fix(billing): explain token exhaustion in chat instead of a bare limit message
   PROBLEM : Users who ran out of tokens mid-cycle got a flat "You've used
             all your tokens for this billing period." with no next step.
   FIX     : Chat now says the period's tokens are expired and names the two
             real options — upgrade the plan, or contact support for an
             add-on. Every query path already routes through
             check_ai_limit(), so one message covers all of them.
   FILES   : datamind/backend/billing.py

C. fix(embed): show Salesplay's show_price_text verbatim
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

D. fix(embed): allow payment only when is_valid_card_added is true
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

E. fix(embed): show Salesplay's own error text, not our generic line
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

F. fix(embed): stop "Check again" flashing after a successful payment
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
.ENV CHANGES: None
