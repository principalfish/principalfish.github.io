# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" is building (GitHub #79). Pieces 1–5 and 7 are committed and pushed.
State checks: `git -C <build worktree> rev-parse --short HEAD` = `f90f26d3` on branch `import-2026-us-midterm-results`, clean tree, pushed (`git status -sb` shows no "ahead"); `python3 ~/.claude/lib/planner.py meta 0002` shows state building.
If anything differs, stop and tell the user.

Owned by the planner chat `planner: import-2026-us-midterm-results` (main checkout). Draft PR #80 (`gh pr view 80 --json state,isDraft`).

Builder `builder: import-2026-us-midterm-results` started 2026-11-07 (session `local_33a550dd-f3e8-4148-865d-3673210013ae`; `list_sessions` with `linked: true`). Open for Phil: merge order with 0001 — conflicts already exist (see plan.md Notes, overlap check); suggested: merge master in after #74 lands, before piece 8.

Piece 6 committed `bfe5dc77`. Merge order: merge master in after #74 lands, before piece 11 and the final review, then re-run the jsdom harness on both pages.

Piece 8 committed `9ec98204` and pushed; PR #80 ticked. Piece 9 started on Phil's go-ahead ("Start piece 9"); uncalled baseline seats are option A. Pieces 1–10 and the piece 11 docs (`613ac558`) committed and pushed; PR #80 ticked. Merge order revised by Phil: don't wait for #74. Master merged in as `b51ab61c` and pushed. `/review self` running in this chat (checks on the whole branch; coverage report requested from the builder). Then `/review self import-2026-us-midterm-results` in this chat, then the live steps (README section 12), each with Phil's go-ahead. The hatch look needs a real-browser check after the piece 11 export.

Next step: present the review findings for Phil to pick fixes; checkpoint 11's live steps after. See `plan.md`.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.
