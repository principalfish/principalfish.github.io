# Resume point — read this first

## Where we are
Issue 0002 "Import 2018–2026 US House and Senate results from NYT data" (GitHub #79) is ready. All 14 pieces are built and pushed (HEAD `3efe8c83`, check with `git rev-parse --short origin/import-2026-us-midterm-results`). The live steps are done, and draft PR #80 carries the final description (`gh pr view 80 --json state,isDraft`). Marking it ready and merging are Phil's.

No pending decision. Next: Phil merges #80. Then `/issues` marks the issue done, and the builder can be retired (`planner.py check-builder import-2026-us-midterm-results`, then archive on Phil's yes).

Owned by the planner chat `planner: import-2026-us-midterm-results` (main checkout). Restore point for the live DB before the imports: `/home/philiph/dbs/elections.pre-us-nyt-import-20261107.db`. The latest archive with 0002's elections (ids 106320–106328) is `~/dbs/elections/elections-2026-11-07-210040.db.gz` (0001's later rerun touched only its own run). Alaska 2026's override (`data/scripts/export/seat_overrides.json`) must be deleted once the ranked-choice final round is imported. See `plan.md` Notes.

## Don't
- Run imports, migrations or exports against the live DB without Phil's explicit go-ahead at the time.
- Try to get past the NYT HTML captcha; use only the static CDN JSON.
