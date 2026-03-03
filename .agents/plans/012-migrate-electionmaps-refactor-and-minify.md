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
- Runtime module imports are now local: `electionmaps/electionmaps.js` references `../site/vendor/d3.v7.esm.js` and `../site/vendor/topojson-client.v3.esm.js`; minified bundle regenerated afterward.
- Root and bio pages now also use local fonts CSS (`site/vendor/fonts/google-fonts.css`) instead of remote Google Fonts links.
- Sanity pass cleanup completed in `electionmaps/electionmaps.js`: removed dead symbols (`viewport`, `hasPollTrackerMetricEnabled`, unused poll-tracker party-meta cache) and hardened poll-tracker timeline parsing to use `as_of_date` chronology with same-date dedupe by highest `election_id`.
- Follow-up fix for local-served module errors: replaced `site/vendor/d3.v7.esm.js` with a fully bundled self-contained ESM build (via esbuild) so it no longer re-exports `/npm/*/+esm` paths that break on local static servers.
- Added reproducible script `npm run vendor:d3` in `package.json` for regenerating the vendored D3 bundle.
- Local static preview automation now runs through `server.sh`: it executes `npm run vendor:d3` and `npm run minify:electionmaps` before starting `python3 -m http.server`; port is configurable via `PORT` env var.
- Poll tracker control-row UX update: moved range buttons (`All`, `Last 30`, `Last 90`) into the same inline control row as metric toggles (`Seats`, `Vote %`) in `electionmaps/index.html`, with CSS alignment updates in `site/styles.css`; minified CSS regenerated.
