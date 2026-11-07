# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" is building (GitHub #79). Pieces 1–5 and 7 are committed and pushed.
State checks: `git -C <build worktree> rev-parse --short HEAD` = `f90f26d3` on branch `import-2026-us-midterm-results`, clean tree, pushed (`git status -sb` shows no "ahead"); `python3 ~/.claude/lib/planner.py meta 0002` shows state building.
If anything differs, stop and tell the user.

Owned by the planner chat `planner: import-2026-us-midterm-results` (main checkout). Draft PR #80 (`gh pr view 80 --json state,isDraft`).

Pending: the builder `builder: import-2026-us-midterm-results` is proposed as a card for Phil to start (`list_sessions` shows it once started). Open for Phil: merge order with 0001 — conflicts already exist (see plan.md Notes, overlap check); suggested: merge master in after #74 lands, before piece 8.

Next step: checkpoint 6 in plan.md (House maps 24/25); ogr2ogr and mapshaper are installed. See `plan.md`.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.
