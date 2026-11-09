# Resume point — read this first

## Where we are
0005 (GitHub #82), state `review`, draft PR #84. All 10 pieces are committed and pushed (latest `413f28eb`) on `claude/restructure-data-folder-e05123`, worktree `.claude/worktrees/restructure-data-folder-e05123`. The record is saved.

## The pending decision (exact)
Whether to run `/review self`.

## What the next session does
`/review self restructure-data-folder-and-console`, then `/publish` after it. The user merges. 0006 builds after the merge.

## Open for review
Dead helpers used only by archived scripts: `ParsedPollWithBases` and `HasRegionalShares` in `importers/polls/bases.py`, YouGov's `regional_shares()`.
The PR text must lead with the one-time editable install needed in the main environment right after merge, or the hourly restic job's `data/scripts/backup_db.py` step fails.

## Don't
- Don't merge. Don't build in this chat. Run `planner.py` only from the main checkout.
