# Resume point — read this first

## Where we are
0005 (GitHub #82), state `review`, draft PR #84. All 10 pieces are committed and pushed (latest `413f28eb`). `/review self` round 1 is recorded in `reviews/01-2026-11-09.md`. The user chose to fix findings 2 and 3; the builder is applying them.

## The pending decision (exact)
Approval of review fixes round 1 (13 files staged, uncommitted, report regenerated with the user's go-ahead), including whether the wider docstring fix for `~db.Database` and `config.NYT_CACHE_DIR` stays.

## What the next session does
On the builder's report: `planner.py ingest`, confirm the fix commits, re-run the checks that raised the findings (correctness) on the new diff with a fresh verifier, update the review file and index, add the `Review fixes (round 1)` line to `issue.md` and `gh issue edit`, save, then suggest `/publish`. The PR text must lead with the one-time editable install needed straight after merge.

## Don't
- Don't merge. Don't build in this chat. Run `planner.py` only from the main checkout. Don't regenerate the report without the user's go-ahead at the time.
