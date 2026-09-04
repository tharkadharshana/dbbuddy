<!-- OPTIONAL prose preface for the next release's PATCH_NOTES.txt.
     create_deploy_patch.py already generates, from git: the version/branch/commit
     header, the ENV KEYS added/removed block, the grouped commit log and the list
     of changed files. Don't duplicate any of that here — use this file only for
     PROBLEM/FIX context that git can't infer, or leave it empty. -->

CHANGES IN THIS PATCH
─────────────────────

A. fix: the onboarding screens print the product name once, not twice
   PROBLEM : The setup/sync screen showed the brand's wordmark logo and then
             the product name again as text directly beneath it, so a
             merchant saw "SalesPlay AI" twice while waiting for their
             workspace.
             The consent screen already guards against this — eb31114 fixed
             it there in August with the note that a wordmark logo already
             carries the name — but the block covering every OTHER phase
             (loading, sync, plans, error) rendered both unconditionally and
             was missed.
   FIX     : The same guard, applied to that block: the title renders only
             when the brand has no logoUrl.
             This is not a blanket removal. A brand with no logo falls back
             to a single-letter tile, which carries no name, so those brands
             still need the text — which is exactly the condition the
             consent screen already uses.
   ACTION  : None. No schema change, no new env key, no new dependency.

SCOPE
─────
One conditional in one embed component. No backend change, nothing on the
answer or export path.

KNOWN GAP
─────────
Print-to-PDF from inside the widget iframe is still unverified in Safari. It
works in a normal browser tab; a blocked popup reports "allow pop-ups"
rather than failing silently.
