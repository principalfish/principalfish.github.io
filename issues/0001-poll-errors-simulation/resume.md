# Resume point: read this first

Updated 2026-11-08 (builder). Issue state: `building`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `1f89732b…` (HEAD; every piece through 7 and the self-review 3 fixes pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after Piece 7.

Pending: self-review 3 fixes are approved and pushed (`1f89732b`). Next: merge master (PR #80 landed, 14 files conflict), which the planner chat briefs Phil on first, then `/publish` (piece 8).

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
