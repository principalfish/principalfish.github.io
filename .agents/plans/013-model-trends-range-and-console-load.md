# 013 Model trends range and console load

## Status
- [x] Trace shared model-trends route/data path used by local data console and electionmaps poll tracker.
- [x] Reproduce and identify why earliest trend history is missing (expected from 2024-07-05 or earliest poll date).
- [x] Implement fix so both views show complete trend history from source.
- [x] Optimize local data console model page to load only latest 10 model runs plus CSV-based trend data.
- [x] Validate endpoints/UI behavior and update docs/learnings.

## Scope
- Shared trend data source and parsing between `data/` model outputs page and `electionmaps` poll tracker.
- Performance of local data console model outputs page.
- Keep fixes minimal and behaviorally consistent with existing UI.

## Validation notes
- Root cause reproduced: trend cache contains many rows where `as_of_date` mismatches the date encoded in `election_name` (`UNS YYYY-MM-DD`), causing timeline collisions/label drift in both UIs.
- Mismatch audit on current CSV: `mismatches=7839`; raw `as_of_date` span `2024-01-01..2026-03-03`, but canonical UNS name-date span is `2024-07-05..2026-03-03`.
- `data/server.py` now canonicalizes trend dates from `election_name` first (falling back to `as_of_date`), and model outputs DB query now fetches selected elections first (default latest 10) before vote-count aggregation.
- CSV-missing fallback in `data/server.py` now scopes trend reconstruction to selected election ids (default latest 10), avoiding full-history vote scans.
- `electionmaps/electionmaps.js` now canonicalizes poll-tracker row dates using UNS date from `election_name` when present.
- Diagnostics clean for touched files; minified frontend assets regenerated via `npm run minify:electionmaps`.
- Added one-off repair utility `data/scripts/normalize_uns_trend_dates.py` and executed it on `electionmaps/data/results/model_output_trends.csv`.
- Repair results: `rows=8008`, `changed=7839`, `unchanged=169`, `unresolved=0`; post-repair mismatch check reports `mismatches=0` and `as_of_date` span `2024-07-05..2026-03-03`.
- Script created backup at `electionmaps/data/results/model_output_trends.csv.bak` before writing.
- Follow-up chart correctness fix: `data/server.py` trend-series grouping/dedupe now keys by party name (case-insensitive) instead of `party_id`, preventing line identity swaps when historical CSV rows contain party-id remaps across dates.
- Trend CSV schema slimmed: removed `party_colour` and `vote_total_sum` columns from `electionmaps/data/results/model_output_trends.csv`; current file rewritten with backup at `model_output_trends.csv.pre-column-drop.bak`.
- `data/models/uns/run_uns_model.py` trend writer now emits compact fields only (`election_id,election_name,as_of_date,party_id,party_name,seats_won,vote_pct`) and filters existing rows to that schema on rewrite.
- Poll tracker colour resolution in `electionmaps/electionmaps.js` now derives from manifest party metadata by `party_id` (with existing key/name fallback), not from trend CSV.
- Local data console trend page (`data/server.py`) no longer depends on dropped columns: party colours come from `Party` table lookup and chart percentages use cached `vote_pct`.
- Follow-up colour mismatch fix: enhanced `data/scripts/normalize_uns_trend_dates.py` to also normalize `party_id` from manifest party-name mapping; applied to trend cache with `party_id_changed=7260`, leaving `party_id_name_mismatches=0`.
- Further schema slimming: removed `party_name` from trend cache CSV; canonical names now resolve from `electionmaps/data/elections.json` manifest parties (site polltracker) and `Party` table lookup (local data console).
- `data/models/uns/run_uns_model.py` trend writer now emits `election_id,election_name,as_of_date,party_id,seats_won,vote_pct` only; `electionmaps/data/results/model_output_trends.csv` rewritten to that header with backup at `model_output_trends.csv.pre-party-name-drop.bak`.
- Added subtitle metadata flow: `run_uns_model.py` now writes `electionmaps/data/results/model_output_trends_meta.json` with `latest_poll_snippet` (pollster + fieldwork range) for the latest run date.
- `electionmaps/electionmaps.js` now loads that metadata and appends the snippet to subtitles in both prediction (`model_uns` top summary) and polltracker mode.
- UI polish: applied the predict-grid thin scrollbar style globally in `site/styles.css` so all scrollable panels use consistent thin scrollbars; regenerated `site/styles.min.css`.
- Predict input table sticky header layering adjusted in `site/styles.css` (opaque cell backgrounds + stronger z-index ordering) to prevent underlying row text bleeding behind the header while scrolling.
- Top-left map toolbar `Reset` (`data-map-action="reset-view"`) now resets map view + filters + choropleths and re-renders, without touching predict user-input values.
- Performance pass: removed `google-fonts.css` from `electionmaps/index.html` so `/electionmaps` no longer fetches self-hosted custom font files; measured avoided font payload is ~1927.48 KB for that route.
- Follow-up experiment: removed `google-fonts.css` from `index.html` as well; measured homepage shell (without custom fonts) ~427.21 KB and avoided custom-font payload ~1927.48 KB on `/`.
- Completed cleanup: removed remaining `bio/index.html` font stylesheet include and deleted all files under `site/vendor/fonts/` (including `google-fonts.css` and bundled font binaries) since no runtime pages reference them.
