<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->

CHANGES IN THIS PATCH
─────────────────────

A. feat: merchants can get their data out of the chat
   PROBLEM : A merchant could see an answer but had no way to keep it.
             There was no export path anywhere — no CSV, no spreadsheet,
             no document — while the SalesPlay plan checklist had been
             advertising "Reports and charts downloadable" for months.
   FIX     : A new export_data MCP tool the model calls ONLY when the
             merchant asks ("send me that as excel", "can I download
             this"). Formats: CSV, spreadsheet, chart PNG, and a
             printable document the browser saves as a PDF. No file is
             offered unless it was asked for — an unasked answer carries
             no export payload and renders no button.
             Nothing is stored: the file is built in the browser from
             rows already in the response. No artifacts directory, no
             signed URLs, no cleanup job. The trade-off is that there is
             no re-download later; a reloaded conversation holds only the
             10-row snapshot, so asking again regenerates the file.

B. feat: printable documents (invoice-style pages, statements)
   PROBLEM : Merchants asked for documents they could hand to someone —
             an invoice-shaped page, a statement — not just raw data.
   FIX     : The model describes a LAYOUT (title, which columns are
             header fields, which are line items, which to total) and the
             page is filled in from the rows it actually queried. The
             model never supplies a figure: totals are summed in the
             browser from the data. A model retyping amounts into a
             document is how a wrong total reaches a merchant's customer,
             so this is structural rather than a prompt instruction.
             A "Tax Invoice" title is refused server-side. These records
             carry no per-line tax, no billing address and no
             registration numbers — the merchant's POS issues tax
             documents and this must not claim to be one. The footer says
             "Generated from your sales records".

C. feat: copy button on every answer, with real tables
   PROBLEM : Answers could only be re-typed. And a copied markdown table
             pasted into Outlook or Teams arrived as a wall of pipes.
   FIX     : A copy button beside the vote buttons. The clipboard carries
             text/html AND text/plain, so rich-text targets paste a real
             formatted table while plain-text targets get the markdown.
             Tables the model wrote inside its own prose are converted
             too, using the same parser that renders them on screen, so a
             pasted answer cannot drift from the displayed one.

D. feat: trials no longer include the premium features
   PROBLEM : A trial was granted everything its plan allowed, so
             downloads, forecasting and anomaly detection were free for
             the whole trial period. There was nothing left to convert on.
   FIX     : check_plan_feature now denies download_export, forecast and
             anomaly_detection while status = 'trial'.
             Deliberately a named set rather than a blanket trial block:
             partner_api, external_api and web_widget are how a merchant
             is integrated at all, and cutting those off mid-trial would
             break the widget rather than upsell it.

   ACTION  : download_export is in the _PLAN_FEATURE_GATE seed, so
             bootstrap_billing_tables inserts its row on restart — no
             manual SQL needed. BUT the seed only covers the Standard
             plan. If this environment has live Growth or Pro
             subscribers, add rows for those plans or they will be
             denied:
               INSERT IGNORE INTO plan_feature_gates (feature, plan_name)
               VALUES ('download_export','Growth'),
                      ('download_export','Pro');
             Note check_plan_feature FAILS OPEN for a feature with no
             gate rows at all — a missing row means everyone gets the
             feature, not nobody.

E. feat: agent answers now record what they ran
   PROBLEM : The agent flow saved its messages with sql_query null and
             row_count zero, while the legacy flow filled both. Agent
             conversations had no record of what was actually executed.
   FIX     : AgentResult carries the last SQL the tools ran (None for
             answers needing no data) and passes it, with the row count,
             to save_message.
   NOTE    : row_count is the last tool call's row count, so an answer
             whose final step was a forecast can record a stale figure.
             Fine for debugging; do not build billing on it.
             Side effect worth expecting: scripts/generate_reports.py
             scores success as "row_count>0 OR sql_query IS NOT NULL", so
             agent answers that previously counted as failures will now
             count as successes. Trial-success numbers will rise — that
             is a data correction, not a regression.

F. fix: the printable document carries no product branding
   PROBLEM : The generated document showed the product name in its header
             and again in its footer. A merchant hands this to their own
             customer — our name, or the partner's, has no business on it.
             Separately, a header field whose value differs per row was
             printed as row one's value: a document showed "Total spent
             LKR 7,000.00" above a table totalling LKR 17,095.20, because
             it had taken the first line's figure and labelled it a total.
   FIX     : No brand appears anywhere on the page. Filenames still carry
             the brand — that is the merchant's own filing, not the
             document. Header fields are now dropped unless their value is
             identical on every row, so a varying column cannot be printed
             under a label that reads as a total; the real total is still
             the summed footer row. The footer gained a time alongside the
             date, and the line "Content is AI generated and unverified."
   ACTION  : None required.

G. feat: a refused answer now offers a way to subscribe
   PROBLEM : A trial merchant who asked to download something was told it
             needs a higher plan and left there. Nothing routes a merchant
             who still HAS access to the plans screen — that path only
             fires for someone already blocked — so the refusal was a dead
             end with no way to act on it.
   FIX     : When the model turns something down on plan grounds it ends
             the reply with a marker, which is stripped before the text
             reaches the merchant, the saved message or the sanitiser, and
             surfaced as `upgrade_offer` on the response. The widget shows
             a "View plans" button that opens the plans screen that
             already exists; dismissing it returns to the conversation
             rather than closing the widget on someone mid-question.
             A marker rather than matching the wording: the model phrases
             the refusal freshly every time, so a phrase list would both
             miss real refusals and fire on answers that merely discuss
             pricing. The instruction is only added to the prompt when a
             feature is actually denied.
   ACTION  : None required.

H. fix: UI corrections
   - The token meter ("1.36M / 2M") is hidden across the main app,
     including the Query page, which was the last one still rendering it
     (and rendering it broken — it passed value/onChange where the
     component expects sub, so it had been drawing nothing). Token
     consumption is an internal billing unit; a merchant has no use for
     it and no way to act on it. It stays on Billing/Usage and in the
     embed. Commented rather than deleted, so restoring it is
     uncommenting one block per page. The embed widget keeps its own
     near-limit warning, which is a genuine heads-up before a merchant
     runs out.
   - The raw record count is gone from the Data Sources connection card.
     Only the last sync time means anything to a merchant there.
   - A sync in progress is green, not amber — amber read as a warning
     about the healthy path.
   - A finished sync now confirms itself ("Sync Complete", full bar,
     record count) for a few seconds. Previously the bar simply vanished,
     which looked identical to a sync that had died.
   - Downloaded files carry the partner's own brand name, never ours. One
     build serves every brand, so a hardcoded name would have put our
     product in a whitelabel merchant's downloads folder.

PARTNER SURFACES
────────────────
No changes are needed on the SalesPlay, Sellmo or any future partner
side. One shared chat surface and one shared download button; the
entitlement rides the /billing/subscription call the widget already
makes. Deploying this is enough.

KNOWN GAP
─────────
Print-to-PDF from inside the widget iframe is unverified. It works in a
normal browser tab; the cross-origin iframe case has not been tested. The
partner iframe snippet sets no sandbox attribute, so it should be
permitted — and if a browser blocks it the button reports "allow pop-ups"
rather than failing silently.
