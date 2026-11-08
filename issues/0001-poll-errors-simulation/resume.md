# Resume point: read this first

Updated 2026-11-08 (planner chat). Issue state: `review`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `a947645e…` (every piece through 7, the self-review 3 fixes and the beta note pushed).
- `gh pr view 74 --json title,state` → OPEN, description still the first final text; it needs `/publish` again.

Pending: the builder is merging `origin/master` (`4c88b881`, 14 files conflict). The merge pauses uncommitted for Phil's review and is not pushed until he approves. Then `/publish` (piece 8), which pauses for approval of the PR text.

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing) until master is merged; use `--current-simulation` for Westminster;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (three self-reviews).
