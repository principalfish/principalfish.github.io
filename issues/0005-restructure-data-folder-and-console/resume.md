# Resume point — read this first

## Where we are
0005 (GitHub #82), state `review`, draft PR #84. All 10 pieces and review fixes rounds 1 and 2 are committed and pushed (HEAD `c34e8c76`). The second re-check found no bugs and raised two low findings about the scan in `data/tests/test_core_package.py`; both are in `reviews/01-2026-11-09.md`.

## The pending decision (exact)
Whether to fix those two low findings in a third round, or leave them and go to `/publish`.

## What the next session does
If fixing: send both to the builder (`local_7fd00d5d-402a-4490-8fa8-e59f02ab44b2`) to run its Review fixes step, quoting the user. On its report: `planner.py ingest`, confirm the commit, re-run correctness with a fresh verifier, update the review file and index, add `Review fixes (round 3)` to `issue.md`, `gh issue edit`, save.
If leaving: record them as skipped with the user's reason, then suggest `/publish restructure-data-folder-and-console`.
The PR text must lead with the one-time editable install needed straight after merge.

## Don't
- Don't merge. Don't build in this chat. Run `planner.py` only from the main checkout. Don't regenerate the report without the user's go-ahead at the time.
