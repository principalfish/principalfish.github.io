# Resume point: read this first

Updated 2026-11-08 (planner chat). Issue state: `building` (back from `ready` for Piece 6h).

State checks:
- `git rev-parse origin/poll-errors-simulation` → `789e36ae…` (HEAD; every piece through 6g pushed).
- `gh pr view 74 --json title,state` → OPEN, with the first final description from `pr.md`. It needs `/publish` again after 6h.

Pending: **Piece 6h** (split the map page: UNS by default, a "Show with modelled uncertainty" checkbox; see `plan.md` 6h, decisions in `decisions.md`). Its decisions are settled. Waiting on Phil for: whether to keep the proposed manual review, whether the latest-poll move applies to the US page too (shared engine; recommended yes), and the go-ahead to start 6h. Then the builder (`builder: poll-errors-simulation`, `local_c2004f9a-b155-458f-ada1-ebe8ad8b0b0d`) runs it.

Reminders until merge:
- the full `export_elections.py` fails on this branch (0002's map-24 is missing); use `--current-simulation` for Westminster until 0002 lands;
- no Westminster poll imports or model runs from a master checkout until merge.

History: `plan.md` (Notes), `decisions.md`, `review.md` (two self-reviews).
