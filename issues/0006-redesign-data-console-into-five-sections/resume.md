# Resume point — read this first

State checks: builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096` is idle on branch `claude/redesign-data-console-into-five-sections-e76f9c`; `git log --oneline -1 origin/claude/redesign-data-console-into-five-sections-e76f9c` shows `4a7a8b9e` (piece 3, pushed). Draft PR #85 is open. State is `building`.

## The pending decision (exact)
Whether to start piece 4, and these five choices, recommendation first:
1. The shared US pages (`/us/import…`, `/us/results…`, `/us/run-models`, and the save action `/us/matchups/<map>/<seat>`) stay until piece 5 splits them, which then carries their redirects and old-POST 404s.
2. Westminster: `/westminster/polls/import` (with its queue beneath), `/westminster/polls/coverage`, `/westminster/by-elections`, `/westminster/model`, `/westminster/outputs`. Holyrood: `/holyrood/model`, `/holyrood/polls/import`.
3. Matchup pages: `/house/matchups`, `/senate/matchups`, `/president/matchup`.
4. `/site/*`, `/db/*`, `/polls`, `/polls/<id>` stay; piece 7 can move the admin ones.
5. 302 for old GET pages only, keeping query strings, in one table with a test. The old queue `/finish` URL gets no redirect, because GET there runs the model step. No manual review for this piece.

## What the next session does
On the go-ahead: record the choices in `decisions.md`, update pieces 4 and 5 in `plan.md`, save, and relay the user's words to the builder with the choices. Never `cd` into the worktree except in a subshell.
