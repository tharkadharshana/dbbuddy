<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->

CHANGES IN THIS PATCH
─────────────────────

One screenshot showed all three defects at once: a generated document read
"$ 4,303.89" where the chat beside it said "LKR 4,303.89", and printed
Refunds as "1,263" where the chat said "LKR 1,263.05". Same figures, same
answer, three different ways of writing them.

A. fix: a document shows the merchant's own currency
   PROBLEM : getUserLocale() read the localStorage key 'dm_embed_user', but
             the widget writes its user to a PARTNER-KEY-SUFFIXED key
             (embedStorage.js — deliberately, so two brands served from one
             origin cannot share a token). The lookup therefore always
             missed in the embed, and every amount fell through to
             formatCurrency's default '$'.
             The chat was unaffected because its figures are the model's own
             prose, formatted server-side from the tenant's locale; only the
             document calls formatCurrency. That is why the discrepancy
             showed up on the printed page and nowhere else.
   FIX     : Read the suffixed key the widget actually wrote, then the plain
             one. Nothing is hardcoded: the symbol still comes from the
             merchant's own synced SalesPlay profile, so a future partner
             needs no code change.

B. fix: refunds and single-product amounts print as money
   PROBLEM : _is_money_column missed "refunds" — it carries no money
             fragment of its own — and the report registry's SINGULAR
             net_sale / gross_sale, because the fragment list held the
             plural "sales". Both then rendered as bare counts: a weekly
             product document printed "768" for LKR 768.02.
   FIX     : Added "sale", "refund", "tip" and "surcharge". Since "sale" is
             a substring match it also hits sale_date, so date-ish tokens
             are now excluded alongside the existing count tokens — a date
             is not a count, but it is certainly not money.
             The frontend heuristic moved in step with the backend list: it
             is the fallback for loaded history snapshots, which carry no
             money_cols, and a divergence there is what these two lists
             existed to prevent.

C. fix: an amount keeps its cents
   PROBLEM : The document renderer forced 0 decimals on every non-money
             number, so any amount that slipped past the check in B lost its
             cents outright rather than merely losing its symbol.
   FIX     : A whole number still prints whole (a count of 5 is "5", not
             "5.00"); a value with a real fractional part keeps it.

   ACTION  : None. No schema change, no new env key, no new dependency.

SCOPE
─────
The chat is untouched — its figures never went through this path. These are
render-time fixes to how an already-answered result is written into a file,
plus the money/count column rule shared by the file and the on-screen table.
308 backend tests and the frontend self-check pass.

KNOWN GAP
─────────
Print-to-PDF from inside the widget iframe is still unverified in Safari. It
works in a normal browser tab; a blocked popup reports "allow pop-ups"
rather than failing silently.
