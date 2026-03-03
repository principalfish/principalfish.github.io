# issue-18

## Status
- [x] Unify electionmaps mobile interaction model
- [x] Make election list a full-screen mobile sheet
- [x] Add mobile Map / Seats / Totals view switching
- [x] Convert right panel into a mobile bottom sheet
- [x] Validate mobile interactions and update notes
- [x] Hook simulation JSON export into local model run action
- [x] Add one-click simulation export button in local server

## Notes
- Track this issue in a branch-named file under `.agents/issues/`.
- Branch: `issue-18`
- Scope starts with electionmaps mobile UX cohesion (left panel already moved to popup previously).
- Added branch-mapped issue tracking rule explicitly in `AGENTS.md` and `.agents/README.md` (`issue-18` branch -> `.agents/issues/issue-18.md`).
- `electionmaps/index.html` now includes mobile-only summary text, a `Map / Seats / Totals` switch, and a bottom-sheet handle in the right panel.
- `electionmaps/mobile-sidebar.css` now implements a full-screen election picker sheet, touch-sized controls, and a snap-style mobile right-side bottom sheet (collapsed/half/full states).
- `electionmaps/mobile-sidebar.js` now manages mobile view state, bottom-sheet state, poll-tracker-aware visibility, and mirrored subtitle summary text.
- Validation: no editor errors in `electionmaps/index.html`, `electionmaps/mobile-sidebar.css`, `electionmaps/mobile-sidebar.js`, `AGENTS.md`, and `.agents/README.md`.
- `data/server.py` now runs `data/scripts/export_non_simulation_elections.py --current-simulation --output-file electionmaps/data/results/prediction-simulation.json` automatically after a successful non-dry-run `/models/run` execution.
- `data/polls/templates/model_run.html` now displays a separate "Simulation JSON Export" result block (command, exit code, stdout/stderr).
- Added `/exports/current-simulation` POST route in `data/server.py` and an `Export Simulation JSON` button on the home console (`data/polls/templates/home.html`) to refresh `prediction-simulation.json` without running the model.
