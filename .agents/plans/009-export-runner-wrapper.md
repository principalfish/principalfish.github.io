# 009 Export runner wrapper

## Status
- [x] Define wrapper behavior and output conventions
- [x] Implement wrapper script to invoke exporter per election
- [x] Add current-simulation export invocation
- [x] Validate wrapper with dry-run execution
- [x] Update learnings with operational notes

## Scope
1. Keep existing exporter as the source of truth for payload generation.
2. Add a thin orchestration script that calls exporter with selector flags.
3. Cover all real elections and the latest prediction simulation.

## Decisions
- Use a Python wrapper in `data/` so it can query DB election names reliably.
- Invoke `export_non_simulation_elections.py` via subprocess with explicit args for each target.
- Write per-election payloads into a configurable output directory and include one `current-simulation.json` export.
- Export shared TopoJSON files per `maps.id` (`map-<id>.topo.json`) so map outputs are deduplicated across elections.
- Emit manifest `settings` (`mapFilesById`, `dataFilesByElectionId`) so the webpage resolves file paths from configuration instead of hardcoding per-election map files.

## Validation notes
- `data/scripts/export_non_simulation_elections.py --dry-run` now plans two topo writes (`map-1.topo.json`, `map-2.topo.json`) and stale topo cleanup.
- Non-dry-run export removed legacy per-election topo files and rewrote `election-maps/data/elections.json` with `settings` and per-election `mapId`.
- `election-maps/data/maps/` now contains exactly two files: `map-1.topo.json` and `map-2.topo.json`.
- Bulk export now also includes the latest `model_uns` election as manifest entry `current-prediction`, which appears as a selectable left-bar option in the webpage and maps to `results/prediction-simulation.json`.
- Party key split is now year-aware for Reform UK: `reformuk` maps to `ukip` before 2024 and `reform` from 2024 onward.
- Vote totals now preserve full numeric values from `vote_total` in `partyInfo[*].total` (no integer rounding), which keeps model output precision.
- Frontend party labels now cover `sdlp` and `uu` (UUP) and use `Reform UK` display text for `reform`.
- Export JSON output now uses compact separators to reduce file size across generated manifest, maps, and results payloads.
- Added and ran `data/scripts/split_ukip_reform_parties.py` to split party usage in main DB:
	- created `UK Independence Party` (`short_name=ukip`) if missing
	- reassigned `votes.party_id` for elections before 2024 from Reform UK to UKIP
	- kept 2024+ rows on Reform UK
	- same boundary logic applied to `poll_rows` by `polls.fieldwork_end` year
- Post-migration verification confirms separate parties in DB (`reformuk` and `ukip`) and vote usage split by year (2010–2019 UKIP, 2024+ Reform UK).
- Manifest export now includes metadata payloads in `settings`:
	- `parties` (full list with ids, keys, names, short names, colours)
	- `partiesByKey` (lookup used by frontend at startup)
	- `regionsByMapId` (regions grouped by map for startup filtering/controls)
- Added `data/scripts/export_manifest_metadata.py` to refresh only manifest metadata without rerunning full election/static exports.
- Result export payloads now use compact schema `pf-results-v2` (`seats[]` with short keys and compact party rows) to reduce JSON size while preserving full meaning.
- Frontend loader (`election-maps/election-maps.js`) now supports compact results format and legacy formats, so exports can be smaller without breaking existing rendering logic.
- Manifest elections now include `comparisonElectionId`:
	- current prediction compares to `2024-general`
	- each historical election compares to the immediately previous election in sequence
	- oldest election (2010) has no comparison id
- Vote totals table now computes `±` seats and `±` vote% from comparison election and hides both comparison columns when no comparison exists.
- `election-maps/election-maps.js` now renders TopoJSON maps with D3 (`d3-geo`, `d3-zoom`, `topojson-client`) instead of static placeholder polygons.
- Clicking a constituency polygon zooms into that feature bounds; clicking empty map space resets zoom.
- Removed battlegrounds toolbar button and related placeholder interaction state.
- Seat list entries now render with party colour icons (no full party-name text), and if a seat flips versus `comparisonElectionId`, the row shows `GAIN FROM` with the losing party colour icon.
- Clicking a seat row in the right-hand seat list now zooms the map to that constituency via shared map feature lookup/zoom controller logic.
- Seat list now keeps a visible selected state (`.is-selected`) for the most recently clicked row so the user can track current map focus.
- Seat-row winner (owner) icon now renders in a fixed-width slot (`.maps-seat-owner`) separate from optional gain text/icon, keeping icon alignment consistent across rows.
- Maps now initialize with a slight zoom-in (`INITIAL_MAP_SCALE`) and reset returns to that same initial framed view rather than full-extent identity.
- Seat list row layout now places the winner icon to the left of constituency name (`.maps-seat-main`) while keeping gain context text/icons on the right side (`.maps-seat-meta`).
- Zoom interaction was aligned to production `main.min.js` behavior: seat-click zoom now uses legacy bounds-derived scale math (sqrt-adjusted bbox + `0.05` factor), `scaleExtent([1,8])`, and longer transition timings (~1500ms click zoom, 500ms reset).
- Boundary styling was tuned to a thinner visual profile per screenshot feedback: constituency borders reduced and region boundary overlay kept only slightly heavier/different colour for subtle separation.
- Applied an additional thin-pass per follow-up feedback: constituency borders set sub-1px and region mesh reduced further while remaining subtly distinguishable.
- Default map opening zoom was increased slightly again (`INITIAL_MAP_SCALE`), so the page lands a bit closer without altering click/reset zoom mechanics.
- Seat highlight layering was corrected: region mesh now renders beneath seat polygons, and hovered/clicked seat paths are raised so highlight strokes appear above neighboring seats.
- Follow-up fix: separated boundary and seat geometry into dedicated D3 groups (`maps-boundary-layer`, `maps-seat-layer`) so seat data-joins no longer target boundary paths; this prevents highlight ordering regressions.
- Added explicit active-seat path state (`maps-region-path-active`) with node tracking + raise-on-activate, so repeated hover/click/seat-list interactions consistently keep the current seat highlighted above others.
- Added click-driven seat details popup overlay in the map stage: map-seat and seat-list selection now open a contextual card with winner/gain metadata, region, majority %, turnout, and top party vote shares (+ comparison deltas where available).
- Seat popup now auto-closes on reset interactions by tying dismissal to the shared reset handler (empty map-space click and reset buttons).
- Frontend now consumes manifest `settings.regionsByMapId` to resolve seat region keys into display labels for the seat popup, replacing raw lowercase/slug region values in UI.
- Seat popup metadata rules were refined: remove duplicated winner line, retain `FROM` gain indicator, show majority as `% = raw margin`, and suppress turnout only for `model_uns` prediction view while always showing turnout for historical/general elections.
- Added frontend party-key normalization for legacy payload variants (notably `ukindependenceparty` -> `ukip`) during seat parsing so early-election labels and colours stay canonical across tables, seat list, and popup.
- Seat popup party rows now include a faint percentage-scaled background bar (party colour, low opacity) behind each row to improve comparative scanability at a glance.
- Popup bar rows were re-tuned for readability: increased bar opacity and switched row layout to a two-column grid so party icon/name stay left-aligned while numeric values remain right-aligned.
- Increased popup row bar opacity further after visual feedback so bar-chart backgrounds are clearly visible while preserving overlaid text legibility.
- Seat popup majority display now mirrors turnout behavior by election type: prediction (`model_uns`) shows percentage only, while non-prediction elections continue showing `% = raw margin`.
- Added a supplemental manifest election entry for `2019 Election (changed boundaries)` (map 2) backed by `data/old_data/files/2019election_new.json`, wired into `settings.dataFilesByElectionId`, left-nav elections list, and comparison chain (`2024-general` now compares to this changed-boundaries 2019 entry).
- Added the same election into the live DB (`elections` + `votes`): `2019 Election (changed boundaries)` on map 2 imported from `2019election_new.json` with full seat match (650/650) and 3,486 vote rows.
- Changed-boundaries election is now explicitly standalone in manifest (no `comparisonElectionId`) so vote-total change columns and seat gain-from logic are suppressed for that view.
- Exporter now excludes DB elections whose names match supplemental legacy entries to avoid duplicate manifest ids when the supplemental election also exists in DB.
- Implemented seat search end-to-end: on each election/map load, build seat-name index from current seats only, regenerate search suggestions, and support Enter/change selection to zoom/highlight/open popup for the matched seat.
- Added slow fade pulse for selected map seat (`maps-region-path-active`) via CSS keyframes, with `prefers-reduced-motion` fallback to disable animation for accessibility.
- Updated seat search UX to run selection/zoom when the search field is exited (`blur`) so users do not need to press Enter; retained Enter as optional shortcut and de-duped repeated submissions.
- Fixed selected-seat pulse targeting: hovering no longer marks seats as active, and active pulse now follows click/search-selected seat only; reset/close clears active seat pulse.
- Updated back links across maps/bio/Chronos to use fish SVG (`principal-fish-silly.svg`) on the left and include `← Back home` label text; added shared back-link fish sizing/alignment styles.
- Fixed fish icon cutoff by removing square crop styling (`object-fit: cover` and circular radius) and preserving intrinsic SVG aspect ratio (`width: auto`, fixed height) on all back-link fish icons.
- Added first-pass popup controls on election maps for `Filters`, `Choropleths`, and `Battlegrounds` (toggleable panels from toolbar), including working gains-only filter, majority/party/region filtering, choropleth metric and delta colouring, and battleground filtering based on previous-election incumbent/challenger lead.
- Refined map control popup visuals to a stronger bespoke style: rounded/glass cards, gradient headers, tighter typography/spacing, focus rings, and improved close-button/button emphasis while preserving existing panel behavior.
- Merged battleground behavior into `Filters` so it works in sync: added optional `second place party` + `%gap` controls in the main filter pass (no separate battleground popup path).
- Simplified choropleths to exactly `type` (`vote share` | `vote share change`) + `party` selection; map colouring and legend now use the selected party metric/delta.
- Refined Filters UX: removed `%gap` controls; moved `second place party` under majority and made it conditional (shown only when `by party` is selected), with state reset to `all` when hidden.
- Vote Totals table now recalculates from the currently visible filtered seats (and visible comparison seats for deltas) on every filter/choropleth refresh, keeping table sorting/toggle state bound to filtered summaries.
- Added on-map choropleth key rendering (title + gradient bar + labels) whenever choropleth is active, and switched choropleth colour ramps to party-aware colouring using the selected party's base colour.
- Increased contrast for negative `vote share change` choropleth values (deeper red anchor) to avoid washed-out appearance on loss-side shading while preserving party-colour positive ramp.
- Updated `vote share change` to a fixed diverging red↔blue ramp (negative red, positive blue) for clearer semantic interpretation; plain `vote share` remains party-colour based.
- Applied the election-maps “bluey” gradient background consistently across root static pages (`/`, `/bio/`, `/404`) by using the same `maps-page` + `maps-background` structure used on `election-maps/index.html`.
- `guesstheyear/` now uses an equivalent local background layer (`.bluey-page` + `.bluey-background`) matching the election-maps gradient while keeping existing Bootstrap/game UI behavior unchanged.

## Closeout
- Issue 009 is complete and closed.
- All requested election-map UX/filter/choropleth updates were implemented and recorded in this plan and `.agents/learnings.md`.
