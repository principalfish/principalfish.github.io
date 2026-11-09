# Resume point — read this first

## Where we are
0005 (GitHub #82), state `review`, draft PR #84. All 10 pieces are committed and pushed (latest `413f28eb`) on `claude/restructure-data-folder-e05123`, worktree `.claude/worktrees/restructure-data-folder-e05123`. `/review self` round 1 has run; the user is choosing which findings to fix.

## The pending decision (exact)
Which verified findings to fix:
1. medium · `data/scripts/backup_db.py:24` · the main venv has no editable install, so by-path scripts fail after merge. No code fix: PR text and the user's install straight after merge.
2. low · `data/models/westminster/uncertainty_validation_report.json:1182-1183` · two of eight `source_sha256` values are stale after piece 9; `numpy_version` is 2.5.4. Regenerating is a live step and needs its own go-ahead, last, after any other fix.
3. low · `data/importers/polls/bases.py:70`, `data/models/support/poll_bases.py:9` and 12 docstring lines name `schema.X`; should be `core.schema`.

Dropped by the verifier: `README.md:13` (the install is at line 14), coverage's accepted wrapper lines, two style notes, six unused imports that are unchanged from master.
Not from a check: dead helpers used only by archived scripts (`ParsedPollWithBases`, `HasRegionalShares` in `importers/polls/bases.py`, YouGov's `regional_shares()`).

## What the next session does
Write this review's file with `planner.py next-review`, add its index line, save. Send the chosen findings to the builder (session `local_7fd00d5d-402a-4490-8fa8-e59f02ab44b2`) to run its Review fixes step. Then re-check, record, and suggest `/publish`.

## Don't
- Don't merge. Don't build in this chat. Run `planner.py` only from the main checkout. Don't regenerate the report without the user's go-ahead at the time.
