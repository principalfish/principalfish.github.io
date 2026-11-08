# Resume point — read this first

## Where we are
0005 (GitHub #82), state `building`. Piece 1 is staged and uncommitted in the builder's worktree `.claude/worktrees/restructure-data-folder-e05123`, branch `claude/restructure-data-folder-e05123`, paused for the user's review.
State check: `git -C .claude/worktrees/restructure-data-folder-e05123 diff --cached -M --stat` shows 12 files, +104/−30.

## The pending decision (exact)
Piece 1 approval. It has a manual review: the user must say they tried the console before an approval is relayed.

## What the next session does
Relay the verdict to the builder (session `local_7fd00d5d-402a-4490-8fa8-e59f02ab44b2`), quoting the user's words. After it commits and pushes: tick the checklist, open the draft PR, and brief piece 2.

## Don't
- Don't approve for the user. Don't build in this chat.
