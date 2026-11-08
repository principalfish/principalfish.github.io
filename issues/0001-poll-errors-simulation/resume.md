# Resume point: read this first

Updated 2026-11-08 (planner chat). Issue state: `review`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `1f89732b…` (every piece through 7 and the self-review 3 fixes pushed).
- `gh pr view 74 --json title,state` → OPEN, description still the first final text; it needs `/publish` again.

Pending:
- Beta note: Phil asked for a note saying this is beta and the model may change significantly, "at the bottom of the map". Builder is adding it to the footer below the map, shown only while the box is ticked, no new tests, uncommitted until Phil has seen it.

Then, as Phil asked ("once done lets merge in master and publish"): merge master through the builder (PR #80 landed, 14 files conflict), tests and bundles, then `/publish` (piece 8), which pauses for approval of the PR text.

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing) until master is merged; use `--current-simulation` for Westminster;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (three self-reviews).
