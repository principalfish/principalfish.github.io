# Resume point: read this first

Updated 2026-11-08 (builder). Issue state: `building`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `5902f98f…` (HEAD; every piece through 7 pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after Piece 7.

Pending: **Self-review 3 fixes** (`reviews/03-2026-11-08.md`; Phil: "Recommended set"). The builder is applying them, then pauses for Phil's hands-on check of the tracker. After that: merge master (PR #80 landed, 14 files conflict), then `/publish` (piece 8). Findings left for #78 are noted in 0003's `plan.md`.

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
