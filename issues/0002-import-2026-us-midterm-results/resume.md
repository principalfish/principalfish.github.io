# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" is building (GitHub #79). Pieces 1–5 and 7 are committed and pushed.
State checks: `git -C <build worktree> rev-parse --short HEAD` = `f90f26d3` on branch `import-2026-us-midterm-results`, clean tree, pushed (`git status -sb` shows no "ahead"); `python3 ~/.claude/lib/planner.py meta 0002` shows state building.
If anything differs, stop and tell the user.

Owned by the planner chat `planner: import-2026-us-midterm-results` (main checkout). Draft PR #80 (`gh pr view 80 --json state,isDraft`).

Builder `builder: import-2026-us-midterm-results` started 2026-11-07 (session `local_33a550dd-f3e8-4148-865d-3673210013ae`; `list_sessions` with `linked: true`). Open for Phil: merge order with 0001 — conflicts already exist (see plan.md Notes, overlap check); suggested: merge master in after #74 lands, before piece 8.

Piece 6 committed `bfe5dc77`. Merge order: merge master in after #74 lands, before piece 11 and the final review, then re-run the jsdom harness on both pages.

Piece 8 (with the seat ± change) approved by Phil ("Approve, commit") and relayed to the builder to commit and push. Check: `git log --oneline -1 origin/import-2026-us-midterm-results`. Once pushed, tick piece 8 in pr.md and `gh pr edit 80`. The hatch look needs a real-browser check after the piece 11 export.

Next step: checkpoint 9 (starts by raising the uncalled-baseline options). See `plan.md`.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.
