# Resume point: read this first

Updated 2026-11-08 (builder). Issue state: `building`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `5902f98f…` (HEAD; every piece through 7 pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after Piece 7.

Pending: **Piece 8** (`/publish poll-errors-simulation`, planner chat): the PR text needs updating for Piece 7, committed as `5902f98f`. No builder work is pending.

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
