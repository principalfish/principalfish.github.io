# Resume point — read this first

## Where we are
0005 (GitHub #82), draft PR #84. All 10 pieces are committed and pushed (latest `413f28eb`) on `claude/restructure-data-folder-e05123`, worktree `.claude/worktrees/restructure-data-folder-e05123`. State should move to `review`, not yet recorded.

## The pending decision (exact)
The user's OK for this chat to return to the main checkout, so the record can be saved and the state set.

## What the next session does
From the main checkout: `planner.py ingest restructure-data-folder-and-console`, `planner.py set <id> state=review`, `planner.py save`, since the updates made while the chat's working directory was the builder's worktree are unsaved and the builder's queued saves are still in its outbox. Then suggest `/review self`, and `/publish` after it. The user merges.

## Open for review
Dead helpers used only by archived scripts: `ParsedPollWithBases` and `HasRegionalShares` in `importers/polls/bases.py`, YouGov's `regional_shares()`.
The PR text must lead with the one-time editable install needed in the main environment right after merge, or the hourly restic job's `data/scripts/backup_db.py` step fails.

## Don't
- Don't merge. Don't build in this chat.
