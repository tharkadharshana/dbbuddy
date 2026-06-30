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

A. feat(chat): add Clear conversation styling and AI disclaimer to main app
   PROBLEM : The main app's chat ("New conversation" link, no disclaimer)
             was inconsistent with the SalesPlay embed chat.
   FIX     : Renamed to "Clear conversation", styled bold + underlined to
             match the embed; added the "AI can make mistakes." disclaimer
             beneath the input.
   FILES   : datamind/frontend/src/pages/ChatPage.jsx

B. fix(embed): shorten SalesPlay header title to avoid truncation
   PROBLEM : "Ask Your Salesplay Data" truncated to "Ask Your..." in the
             widget header at narrow widths.
   FIX     : Replaced with "Ask Your AI"; original productTitle expression
             left commented for easy revert.
   FILES   : datamind/frontend/src/embed/EmbedChat.jsx

DB CHANGES  : None
.ENV CHANGES: None
