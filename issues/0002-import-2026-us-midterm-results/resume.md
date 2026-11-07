# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" (GitHub #79, draft PR #80): all 14 pieces are built, committed and pushed, the live steps (migration, imports, Current Senate, exports) are done, and Phil has confirmed the browser check. The state is review.
State checks: `git -C <build worktree> rev-parse --short HEAD` = `3efe8c83` on branch `import-2026-us-midterm-results`, clean tree, pushed (`git status -sb` shows no "ahead"); `python3 ~/.claude/lib/planner.py meta 0002` shows state review.
If anything differs, stop and tell the user.

Owned by the planner chat `planner: import-2026-us-midterm-results` (main checkout). Draft PR #80 (`gh pr view 80 --json state,isDraft`).

Restore point for the live DB before the imports: `/home/philiph/dbs/elections.pre-us-nyt-import-20261107.db`.

Next step: `/review self` has run once (`reviews/01-2026-11-07.md`, findings fixed in `98269358`); later work (pieces 12–14) may warrant another, then `/publish`. See `plan.md` Notes for everything decided during the build.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.
