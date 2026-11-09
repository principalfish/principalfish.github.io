# Resume point — read this first

State checks: builder session `local_25f43011-ef61-46dc-9023-7e1b5918b096` is idle on branch `claude/redesign-data-console-into-five-sections-e76f9c`; `git log --oneline -1 origin/claude/redesign-data-console-into-five-sections-e76f9c` shows `2178b777` (piece 2, pushed). Draft PR #85 is open. State is `building`.

## The pending decision (exact)
Whether to start piece 3, and these four choices, recommendation first:
1. Chart.js: vendor one pinned version into `console/static/` (about 200 KB, MIT), replacing the unpinned CDN tag at `model_outputs.html:88`, its only use. Alternative: pin the CDN URL, which still fails offline.
2. Template merging: run form and result macro first, then the queue macros one at a time under the existing assertions; stop short of anything that would change the rendered HTML.
3. Reformatting the long uncertainty templates is checked by comparing rendered HTML with whitespace collapsed.
4. Add a short manual review: charts on a Westminster outputs page and one other section's outputs page.

## What the next session does
On the go-ahead: record the choices in `decisions.md` and piece 3 of `plan.md`, save, and relay the user's words to the builder with the choices. Run planner commands from the main checkout, and never `cd` into the worktree except in a subshell.
