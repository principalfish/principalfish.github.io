# Resume point: read this first

Updated 2026-11-08 (builder). Issue state: `building`.

State checks:
- `git rev-parse origin/poll-errors-simulation` → `5902f98f…` (HEAD; every piece through 7 pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after Piece 7.

Pending: **Self-review 3 fixes built, uncommitted** (`reviews/03-2026-11-08.md`; 28 files in the builder's worktree, checks green). Waiting on Phil's hands-on check of the tracker and approval. On approval the builder commits and pushes. After that: merge master (PR #80 landed, 14 files conflict), then `/publish` (piece 8).

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
