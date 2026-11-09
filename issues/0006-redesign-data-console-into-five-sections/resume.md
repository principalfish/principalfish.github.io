# Resume point — read this first

State checks: builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096` is idle on branch `claude/redesign-data-console-into-five-sections-e76f9c`; `git log --oneline -1 origin/claude/redesign-data-console-into-five-sections-e76f9c` shows `0b98a7b1` (piece 1, pushed). Draft PR #85 is open. State is `building`.

## The pending decision (exact)
Whether to start piece 2, and these four choices, recommendation first:
1. The current section comes from a table of endpoints, plus the `chamber` argument for the shared US endpoints, not from the URL. Endpoint names stay stable through piece 4; URLs don't.
2. `/polls` and poll detail sit under a top-level "All polls" link with no current section. `/polls/coverage` sits under Westminster: the page is Westminster-only and the plan's nav puts it there (the builder had suggested top-level).
3. New pages: `/westminster`, `/holyrood`, `/house`, `/senate`, `/president`, `/admin`. All 75 existing rules stay unchanged; the check becomes "old rules identical, only these added".
4. Until piece 5 replaces it, the one "Run US models" form appears on all three US landing pages through one shared partial, keeping its confirm text.

## What the next session does
On the go-ahead: record the choices in `decisions.md` and piece 2 of `plan.md`, save, and relay the user's words to the builder with the choices. The manual review runs `server.py` on 5055 from the worktree.
