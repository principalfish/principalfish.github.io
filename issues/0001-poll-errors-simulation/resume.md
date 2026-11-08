# Resume point: read this first

Updated 2026-11-08 (planner chat). Issue state: `building` (back from `ready` for Piece 7).

State checks:
- `git rev-parse origin/poll-errors-simulation` → `789e36ae…` (HEAD; every piece through 6g pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after Piece 7.

Pending: **Piece 7** (split the map page: UNS by default, a "Show with modelled uncertainty" checkbox; see `plan.md` piece 7, decisions in `decisions.md`). Its decisions are settled. Started 2026-11-08 (Phil: "Start piece 7"); the builder (`builder: poll-errors-simulation`, `local_c2004f9a-b155-458f-ada1-ebe8ad8b0b0d`) has built it, uncommitted (22 files, checks green). Waiting on Phil: the manual review and approval (tried it, approve), plus whether to delete the builder's three scratch DB copies. On approval the builder commits and pushes; then `/publish` again (piece 8).

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
