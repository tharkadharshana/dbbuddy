<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->

CHANGES IN THIS PATCH
─────────────────────

All four are export-path fixes. A merchant asked for "today's sales
summary" as a PDF: the chat answered correctly with eleven figures, the
spreadsheet carried five, and the PDF had a single column under the full
summary's title. Same root cause, three symptoms — plus a guard so it
cannot come back silently.

A. fix: an answer spanning several reports exports all of it
   PROBLEM : "Today's sales summary" calls TWO reports — sales_summary for
             the money, receipts for the count and average ticket. Both
             write to one last_result slot and _set_last_result overwrote
             it, so the export carried whichever report ran last.
             The chat was right the whole time: it writes its prose from
             the tool return values, which were complete. Only the export
             read the slot. That is why the gap survived unnoticed — the
             loudest surface was the correct one.
             So the spreadsheet held the receipts report's five columns
             verbatim, and the document — whose layout named sales_summary
             columns — rendered one column, because the rest were no longer
             in `columns` and were correctly dropped as unfillable.
   FIX     : Consecutive METRIC results merge into the one row. Row-bearing
             results (get_report_detail, top-N tables) still replace: many
             rows about different things cannot merge into a summary row
             without inventing a table nobody queried. Switching shape
             resets, since neither shape carries the other's meaning.
   EFFECT  : The merged row is one row before and after, so row_count,
             billing (_charge_op) and the answer_trace log are unchanged.
             The agent path passes no columns/data to save_message, so
             stored conversation history is untouched. Only the export
             payload reads the extra keys.

B. fix: a margin prints as 23.12%, not 0.2312%
   PROBLEM : Ratio metrics are computed as num/den, so a 23.12% gross
             margin sits in the row as 0.2312. The chat is unaffected — the
             model reads the fraction and writes "23.12%" itself. A file
             renderer has no such judgement and printed "0.2312%" beside a
             chat that said 23.12%.
   FIX     : Scaled at the export boundary, applied once in DownloadButton
             so the document, CSV and spreadsheet agree.
             NOT fixed in report_cache: that number is what every chat
             answer is written from, and the chat is already correct —
             changing it there would have made the chat say 2312%.
             Which columns are fractions comes from the registry (ratio
             metrics whose label reads as a percentage), never from the
             column name: a value below 1 is equally consistent with a
             fraction and with a genuinely small percentage, and
             avg_receipt_value is a ratio that must NOT be scaled. A
             hand-written SQL column merely named "..._pct" is left alone
             for the same reason.

C. fix: a percentage column is a level, not a change
   PROBLEM : The document renderer stamped a "+" on any column matching
             pct/rate — logic written for period-over-period deltas. A
             gross margin printed as "+18.96%" reads as growth rather than
             the margin itself.
   FIX     : The sign now needs a delta-shaped column name (change, delta,
             growth, _diff). Percentages render to two decimals like every
             other formatted figure.

D. fix: a stale document layout is rejected instead of printed
   PROBLEM : _clean_document_spec drops any column the loaded rows cannot
             fill, so a page never renders "undefined". But dropping MOST
             of a layout is not a stray field — it means the model is
             describing a different result than the one loaded. That is
             what turned bug A into a blank-looking page rather than an
             error: a one-column document under a full summary's title
             reads as a working document, not a failure.
   FIX     : Losing over half the requested columns now raises, naming the
             missing columns and what is actually available, so the model
             re-runs the query in the same reply. This is the change that
             stops the class of bug rather than the instance.

   ACTION  : None. No schema change, no new env key, no new dependency.

E. feat: a refused answer now offers a way to subscribe
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

SCOPE
─────
The export fixes (A-D) change nothing on the answer path: the chat's
wording, figures, billing and stored history are untouched; they change how
an already-answered result is assembled into a file.

E adds one field (`upgrade_offer`) to the response and one instruction to
the prompt, and only when a feature is actually denied. An answer that
refuses nothing carries neither. 305 backend tests and the frontend
self-check pass.

KNOWN GAP
─────────
Print-to-PDF from inside the widget iframe is still unverified in Safari.
It works in a normal browser tab; a blocked popup reports "allow pop-ups"
rather than failing silently.
