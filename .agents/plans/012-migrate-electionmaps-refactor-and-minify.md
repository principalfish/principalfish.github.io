# 012 Migrate electionmaps, refactor, and minify

## Status
- [x] Rename route/folder from `election-maps` to `electionmaps` across code/docs/scripts.
- [x] Refactor `electionmaps.js` to reduce duplication/superfluous helpers while preserving behavior.
- [x] Add automated minification flow for JS and CSS.
- [x] Generate minified artifacts and switch page to use them.
- [x] Audit/report external libraries used by election maps runtime.
- [x] Validate with focused checks and record learnings.

## Scope
- Migrate folder and route references repo-wide (`election-maps/` -> `electionmaps/`) while preserving data export/import behavior.
- Keep behavior unchanged while reducing obvious repetition in frontend map script.
- Implement a reproducible minification command (instead of manual edits) and run it.
- Confirm all third-party browser/runtime libs used by election maps.

## Validation notes
- Diagnostics: no editor errors in `electionmaps/electionmaps.js`, `electionmaps/index.html`, `data/server.py`, `data/models/uns/run_uns_model.py`, `data/scripts/export_non_simulation_elections.py`, `data/scripts/run_export_targets.py`, and `data/scripts/export_manifest_metadata.py`.
- Minify pipeline validated with `npm run minify:electionmaps`; outputs present at `electionmaps/electionmaps.min.js` and `site/styles.min.css`.
- External libs used by runtime: D3 (`https://cdn.jsdelivr.net/npm/d3@7/+esm`) and TopoJSON client (`https://cdn.jsdelivr.net/npm/topojson-client@3/+esm`). Fonts are loaded from Google Fonts.
