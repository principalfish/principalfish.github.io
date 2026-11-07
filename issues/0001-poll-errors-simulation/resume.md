# Resume point: read this first

Updated 2026-11-07 (planner chat). Issue state: `ready`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `789e36ae…` (HEAD; every piece through 6g pushed).
- `gh pr view 74 --json title,state` → "feat: add Monte Carlo poll-error uncertainty to Westminster forecasts", OPEN, with the final description from `pr.md`.

No pending decision. PR #74 is open with its final description; **merging is Phil's.** Once merged, `/issues` (which syncs) marks the issue `done`.

Reminders until merge (also in the PR text):
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
