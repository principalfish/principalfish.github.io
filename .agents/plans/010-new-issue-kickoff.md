# 010 New issue kickoff

## Status
- [x] Capture issue scope and success criteria
- [x] Implement agreed changes
- [x] Validate behavior and note checks run
- [x] Update learnings with durable takeaways

## Scope
- Perform a thorough codebase audit for superfluous code, likely bugs, and modularization risks.
- Prioritize findings by user impact and implementation effort.
- Run available validation checks to ground findings in current runtime state.

## Decisions
- No production code changes in this pass; deliver audit findings first.
- Treat vendored files under `site/vendor/` as out-of-scope for quality findings unless repository integration is at fault.
- Focus recommendations on `data/`, `election-maps/`, and `guesstheyear/` user-authored code.

## Validation notes
- Ran test suite: `cd data && ./run_tests.sh`
- Result: `103 passed in 21.82s`.
- Checked diagnostics for key files (`data/server.py`, `data/db.py`, `election-maps/election-maps.js`, `guesstheyear/script.js`, `guesstheyear/app.py`): no editor-reported errors.
- Post-change validation rerun after implementing requested items 2/4/5:
	- Diagnostics: no editor errors in `data/server.py` and `guesstheyear/script.js`.
	- Tests: `cd data && ./run_tests.sh` => `103 passed`.

## Findings summary
- High: `guesstheyear/app.py` assumes a challenge row always exists; empty/invalid DB can crash `/api/challenge` due to unchecked `row` use.
- High: `data/server.py` creates a fresh SQLAlchemy engine per request via `_get_db() -> Database()`, causing avoidable connection/engine churn.
- Medium: `data/server.py` uses unbounded in-memory `PREVIEW_CACHE` without TTL/size cap, risking memory growth under repeated preview traffic.
- Medium: `guesstheyear/script.js` assigns `seed` without declaration, creating an implicit global and increasing mutation risk.
- Medium: very large mixed-responsibility modules (`election-maps/election-maps.js`, `guesstheyear/script.js`, `data/server.py`) reduce maintainability and raise regression risk.
- Low: `data/server.py` hardcodes dev secret key and runs Flask with `debug=True` in the module entrypoint; safe for local use but risky if reused in non-local deploys.

## Implemented now (user-requested 2, 4, 5)
- (2) DB lifecycle reuse: `data/server.py` now memoizes a process-level `Database` instance in `_get_db()` (`_DB` cache) instead of constructing a new engine per route call.
- (4) Implicit global fix: `guesstheyear/script.js` removed undeclared `seed` assignment by introducing `getDailyChallengeIndex(...)` with local `const` usage.
- (5) Targeted modularization: `guesstheyear/script.js` now centralizes repeated concerns into helpers:
	- daily save key/state load/save (`getDailySaveKey`, `loadDailyGameState`, `saveDailyGameState`)
	- guess-input control toggling (`setGuessControlsDisabled`)
	This reduces duplication in `init`, `handleGuess`, `viewGame`, and `resetGameState`.

## Implemented now (Predict 2029 mode)
- Added a new left-rail clickable state in `election-maps/election-maps.js`: `Predict 2029` (appended after election links).
- Added a sidebar predict controls panel in `election-maps/index.html` (`#mapsPredictPanel`) with:
	- party selector
	- per-region swing inputs (percentage points)
	- reset party / reset all actions
- Added supporting styles in `site/styles.css` for predict panel layout and regional input rows.
- Implemented client-side regional projection logic in `election-maps/election-maps.js`:
	- stores regional swings per party
	- applies swings to baseline seat vote shares by region
	- renormalizes shares to 100%
	- recomputes seat winners and re-renders map/table/seat list through existing view-state pipeline.

## Validation notes (Predict 2029)
- Diagnostics check run for touched files:
	- `election-maps/election-maps.js`
	- `election-maps/index.html`
	- `site/styles.css`
- Result: no editor-reported errors.

## Follow-up fix (Predict 2029 regression + production warning)
- Fixed runtime regression in `election-maps/election-maps.js` by declaring missing state map `predictBaseSeatsByKey` used during predict-mode initialization/projection.
- Removed Tailwind CDN runtime includes from `election-maps/index.html` (`site/vendor/tailwindcdn.js`, `site/tailwind-config.js`) to avoid production runtime warning and keep page CSS static.

## Validation notes (Follow-up fix)
- Diagnostics check run for touched files:
	- `election-maps/election-maps.js`
	- `election-maps/index.html`
- Result: no editor-reported errors.

## Follow-up update (Predict input model alignment)
- Adjusted Predict 2029 input model in `election-maps/election-maps.js` to fixed parties only:
	- Labour, Conservative, Reform, Green, Lib Dems.
- Prediction UI now excludes Northern Ireland from input rows.
- Added England aggregate entry with expandable/collapsible English region rows (`Show regions` / `Hide regions`).
- Swing resolution behavior now applies England values as defaults for English regions unless a region-specific override is entered.
- Projection now treats non-listed parties as residual (`Other` bucket): after adjusted listed-party shares, remaining share is assigned to non-listed parties proportionally.

## Validation notes (Predict input model alignment)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.
- Static sanity checks:
	- fixed predict party key set matches requested order,
	- NI excluded from input row generation,
	- England expansion toggle text/path present,
	- England fallback swing resolution used for English seats.

## Follow-up update (Prepopulate with 2024 regional numbers)
- Predict mode inputs now prepopulate from `2024-general` regional vote shares (for Labour, Conservative, Reform, Green, Lib Dems).
- `Submit` now derives regional swings from `(input share - 2024 baseline share)` and then reuses the existing projection pipeline.
- England aggregate remains a default for English regions; explicit region-row edits override England values.
- Reset now restores baseline 2024 values (instead of zeroing inputs).

## Validation notes (2024 prepopulation)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.
- Static sanity checks:
	- baseline loader targets manifest election id `2024-general`,
	- baseline share map is built per region (+ England aggregate),
	- submit path rebuilds swings from edited inputs before projection.

## Follow-up update (Predict input UX constraints)
- Prediction grid input boxes widened for easier editing.
- Prediction numeric inputs now normalize/display to one decimal place.
- Rightmost matrix column now represents auto-calculated `Other` share per row (`100 - entered five-party sum`) instead of a fixed total.
- Submit now blocks projection when any row exceeds 100 entered percentage and shows a warning popup listing offending rows.

## Validation notes (Predict input UX constraints)
- Diagnostics: no editor errors in `election-maps/election-maps.js` and `site/styles.css`.
- Static sanity checks:
	- one-decimal formatting helpers used for rendered and changed values,
	- `Other` cells update live on input changes,
	- submit path validates over-100 rows before applying projection.

## Follow-up update (Poll tracker mode)
- Added a new left-rail mode action: `Poll tracker` (below `Predict 2029`).
- Poll tracker mode hides map + right insights panel and shows a dedicated no-map trend view.
- Added CSV-backed multi-line chart sourced from `data/models/uns/output/model_output_trends.csv` with:
	- configurable metric toggles (`Seats`, `Votes`)
	- configurable party toggles
	- dual y-axes (left seats, right votes)
	- solid seats lines and dashed votes lines per selected party.

## Validation notes (Poll tracker mode)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.
- Static sanity checks:
	- CSV parser reads `seats_won` + `vote_total_sum` fields,
	- poll tracker controls are wired and trigger chart rerender,
	- mode switching hides map layout and restores it when returning to election/predict views.

## Follow-up update (Poll tracker fixes)
- Changed poll tracker secondary metric from vote totals to vote percentage (`vote_pct`), including right y-axis tick formatting with `%`.
- Completed and wired quick date-range controls (`All`, `Last 30`, `Last 90`) to slice the plotted timeline window.
- Hardened no-map mode layout by forcing `display:none` on map stage and right insights panel while poll tracker is active.

## Validation notes (Poll tracker fixes)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.
- Static sanity checks:
	- parser now reads `vote_pct` field,
	- chart legend/labels now indicate `Vote %`,
	- range button state updates and triggers chart rerender,
	- layout toggle path explicitly sets `hidden` and `display` for map panes.

## Follow-up update (Poll tracker hover + controls placement)
- Added line-hover tooltip interaction in poll tracker chart.
- Hovering either seats or vote-% line now shows: party, date, seats, vote % at nearest timeline point.
- Moved range controls (`All`, `Last 30`, `Last 90`) into a dedicated row below Seats/Vote% toggles.

## Validation notes (Poll tracker hover + controls placement)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.
- Static sanity checks:
	- tooltip handlers bound on both solid and dashed lines,
	- tooltip content includes seats and vote % fields,
	- range group appears in separate row under metric toggles in markup.

## Follow-up update (Poll tracker x-axis readability)
- Reduced x-axis date tick density dynamically based on chart width.
- Added dedupe guard for end ticks to avoid tightly stacked final date labels.
- Rotated x-axis date labels (`-32°`) and increased bottom chart margin for better readability.

## Validation notes (Poll tracker x-axis readability)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.
- Static sanity checks:
	- adaptive tick-count variables and spacing logic present,
	- rotated x-axis label transform applied.

## Follow-up update (Mode URL query params + default election)
- Added route query support with `view` + `election` params:
	- `view=election`
	- `view=predict`
	- `view=polltracker`
- Mode switches now update URL via history replace state without full reload.
- Election links now include `?view=election&election=<id>`.
- Startup now defaults election selection to `current-prediction` when no valid election query is provided.
- Startup routing now auto-enters predict/polltracker mode when `view` query requests it.

## Validation notes (Mode URL query params + default election)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.
- Static sanity checks:
	- route helpers present and used by predict/polltracker activation,
	- election link hrefs include `view=election`,
	- default fallback path targets `current-prediction`.

## Follow-up update (Trend CSV migration)
- Migrated `model_output_trends.csv` to `election-maps/data/results/model_output_trends.csv`.
- Updated generation path in `data/models/uns/run_uns_model.py` so new runs write directly to the new location.
- Updated server trend cache path in `data/server.py` and frontend poll tracker path in `election-maps/election-maps.js`.
- Moved existing file contents from old `data/models/uns/output/` location to new `election-maps/data/results/` location.

## Validation notes (Trend CSV migration)
- File search confirms a single canonical trend cache file at `election-maps/data/results/model_output_trends.csv`.
- Diagnostics: no editor errors in `data/models/uns/run_uns_model.py`, `data/server.py`, and `election-maps/election-maps.js`.

## Follow-up update (Poll tracker axis labels)
- Added explicit chart axis labels in poll tracker view:
	- left y-axis: `Seats`
	- right y-axis: `Vote %`
	- x-axis: `Date`

## Validation notes (Poll tracker axis labels)
- Diagnostics: no editor errors in `election-maps/election-maps.js` and `site/styles.css`.

## Follow-up update (Polltracker query cleanup)
- Poll tracker route now omits `election` query param and uses only `view=polltracker`.
- Election query param remains for election/predict routes.

## Validation notes (Polltracker query cleanup)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Remove `seats.electorate` dependency in UNS)
- Updated `data/models/uns/run_uns_model.py` to stop loading ORM `Seat` entities for simulation setup.
- Added a minimal seat reference query (`SELECT id, region_id FROM seats ...`) and local `SeatRef` dataclass for UNS scope building.
- This removes runtime dependence on `seats.electorate` for UNS simulation runs while preserving existing projection behavior.

## Validation notes (UNS seat dependency removal)
- Ran: `cd data && ../election_data/bin/python models/uns/run_uns_model.py --dry-run`
- Result: simulation completed successfully (650 projected seats, no schema error for missing `seats.electorate`).

## Follow-up update (Poll tracker default party selection)
- Updated `election-maps/election-maps.js` poll tracker defaults so `Green` is selected by default.
- `Other`/`Others` is now excluded from default preselection.
- Default set still targets six parties; when Green is not in top-six non-other by seats, one non-green default entry is replaced with Green.

## Validation notes (Poll tracker default party selection)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Predict NAT column for Scotland/Wales)
- Updated Predict 2029 input matrix to include a single `NAT` column.
- `NAT` maps to `SNP` for Scotland rows and `Plaid Cymru` for Wales rows.
- England/English-region rows show no NAT input (`—`) and are unaffected.
- Projection and baseline/swing logic now model `snp` and `plaidcymru` shares where applicable.

## Validation notes (Predict NAT column)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Poll tracker nav highlight)
- Fixed poll tracker left-rail active-state ordering in `activatePollTrackerMode()`.
- Active class removal now runs before setting Poll tracker active, so the selected item remains highlighted.

## Validation notes (Poll tracker nav highlight)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Predict input party column order)
- Reordered Predict user-input party columns to match requested sequence:
	- Labour, Conservative, Lib Dems, Green, Reform, NAT, Other.
- Implemented by updating base predict party-key ordering while keeping NAT/Other logic unchanged.

## Validation notes (Predict input party column order)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (UNS auto-backfill missing dates)
- Updated `data/models/uns/run_uns_model.py` to auto-backfill missing daily dates by default.
- On non-dry-run execution, the script now reads existing trend-cache `as_of_date` values and identifies missing dates between the latest prior date and requested `--as-of-date`.
- It runs simulations for each missing date (using the same lookback-window length and half-life settings) and prints `AUTO-BACKFILL` plus per-run progress.
- If no missing dates are found, behavior falls back to a single run for the requested date.

## Validation notes (UNS auto-backfill)
- Diagnostics: no editor errors in `data/models/uns/run_uns_model.py`.
- Dry-run smoke check succeeded for `--as-of-date 2026-03-03`.
- Planner check (`dates_to_run_for_cfg`) returned an explicit missing-date list and range as expected.

## Follow-up run (Live backfill execution)
- Executed non-dry-run: `run_uns_model.py --as-of-date 2026-03-03 --since-days-back 30`.
- Auto-backfill processed missing dates with progress output through `Backfill progress: 60/60`.
- Post-run trend-cache verification confirms no missing dates in `2026-02-20` through `2026-03-03`.

## Follow-up cleanup (`#2` model outputs)
- Removed accidental `model_uns` elections with names ending in ` #2` from the database and deleted dependent votes.
- Purged corresponding rows from `election-maps/data/results/model_output_trends.csv` by matching election ids and `election_name` suffix.
- Verification confirms zero remaining ` #2` rows in both DB and CSV.

## Follow-up update (Same-date overwrite policy)
- Updated `run_uns_model.py` so running the model for an `as_of_date` now overwrites existing outputs for that date instead of creating `#2` names.
- Implementation deletes existing `model_uns` elections for `UNS <as_of_date>%` (plus votes) before persisting the fresh run.
- Trend cache update now removes existing rows for the same `as_of_date` before writing new rows, ensuring full-date replacement.

## Validation notes (Same-date overwrite policy)
- Ran `run_uns_model.py` twice for `2026-03-03`; both runs persisted as `UNS 2026-03-03`.
- Verification:
	- DB exact count for `UNS 2026-03-03` = `1`
	- DB suffix count for `UNS 2026-03-03 #*` = `0`
	- CSV rows for `2026-03-03` with suffix `#*` = `0`

## Follow-up update (Local console trend chart mapping)
- Fixed trend-series assembly in `data/server.py` for `/models/outputs` charts.
- Chart timeline now orders by `as_of_date` (chronological) instead of `election_id`.
- Added per-party/per-date deduping that keeps the latest row by `election_id` when duplicates exist for the same date.
- This prevents visual jumps/misaligned party traces after same-date overwrites/backfills.

## Validation notes (Local console trend chart mapping)
- Diagnostics: no editor errors in `data/server.py`.
- Trend CSV sanity check confirms all rows have parseable `as_of_date` values (`bad_or_missing_as_of_date_rows=0`).

## Follow-up update (Predict layout in right column)
- Moved Predict `User Input (uniform swing)` panel from map-stage overlay into the right column (`maps-panel-right`).
- Added predict-mode layout sync so when `Vote Totals` is expanded, `Seats` card is hidden and Predict input fills remaining right-column space.
- When vote totals are not expanded, `Seats` remains visible and Predict input is shown as a normal right-column card.

## Validation notes (Predict right-column layout)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.

## Follow-up update (Predict split-ratio tuning)
- Added compact-vs-fill state classes for Predict right-column panel.
- In Predict mode with non-expanded Vote Totals, input panel now uses a bounded compact height so Seats remains visible with a better balance.
- When Vote Totals is expanded, existing fill behavior remains (input takes reclaimed space, Seats hidden).

## Validation notes (Predict split-ratio tuning)
- Diagnostics: no editor errors in `election-maps/election-maps.js` and `site/styles.css`.

## Follow-up update (Predict input panel cleanup)
- Removed close `×` control from Predict input panel header.
- Restyled Predict panel header to align with right-column card styling (non-gradient, subtle divider).
- While `Show regions` is expanded in Predict mode, Seats card is now hidden (same reclaim behavior as expanded Vote Totals).
- Simplified party headers to colour-only swatches with names on hover (`title`), including NAT swatch/hover text.
- Reduced input box size for denser matrix layout.
- Removed internal Predict-grid scrolling behavior by default (`overflow: visible`, no max-height cap) to avoid in-panel scrollbars.

## Validation notes (Predict input panel cleanup)
- Diagnostics: no editor errors in `election-maps/election-maps.js`, `election-maps/index.html`, `site/styles.css`.

## Follow-up update (Predict toggle placement + filter input width)
- Updated Predict England row control layout so `Show regions` is stacked vertically below the `England` label.
- Widened filter popup majority range input boxes so 3-digit values fit comfortably.

## Validation notes (Predict toggle placement + filter input width)
- Diagnostics: no editor errors in `site/styles.css`.

## Follow-up update (Other swatch + final width nudge)
- Predict `Other` header now renders as a grey swatch-only box (hover label via title), matching the simplified colour-header style.
- Increased filter majority input width slightly again for better 3-digit fit/comfort.

## Validation notes (Other swatch + final width nudge)
- Diagnostics: no editor errors in `election-maps/election-maps.js` and `site/styles.css`.

## Follow-up update (Conditional predict scrollbar)
- Added conditional internal vertical scrolling for Predict input grid when both conditions are true:
	- Vote Totals expanded
	- Show regions expanded
- This prevents vertical clipping when both sections are expanded simultaneously.

## Validation notes (Conditional predict scrollbar)
- Diagnostics: no editor errors in `election-maps/election-maps.js` and `site/styles.css`.

## Follow-up update (Predict region label width)
- Added a Predict-only display alias for `Yorkshire and the Humber` -> `Yorks and the Humber` in the user input matrix.
- Underlying region keys/data are unchanged; this is display-only to reduce row width pressure.

## Validation notes (Predict region label width)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Predict directional region abbreviations)
- Expanded Predict display-only aliases for long directional English region names:
	- `North East England` -> `North East`
	- `North West England` -> `North West`
	- `South East England` -> `South East`
	- `South West England` -> `South West`
	- `East of England` -> `E of England`
- Existing `Yorkshire and the Humber` -> `Yorks and the Humber` alias remains.

## Validation notes (Predict directional region abbreviations)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Predict Yorkshire label final)
- Updated Predict display alias for `Yorkshire and the Humber` to final preferred short label: `Yorks`.

## Validation notes (Predict Yorkshire label final)
- Diagnostics: no editor errors in `election-maps/election-maps.js`.

## Follow-up update (Predict helper text removal)
- Removed the Predict panel helper sentence: `Prepopulated with 2024 regional vote shares. Edit values and submit; remaining share is treated as Other.`

## Validation notes (Predict helper text removal)
- Diagnostics: no editor errors in `election-maps/index.html`.

## Follow-up fix (Predict force-scroll visibility)
- Added a max-height cap for `maps-predict-window-force-scroll .maps-predict-grid` so vertical scrollbar appears when both Vote Totals and Show regions are expanded.

## Validation notes (Predict force-scroll visibility)
- Diagnostics: no editor errors in `site/styles.css`.

## Follow-up fix (Predict actions anchored, grid scroll only)
- In force-scroll state, Predict panel body now uses a two-row layout: scrollable grid area + anchored actions row.
- Submit/Reset controls are pinned to the bottom (outside the scroll area), so the grid can scroll fully to last region rows.

## Validation notes (Predict actions anchored, grid scroll only)
- Diagnostics: no editor errors in `site/styles.css`.

## Follow-up update (Predict scrollbar thickness)
- Styled Predict user-input grid scrollbar to be much thinner and subtler.
- Added both Firefox (`scrollbar-width`) and WebKit (`::-webkit-scrollbar`) rules for consistency.

## Validation notes (Predict scrollbar thickness)
- Diagnostics: no editor errors in `site/styles.css`.

## Follow-up fix (Predict double-expanded scrollbar restoration)
- Added an explicit max-height bound to the Predict force-scroll body (`min(52vh, 430px)`) and reinforced flex/min-height constraints.
- This ensures the grid overflows in double-expanded state so the vertical scrollbar is always present.

## Validation notes (Predict double-expanded scrollbar restoration)
- Diagnostics: no editor errors in `site/styles.css`.

## Follow-up fix (Predict anchored header bleed-through)
- Made Predict panel top header background fully opaque and raised its stacking order.
- Increased sticky table-header z-index and ensured opaque header backgrounds so scrolling rows do not visually bleed behind anchored headers.

## Validation notes (Predict anchored header bleed-through)
- Diagnostics: no editor errors in `site/styles.css`.
