# Resume point — read this first

## Where we are
0005 (GitHub #82), state `building`, draft PR #84. Pieces 1 and 2 are committed (`4b0f4475`, `0539b23f`). Piece 3 is staged and uncommitted in `.claude/worktrees/restructure-data-folder-e05123`, branch `claude/restructure-data-folder-e05123`, paused for the user's review.
State check: `git -C .claude/worktrees/restructure-data-folder-e05123 diff --cached -M --shortstat` shows 79 files changed.

## The pending decision (exact)
Piece 3 approval.

## What the next session does
Relay the verdict to the builder (session `local_7fd00d5d-402a-4490-8fa8-e59f02ab44b2`), quoting the user's words, and ask for a full `run_tests.sh` on the final tree before committing: the last reported full run predates the final test additions. After it commits and pushes: tick the checklist and brief piece 4.

## Don't
- Don't approve for the user. Don't build in this chat.
