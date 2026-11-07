# Plan: Import 2018–2026 US House and Senate results from NYT data

GitHub issue #79 · branch `import-2026-us-midterm-results` · base `master`

**Build model:** frontier (`claude-opus-5-5`) for every piece. Most pieces carry design judgement: schema, three source schemas, geometry stitching, front-end rendering and predict rollover. No piece is mechanical enough to hand down.

## Context

The 2026 midterms were held on 2026-11-03. The US maps stop at 2024, and the current US House and Senate converters (`old_data/scripts/usa/convert_538_{house,senate}.py`) read FiveThirtyEight CSVs. 538 closed in 2025.

### Settled decisions (from decisions.md, in full)
- **Scope:** House + Senate. Governors are out.
- **Timing:** import provisional results now, with re-runnable tools that refresh in place as counts finish.
- **Forecast scoring** (forecast vs. result) is a separate issue; not built here.
- **Source:** NYT's static results JSON (AP calls), credited with a disclaimer (NYT/AP data, non-commercial use). BBC is out. URL table and schema notes are in `threads/sources.md`:
  - 2026: `static01.nyt.com/elections-assets/pages/data/2026-11-03/results-{house,senate}.json` (schema A)
  - 2024: `.../pages/data/2024-11-05/results-{house,senate}.json` (A; district totals only)
  - 2022: `static01.nyt.com/elections-assets/2022/data/2022-11-08/results-{house,senate}.json` (A, older variant)
  - 2020: `static01.nyt.com/elections-assets/2020/data/api/2020-11-03/national-map-page/national/{house,senate}.json` (B)
  - 2018: `int.nyt.com/applications/elections/2018/api/1/races/2018-11-06.json` (C; one file, all offices)
  - Per-state 2026 Senate files with counties: `.../2026-11-03/results-<state>-us-senate[-special].json` (not needed now).
  - Fetch only the static CDN JSON. Never try to get past the HTML pages' captcha.
- **Raw downloads** go to a local cache outside git. Only the converted per-seat election JSON is committed.
- **Uncalled races:** store votes so far, mark no one elected, and flag the seat as uncalled with % expected vote counted. The map shows it as leading/uncalled.
- **Publication:** 2026 (and re-sourced past years) are exported to the public US maps site.
- **Disclaimer:** a source line under the map whenever a NYT-sourced election is selected: "Results: The New York Times / AP. Provisional until certified." Not in seat popups.
- **Past elections:** one NYT toolchain for 2018–2026 House + Senate, with three schema adapters (A: 2022–26, B: 2020, C: 2018).
  - Re-import 2020–2024 with `--refresh` (ids kept) to replace the 538 data, diffing against the 538 files as a check.
  - Add House 2018/2020 and Senate 2018.
  - Presidential keeps its existing sources.
- **House geometry:** one map id per district set.
  - Map 21 (cd119) stays for 2022–24.
  - New map for 2026: Census cb_2024 cd119 for the 41 unchanged states, plus each redrawn state's official enacted-plan shapefile (CA, TX, UT, OH, NC, FL, TN, LA, AL; evidence in `threads/redistricting.md`), stitched. A per-state source table lives in the build script; swap to Census 120th-Congress files when they appear. MO did not redraw. CO/NV/OR look like noise; confirm in the build.
  - New 2012-cycle map from Census cb_2018 cd116 serves both 2018 and 2020. Known inaccuracy: NC 2020 used a 2019 court map that no Census file has, so NC 2020 shows on 2018 boundaries. The 2020 House source line notes this.
- **Runoffs/RCV:** store the decisive round (runoff or final RCV round) once available, falling back to the November first round until then. Runoffs to locate on NYT: GA Senate 2021-01-05 (both seats), GA Senate 2022-12-06, LA-5 2026 (December). Where none is found, the seat keeps its first round and is flagged.
- **Senate specials:**
  - Standalone specials (FL/OH 2026, AZ 2020) take the state's map slot.
  - Where a state has a regular and a special in the same year (GA 2020; MN, MS 2018; CA, OK 2022; NE, CA 2024), the map shows the regular race and the special goes in a separate per-year specials list in the export. It is shown in the state popup and counted in the chamber tally.
- **Same-party races** (CA/WA top-two, LA jungle) keep today's merge: one vote row per party per seat, totals summed, top candidate named. A per-seat `sameParty` flag lets the popup say so.
- **Predict rollover** happens in this issue: House and Senate `predictBaselineElectionId` → 2026-cycle results; `nextElectionYear` 2028, `nextElectionDate` 2028-11-07. The 2026 forecasts stay frozen (archived entries in the election list) for the scoring issue. 2028 forecast models are out of scope.
- **Predict anchor:** House and Senate predict views open on the actual 2026 result.
- **Senate 2028 baselines:** class 3 swings from 2022-us-senate, except a per-seat override list (FL, OH → 2026-us-senate). Generalise the `senateSpecialElections` per-seat `baselineElectionId` mechanism (`electionmapslogic/features/senate-baseline.js:11-35` is already generic). Use a separate key, because past entries in `senateSpecialElections` would corrupt `member.up` (`state.js:1378-1395`), and `_live_senate_specials` (`data/scripts/export/manifest.py:211-235`) drops non-next-year entries.
- **Current Senate** is derived from the 2026 results: 65 holdovers plus the 2026 winners. It is new code alongside the congress-legislators converter. Uncalled seats show as uncalled. `run_us_senate_model.py:55` reads the same file, so its chamber baseline changes too.
- **Console tool:** the local data console gets a US results tool: fetch the latest NYT files, convert, preview, import (refresh in place), for any supported year and chamber.
  - It follows the form → preview → confirm pattern (`console/blueprints/by_elections.py:22,43,83`) and shells out through `console/services/runner.py:21`.
  - The preview shows intermediate numbers (console-transparency rule): seats changed, newly called, % counted per race, party mapping, warnings.
  - Import does not export. Export is a separate explicit button.

### Assumed (not asked; carried as given)
- Re-runs are manual (no scheduler).
- The NYT-vs-538 diff is a reported check, not a gate.
- Tests use small hand-made fixtures in the NYT schemas, not copies of NYT files.
- Conversions produce the existing election-JSON shape (`{key: {seatInfo, partyInfo}}`), extended with status fields, and load through `old_data/scripts/usa/us_import.py`.

### Code facts the builder needs (data/ unless noted)
- **Schema:**
  - `Election` (`models.py:238-275`), `Seat` (`:167-197`, belongs to a map), `Vote` (`:282-317`).
  - There is no per-election-per-seat table.
  - Migrations are hand-written idempotent sqlite3 scripts; template `scripts/migrate_add_us_poll_scope.py` (Step list `:15-24`, single BEGIN `:118-120`). `Database.create_tables` (`db.py:155`) only does `create_all`.
  - `db.py:88-118` `ensure_elections_sqlite_schema` hand-copies the elections and votes DDL (used by `model_support/persistence.py:130`). Mirror any change to those tables there.
- **Import:**
  - `us_import.import_us_election` hardcodes `election_date=date(year, 11, 5)` (`us_import.py:134`). Take the real date.
  - One Vote per party per seat (`:140-157`).
  - `scripts/rebuild_database.py:106-141` replays `old_data/files/usa/*.json`; new files must slot in there.
- **Export:**
  - `_export_page` (`scripts/export_elections.py:472`) → `build_result_payload` (`scripts/export/payload.py:72-185`). Per-seat dict at `payload.py:163-179` (`n, r, w, p, ev`), schema `pf-results-v4`.
  - Manifest entry dict at `export_elections.py:695`. Hand-added entry fields survive regen only via `PRESERVED_ENTRY_FIELDS` (`:912`).
  - `naming.py` stems/ids are keyed on type+year (`:199-206`, `:255-262`), so a same-year special would collide.
  - mapModes flow through `build_map_modes_with_regions` (`scripts/export/manifest.py:93-151`). Ordering is `ordering.py:105-158`; supplemental entries are in `scripts/export/legacy.py:14-42`.
  - Baselines also live in `manifest.py:26-30` and `models/us/run_us_*_model.py` (`baseline_election_name`).
- **Front end:**
  - Winner fill `electionmapslogic/dom.js:1986-2004`; popup `renderSeatPopup` `dom.js:1372`; meta tags `:1414-1419` (copy the BY-ELECTION tag pattern at `:1415`).
  - `Seat` constructor/`fromRaw` `state.js:328-358`; clone at `:973`.
  - No footer/credit mechanism exists. The nearest are the subtitle span (`dom.js:80-112`) and the referendum info button (`dom.js:299-317`); viewport markup is `electionmapslogic/shell.html:113-125`.
  - Chamber tally is the multiMember branch of `summarize` (`state.js:717-733`).
  - Predict baseline is read at `features/predict-controller.js:63`; specials at `predict-controller.js:379`, `senate-forecast-controller.js:37`.
- **Console:**
  - Blueprints registered in `console/__init__.py:28-51`; nav card in `templates/home.html:31-53`.
  - In-memory preview cache `console/services/preview.py`.
  - US runner `console/services/us_models.py` (`model_run_slot` `:224-245`, `_run_chambers_and_export` `:352-384`); `_guarded_run` `blueprints/us.py:379`.
- **Tests:**
  - `tests/test_usa_{house,senate}.py` load old_data modules via importlib.
  - `tests/test_console_us.py`; `tests/test_export_manifest.py` (specials `:96-175`), `test_export_ordering.py`, `test_export_naming.py`.
  - Console template: `tests/test_console_by_elections.py`. Fixtures: `tests/conftest.py:52` (`db`), `:25` live-DB guard; `tests/console_fixtures.py:28-60`.
  - JS: vitest, `electionmapslogic/tests/*.test.js`.
- **Paths:** `config.py` defines only `DATABASE_PATH`. `mypy.ini` has `files = .` and `mypy_path = old_data/scripts/usa`, so old_data is type-checked.

## Approach

Normalise all three NYT schemas into one in-memory race model, then emit the existing election-JSON shape (extended with status) and load it through the existing US importer. One converter covers every year; only the adapters differ.

Per-seat status (called, % counted, sameParty, decisive-round flag) goes in a new `election_seat_status` table keyed (election_id, seat_id). The vote rows are unchanged, so the vote schema, payload rows and `ensure_elections_sqlite_schema`'s vote DDL stay as they are. The per-election source credit is a nullable `source_note` column on `elections` (mirrored in `db.py:88-118`).

Clashing Senate specials are stored as their own `Election` row:
- name `"{year} US Senate Special Election"`, type `us_senate`;
- `parent_election_id` → the regular election;
- seats are the same map-23 state seats.

The export folds the special into the regular election's payload as a `specials` list and gives it no manifest entry of its own; `naming.py` learns to skip or id it. Standalone specials are ordinary seats in the regular election.

New maps: id **24** for the 2026 district set and **25** for the 2012-cycle set (next free US ids after 21–23; the builder confirms they're free). Each has a full 435-district topojson built by a stitch tool that extends `build_house_topojson.py`.

New code lives in `data/scripts/us_results/` (the newer live-importer location, as with `scrape_holyrood_2026.py`), reusing `old_data/scripts/usa/us_import.py` for the load. Raw cache: `config.NYT_CACHE_DIR`, default `/home/philiph/dbs/nyt-cache` (outside the repo, next to the DB). Tests always pass an explicit tmp dir.

**Rejected:**
- Per-candidate vote rows (user chose to keep merging).
- Columns on every Vote row for status (duplicated per party, and changes the mirrored vote DDL).
- Keying clashing specials as extra map seats like `"Georgia (special)"` (seats without geometry leak into seat lists and the predict grid).
- Patching map 21 in place (2022/24 results need it unchanged).
- Wikipedia, MIT Election Lab, BBC as sources (see decisions.md).
- Committing raw NYT files.

## Pieces

1. **Seat status and source schema**
   - Files: `data/models.py`, `data/db.py`, `data/scripts/migrate_add_us_result_status.py` (new), `data/tests/test_migrate_us_result_status.py` (new), `data/tests/test_migrate_us_electoral_votes.py` (column lookup by name)
   - What: add an `ElectionSeatStatus` model (election_id, seat_id, called bool, pct_counted float nullable, same_party bool, decisive_round text nullable, unique (election_id, seat_id), cascade on election delete). Add `Election.source_note` (nullable text) and mirror it in `ensure_elections_sqlite_schema`. Add a `Database` helper to upsert/clear status for an election. Write an idempotent migration with `--dry-run` following `migrate_add_us_poll_scope.py`.
   - Functions: `ElectionSeatStatus`, `Database.set_seat_statuses`, `Database.get_seat_statuses`, migration `main`
   - Depends on: none
   - Acceptance: migration applies twice cleanly on a temp copy, and `--dry-run` reports the steps; `create_tables` builds the table on a fresh DB; existing tests pass.

2. **NYT fetch and cache**
   - Files: `data/config.py`, `data/scripts/us_results/__init__.py`, `data/scripts/us_results/sources.py` (new), `data/scripts/us_results/fetch.py` (new), `data/tests/test_us_results_fetch.py` (new)
   - What: a URL table per (year, chamber, kind: general/runoff) from Context, `NYT_CACHE_DIR` config, and a fetcher.
     - Generic client name; writes `<cache>/<year>/<chamber>-<kind>-<UTC timestamp>.json` plus a `latest` pointer; records retrieval time and `resultsUpdatedAt`.
     - Refuses non-CDN hosts. Has an offline mode that reads the latest cached file.
     - Locate the GA 2021/2022 and LA-5 2026 runoff URLs; record them or mark them absent.
   - Functions: `source_for(year, chamber, kind)`, `fetch(source, cache_dir, *, offline=False)`
   - Depends on: none
   - Acceptance: unit tests with a stubbed HTTP layer cover the cache layout, offline mode and host refusal; the runoff URL findings are recorded in Notes.

3. **Schema adapters and race model**
   - Files: `data/scripts/us_results/races.py` (new), `data/scripts/us_results/adapters.py` (new), `data/tests/test_us_results_adapters.py` (new) + fixtures under `data/tests/fixtures/nyt/`
   - What: a `Race` dataclass (state, seat key — House `ST-NN` with at-large `-01` as in map 21, Senate state name; candidates (name, party code, votes); winners; called; pct_counted; is_special; round info; certified).
     - Adapters A (2022–26), B (2020), C (2018) map NYT fields onto it: A uses `race_call_status`/`outcome.won`/state-level `reporting_units[].eevp`; B and C use their fields as found.
     - Party map: DEM/DFL → democrat, GOP/REP → republican, LIB, GRN → usgreen, IND → independent, everything else → others.
     - Drop the national popular-vote race and non-voting delegates.
     - RCV: the final round when `rounds` exist. A runoff race replaces the first round when supplied.
   - Functions: `Race`, `parse_a`, `parse_b`, `parse_c`, `party_key`
   - Depends on: none
   - Acceptance: hand-made fixtures for each schema cover a called race, an uncalled race, a same-party race, RCV with rounds, a jungle/runoff race, a standalone special and a clashing special; all parse to the expected `Race`s.

4. **Converter and importer**
   - Files: `data/scripts/us_results/convert.py` (new), `data/scripts/us_results/cli.py` (new), `old_data/scripts/usa/us_import.py`, `old_data/scripts/usa/import_{house,senate}_elections.py`, `data/scripts/rebuild_database.py`, `data/tests/test_us_results_convert.py` (new), `data/tests/test_us_results_cli.py` (new), `data/tests/test_usa_house.py`, `data/tests/test_usa_senate.py`, `data/tests/test_rebuild_database.py`; follow-ups also touch `data/models.py`, `data/db.py`, `data/scripts/migrate_add_us_result_status.py`, `data/tests/test_migrate_us_result_status.py` (senate_class column) and `data/scripts/us_results/races.py`, `data/tests/test_us_results_adapters.py` (unaffiliated → independent)
   - What: turn `Race`s into the election-JSON shape, extended per seat with `seatInfo.called`, `pct`, `sameParty`, `round`, and top-level `meta` (source note, retrieved/updated times, year, chamber, map id).
     - Write to `old_data/files/usa/{house,senate}-<year>.json` (committed) and, for clashing specials, `senate-<year>-special.json`.
     - Extend `import_us_election`: map id per year (House 2018/20 → 25, 2022/24 → 21, 2026 → 24; Senate → 23), the real election date, status rows, source_note, and special elections with `parent_election_id`. Same-party merge as today.
     - CLI: `fetch | convert | import | all` with `--year --chamber --refresh --dry-run --offline`, plus a diff report against the current file/DB (seats whose winner changed, newly called, vote deltas). It doubles as the NYT-vs-538 check.
     - Teach `rebuild_database.py` the new files.
   - Functions: `convert_races`, `import_us_election` (extended), `diff_against_existing`, CLI `main`
   - Depends on: 1, 2, 3
   - Acceptance:
     - a fixture year converts and imports into the temp DB with correct winners/status/specials;
     - `--refresh` keeps election ids;
     - `--dry-run` writes nothing;
     - the diff report lists changes;
     - existing US importer tests still pass.

5. **Console results tool**
   - Files: `data/console/blueprints/us_results.py` (new), `data/console/__init__.py`, `data/console/templates/us_results_form.html` (new), `data/console/templates/us_results_preview.html` (new), `data/console/templates/home.html`, `data/tests/test_console_us_results.py` (new)
   - What: form (chamber, year, fetch-latest or use-cache) → preview (token via `store_preview`).
     - The preview shows retrieval/updated times, per-race rows (seat, leader, margin, % counted, called, newly called/changed vs. DB), the party mapping table, warnings and the diff summary.
     - Confirm runs the import (refresh in place) and writes the converted JSON.
     - The result page has a separate "Export to site" button running the export via the runner. The USA card in home.html links to it.
   - Functions: `us_results_form`, `us_results_preview`, `us_results_confirm`, `us_results_export`
   - Depends on: 4
   - Acceptance: console tests (modelled on `test_console_by_elections.py`, `RecordingRunner`) cover form, preview content, confirm import, export as a separate action, and an expired token; `create_app()` smoke passes.

6. **House district maps 24 (2026) and 25 (2012 cycle)**
   - Files: `old_data/scripts/usa/build_house_topojson.py`, `old_data/scripts/usa/house_2026_sources.py` (new: per-state enacted-plan source table), `old_data/scripts/usa/check_redistricting.py` (new: county-overlap check), `uselectionmaps/data/maps/map-24.topo.json` (new), `uselectionmaps/data/maps/map-25.topo.json` (new), `uselectionmaps/data/map-modes-shell.json`, `data/tests/test_usa_house_topojson.py` (new or extended)
   - What:
     - Extend the builder to stitch: base national shapefile minus the listed states, plus per-state shapefiles, normalised to `ST-NN` names and `region` as today.
     - Find and record each redrawn state's official enacted-plan download (CA, TX, UT, OH, NC, FL, TN, LA, AL).
     - Build map 24 from cb_2024 cd119 + those plans, and map 25 from cb_2018 cd116.
     - Port the scratch redraw check into a repo script and use it to confirm CO/NV/OR are noise.
     - Add mapModes 24/25 to the shell (projection `albersUsa`, like 21). Create Map rows via the importer (piece 4's map ids).
   - Functions: `stitch_states`, `build_topojson`, `check_redistricting`
   - Depends on: none (piece 4 references the ids)
   - Acceptance: both topojsons have exactly 435 districts with unique `ST-NN` names that match the 2026 / 2018 NYT district keys; the check script reports CO/NV/OR as unchanged (or the finding is raised with the user); state sources are listed with URLs and retrieval dates.
   - Note: downloads and shapefile tooling (`ogr2ogr`, `mapshaper`) must be present. Check first and ask the user if they're missing.

7. **Export: status, source line data, specials, naming**
   - Files: `data/scripts/export/payload.py`, `data/scripts/export_elections.py`, `data/scripts/export/naming.py`, `data/scripts/export/manifest.py`, `data/tests/test_export_payload.py`, `data/tests/test_export_naming.py`, `data/tests/test_export_manifest.py`
   - What:
     - Per-seat payload gains `c` (called, emitted only when false) and `pc` (% counted), `sp` (sameParty) and `rd` (decisive round label), all optional so older payloads are unchanged; bump the schema only if the front end needs it to branch.
     - The manifest entry gains `sourceNote` from `Election.source_note`.
     - The regular Senate payload gains `specials: [{seat, class, winner, parties…, called, pc}]` from child special elections.
     - Special child elections get no manifest entry, and `naming.py` doesn't collide.
     - Archived 2026 forecast entries stay listed.
   - Functions: `build_result_payload`, manifest entry builder, `election_id`/stem helpers
   - Depends on: 1, 4
   - Acceptance: payload/manifest/naming tests cover each field, absent-by-default behaviour, and specials folding; `export_elections.py --dry-run` against a temp DB plans the new files.

8. **Front end: uncalled seats, source line, specials**
   - Files: `electionmapslogic/state.js`, `electionmapslogic/dom.js`, `electionmapslogic/shell.html`, styles where the map styles live, `electionmapslogic/tests/*.test.js`, rebuilt bundles (`npm run minify:electionmaps`)
   - What:
     - `Seat` parses `c/pc/sp/rd` in the constructor and `fromRaw` (both, `state.js:328-358`).
     - Uncalled seats are filled with the leader's colour hatched or lightened (`dom.js:1986-2004`); the popup gets an UNCALLED · n% counted tag (pattern `dom.js:1415`), a same-party note and the decisive-round label.
     - A source line under the map (new element in `shell.html:113-125`) shows the entry's `sourceNote` when present.
     - The Senate popup shows a state's special result, and the chamber tally counts specials.
     - Uncalled seats with no `w` (no positive votes, or a tied lead) render neutral, not as Others.
     - Cross-map comparisons (US House always compares with the previous House election): with no filter active, the totals-table ± must total the WHOLE comparison election, not just districts whose keys match. Otherwise reapportionment years misreport (2022 vs 2020 compares 435 seats with 428: R +13/D −6 instead of +9/−9). Per-seat gain/hold stays key-matched; a key absent from the comparison shows no gain.
   - Functions: `Seat`, `Seat.fromRaw`, fill function, `renderSeatPopup`, source-line render, `summarize`
   - Depends on: 7
   - Acceptance: vitest covers parsing, fill choice, popup tags, source line and tally; jsdom harness run (AGENTS.md recipe) on a US House and a Westminster page shows no regressions; bundles rebuilt.

9. **Predict rollover to 2028 and per-seat Senate baselines**
   - Files: `uselectionmaps/data/map-modes-shell.json`, `data/scripts/export/manifest.py` (`:26-30` baselines, new override key passthrough), `data/models/us/run_us_{house,senate}_model.py` (`baseline_election_name`), `electionmapslogic/features/predict-controller.js`, `electionmapslogic/features/senate-baseline.js`, tests (`test_export_manifest.py`, vitest)
   - What:
     - House: `predictBaselineElectionId` → `2026-us-house`, `predictAnchorElectionId` → `2026-us-house`.
     - Senate: baseline → `2022-us-senate` (class 3), anchor → `2026-us-senate`, `nextElectionYear` 2028, `nextElectionDate` 2028-11-07; the Seats-up tab label goes to 2028.
     - Add a `senateBaselineOverrides: [{seat, baselineElectionId}]` key (FL, OH → 2026-us-senate), consumed through `loadSenateSpecialBaseline`'s grouping.
     - Expire `senateSpecialElections` entries for 2026.
     - Model scripts' baseline names follow, but the models are not re-run.
     - Uncalled seats in the baseline (22 House at 2026-11-07; the Senate ones, AK and MS, are class 2 and moot for 2028): decided with the user, option A. Project each from its votes counted so far as if final and mark the projected seat called. A zero-vote seat stays neutral and uncounted. The 2026 result (anchor) view keeps `called:false`.
   - Functions: `loadSenateSpecialBaseline` (generalised), predict baseline loading, manifest passthrough
   - Depends on: 7, 8
   - Acceptance: tests show the 2028 class-3 seat list with FL/OH swinging from 2026 and the rest from 2022; the predict view opens on the 2026 result in the jsdom harness; the 2026 forecasts are still listed; tests show an uncalled baseline seat projecting from its leader and coming out called, a zero-vote seat staying unprojected, and the anchor view keeping `called:false`.

10. **Derived Current Senate**
    - Files: `data/scripts/us_results/current_senate.py` (new), `data/scripts/export/legacy.py` (if the entry needs a note), `data/tests/test_us_results_current_senate.py` (new)
    - What: build `senate-current.json` from the existing snapshot's holdovers (classes 1 and 3, minus seats filled in 2026, i.e. FL/OH class 3) plus the 2026 Senate election's winners, in the same shape `convert_senate_current.py` writes. Uncalled seats are marked uncalled (no member party; the front end shows them neutral/hatched). Re-runnable from the CLI and the console result page. Check what `run_us_senate_model.py:55` needs from the file and keep it valid.
    - Functions: `build_current_senate`
    - Depends on: 4, 8
    - Acceptance: a fixture produces 100 seats with the correct holdover/winner split and uncalled handling; the senate model loader still parses the file.

11. **Docs and live-data runbook** (live steps each need Phil's explicit go-ahead at the time)
    - Files: `README.md` (runbook section), `AGENTS.md` if a command changes
    - What:
      - Document the CLI, the console tool, cache location, migration, map builds and the refresh cadence.
      - Add a "2028 model run: before you start" list to the runbook (user decision, piece 9 review), with the four items from piece 9's open items in Notes:
        - bump `US_MODEL_COMPARISON_IDS` (manifest.py) to 2026-us-house / 2022-us-senate, since it is per type, not per cycle;
        - the House runner moving from map 21 to 24 makes `publish_trends` drop the archived 2026 House trend series;
        - House district polls are stored on map-21 seats, so repoint the poll importers before the 2028 House run;
        - every Senate forecast exports as `current-us-senate` / "2026 Prediction", so a 2028 run collides with the 2026 one.
      - Then, only with go-ahead, run against the live DB:
        - the migration;
        - imports for 2018–2026 House + Senate, with the NYT-vs-538 diff reported for 2020–24;
        - the Current Senate rebuild;
        - the export.
      - Commit only the files the piece owns. Never `git add -A`: the test suite dirties `model_output_trends.json`.
    - Depends on: 1–10
    - Acceptance: the runbook is reviewed and has the 2028 list; each live step is run only after approval, and its output (counts, diff summary) is reported.

## Checkpoints
- [x] 1. Seat status/source schema + migration (ec682969)
- [x] 2. NYT fetch + cache (2a8f90e5)
- [x] 3. Schema adapters A/B/C + race model (9f17c51d)
- [x] 4. Converter, importer extensions, CLI, diff report (0a9007e5)
- [x] 5. Console results tool (147ad9d4)
- [x] 6. House maps 24 (2026) and 25 (2012 cycle) (bfe5dc77)
- [x] 7. Export: status, sourceNote, specials, naming (f90f26d3)
- [x] 8. Front end: uncalled, source line, specials (9ec98204)
- [ ] 9. Predict rollover + per-seat Senate baselines
- [ ] 10. Derived Current Senate
- [ ] 11. Docs + live-data runbook (live steps on go-ahead)

## Verification
After each piece:
- `cd data && ./run_tests.sh` (strict mypy gates, then pytest; narrow with `-k` or a test path while iterating)
- `npm test` (vitest), for pieces 8–9 and any JS change
- `npm run minify:electionmaps` after any `electionmapslogic/` change
- `git diff --check`

Smoke checks:
- `cd data && ./election_data/bin/python -c "from console import create_app; create_app()"` (pieces 5+)
- `cd data && ./election_data/bin/python scripts/export_elections.py --dry-run` (pieces 7+)
- the jsdom harness from AGENTS.md on US House, US Senate and a Westminster page (pieces 8–9)

Note: this worktree has no `data/election_data` venv or `node_modules`. Build in the main checkout or create them first.

Test strategy:
- hand-made NYT fixtures per schema (piece 3);
- temp-DB import round trips, including `--refresh` id stability (piece 4);
- console preview/confirm via `RecordingRunner` (piece 5);
- topojson invariants of 435 unique names matching NYT keys (piece 6);
- payload/manifest field presence and absence (piece 7);
- vitest for rendering and tallies (piece 8);
- predict baseline selection (piece 9);
- 100-seat chamber assembly (piece 10).

No test touches the live DB (conftest guard).

## Notes
- 2026-11-07 (builder): branch `import-2026-us-midterm-results` cut from origin/master; baseline `./run_tests.sh` green (4556 passed). The build worktree links the main checkout's venv as `data/.venv` and `node_modules` (both git-excluded via the common `info/exclude`).
- 2026-11-07: pieces 1 and 2 built in parallel (user opted in), each in its own worktree, integrated here and reviewed/committed separately.
- 2026-11-07 (piece 1): `test_migrate_us_electoral_votes.py` compared the last `elections` column (`[-1]`) to check `target_election_year`; adding `source_note` broke that. Changed it to look the column up by name, and added the file to piece 1's Files.
- 2026-11-07 (piece 1): the raw-SQL election deletes (`model_support/persistence.py` `_delete_range`, `console/services/model_outputs.py:646`) don't remove status rows. Harmless today, because only model elections go through those paths and they never have status rows. Revisit if a results election ever does.
- 2026-11-07: piece 6 is blocked on tooling: neither `ogr2ogr` nor `mapshaper` is installed on this machine. Raise it with the user before starting piece 6.
- 2026-11-07 (piece 2): runoff sources found and added to `sources.py` (kind "runoff"):
  - 2018 Senate (MS special runoff, 2018-11-27, schema C): `https://int.nyt.com/applications/elections/2018/api/1/races/2018-11-27.json`. MS only.
  - 2020 Senate (GA, both seats, 2021-01-05, schema B): `https://static01.nyt.com/elections-assets/2020/data/api/2021-01-05/state-page/georgia.json`. Also holds a Public Service Commission runoff, which the importer must skip.
  - 2020 House (LA-05, 2020-12-05, schema B): `https://static01.nyt.com/elections-assets/2020/data/api/2020-12-05/state-page/louisiana.json`. Also holds a ballot measure and a PSC runoff, to skip.
  - 2022 Senate (GA, 2022-12-06, schema A2): `https://static01.nyt.com/elections-assets/2022/data/2022-12-06/results-georgia-us-senate-runoff.json`. The 2022-11-08 results-senate.json also carries race `GA-G-S-2022-12-06`.
  - 2026 House (LA-05): pending, no URL yet (December 2026 runoff).
  - No LA December House runoffs were found for 2018, 2022 or 2024. The 2024-12-07 Louisiana file holds only constitutional amendments.
- 2026-11-07 (pieces 1+2): one analysis pass ran over the combined diff, with findings routed per piece. Piece 1: only a missing CLI test. Piece 2: a batch of network-error, redirect-allowlist, pointer-validation and test fixes. `.env_example` gained `NYT_CACHE_DIR` (added to piece 2's files). The README migration step is left to piece 11.
- 2026-11-07: pieces 1 and 2 committed separately (ec682969, 2a8f90e5) and pushed to `origin/import-2026-us-midterm-results`. Final checks: mypy clean, 4667 passed, plus a re-run of the touched files after the last two review fixes (158 passed).
- 2026-11-07 (piece 3): user decisions:
  - California's partial-term Senate specials (Padilla 2022, Schiff 2024) are absent from NYT's files. Accept the gap; the same candidate won the regular race. Piece 7/8 mention it in those years' source line.
  - LA-05 2026, pending its December runoff, stays plain uncalled, with no runoff-pending flag.
  - State Green affiliates (e.g. Pacific Green) map to usgreen.
  - Also noted: the 2026 RCV races (AK, ME) have no NYT `rounds` yet, so they store the first round until NYT publishes the final round. Already-decided runoffs sit inside the 2020 (B) and 2022 (A2 Senate) general files, so those races carry `decisive_round="runoff"` even with kind "general".
- 2026-11-07 (piece 3) real-sample counts: House 435 every year. Senate 35/34/35/35/35 (2026→2018; 2024 is 34 because of the CA gap). 2026 has 22 House and 2 Senate races uncalled. NC-09 2018 is uncalled, which is correct: it was never certified.
- 2026-11-07 (piece 3) review round 1:
  - Correctness spot-checks passed. ME-02 2018/22/24 and AK-01 2022/24 RCV final rounds are right; so are the GA 2020/2022, MS 2018 and LA-05 2020 runoff swaps, and the CA same-party flags.
  - Fixes routed: add a `certified` field; fall back to precincts for B's pct when eevp is missing; a Senate-class table for B/C specials (2018 MN/MS → 2, 2020 AZ/GA → 3); bounds checks against overflow; `isdecimal`; duplicate candidate ids refused; extra tests.
- Note for piece 8: candidate names and party codes are stored as NYT sends them. Render them through the front end's existing escape helper, never raw `innerHTML`.
- 2026-11-07: piece 3 committed (9f17c51d) and pushed. Round 2 closed `certified` (A/A2 only; B's `edison_certified` doesn't mean certification), the B pct fallback, the B/C special-class table, bounds checks, ASCII-only ints and duplicate-id refusal. Checks: mypy clean, 4796 passed.
- Piece 3 Green-affiliate mapping: PAG / `pacific-green` (OR) and `mountain` (WV, 2018) → usgreen. GPJ, GRP/Grp, PRG/progressive and liberty-union stay others. Mountain Party's 2018 Green affiliation is unverified.
- 2026-11-07 (piece 4): first build passed (4849 tests). Trial run over every year: NYT and the committed 538 files agree on every winner for Senate 2020/22/24 and House 2022/24 (there's no 538 House 2020 file).
  - 538's AK Senate 2022 summed RCV rounds (777k against NYT's final-round 254k); NYT is right.
  - NE 2024's Osborn is IND in NYT, D in 538; NYT is right.
  - The importer refuses to refresh an election onto a different map, or to reuse a map id that belongs to another parliament. Confirm 24/25 are free in the live DB before piece 11.
  - CLI: `python scripts/us_results/cli.py <fetch|convert|import|all>` from data/.
- 2026-11-07 (piece 4) user decisions:
  - "Provisional until certified" applies only to the current cycle (the newest year in sources), while anything there is uncalled or uncertified. NYT's 2024 files still carry stale uncertified flags.
  - Uncontested seats backfill real totals from the 538 file when it has them and the winner matches, with `votesSource` flagged. Otherwise they get the nominal 100. Re-converting must stay idempotent after the 538 file is overwritten.
  - Unaffiliated-type codes (NPA, UNA, McMullin's) map to independent.
  - A special's Senate class gets a nullable `senate_class` column on election_seat_status, amended into piece 1's migration in place, since it hasn't run live.
- 2026-11-07 (piece 4) review round 1. Security: no findings. Correctness verified on temp DBs:
  - refresh keeps election ids and replaces status rows;
  - specials keep their parent links, and rebuild orders them after their regular year;
  - map-id guards fire;
  - 538-format files import unchanged;
  - re-convert is byte-identical, so the backfill survives;
  - provisional appears on 2026 only.
- Piece 4 fixes routed:
  - validate the file before clearing a refresh's votes (a bad file had emptied the election);
  - main() turns importer SystemExit into exit 1;
  - refuse a stale `senate-<year>-special.json` rather than import it (the operator removes it by hand; no automatic deletion);
  - no empty Map row from a refused import;
  - the House `all` no longer checks the Senate special;
  - extra error-path tests; explicit raises instead of asserts.
- Note for piece 8: render `sourceNote` (and candidate names and party codes) with textContent or the escape helper, never innerHTML.
- 2026-11-07 (piece 4) review rounds 2–3:
  - Fixed: malformed files map to `error:` + exit 1 before the report runs. A failed first import leaves no Map row.
  - Rebuild refuses up front (`RebuildError`) when `meta.special_file` doesn't match.
  - `convert` refuses a leftover specials file; a dry run only warns.
  - One database-free validator, `check_us_election_file` (the importer's own checks), replaces the CLI's parallel one. `import_files` plans every file before importing any, so a bad specials file can't half-import a year.
  - A warning flags a Senate special still in the DB that no file names.
  - Final: mypy clean, 4936 passed. The all-years trial converts and imports all 14 elections, with zero diffs on a `--refresh` pass and 0 winner changes vs 538.
- 2026-11-07: piece 4 committed (0a9007e5) and pushed. Before piece 11: confirm map ids 24/25 are free in the live DB.
- 2026-11-07 (piece 5) first build: routes GET `/us/results`; POST `/us/results/preview`, `/us/results/confirm/<token>`, `/us/results/export`.
  - The out-dir and cache come from `app.config` (`US_RESULTS_OUT_DIR`, `US_RESULTS_CACHE_DIR`).
  - Export runs `export_elections.py` under `model_run_slot`, the same as the site and by-election flows.
  - CSRF is the app-wide same-origin hook.
- Piece 5 review round 1. Security: no findings. Fixes routed:
  - on any import failure, restore the old file bytes (a DB error had left the committed JSON overwritten);
  - refuse stale tokens (an older preview after a newer one), and drop sibling tokens on confirm;
  - pop the token inside the slot;
  - a public `fetch.cached_snapshot_info` replaces the form's direct pointer reads (fetch.py added to piece 5's files);
  - a separate "not imported (House special)" label;
  - change-label tests, the files-checked count, line wrapping.
- 2026-11-07 (piece 5) review rounds 2–3 closed:
  - Per-file restore: a file whose import committed stays; others are restored or deleted, along with every directory the write created.
  - The stale-token refusal keys on `meta.retrieved_at`; sibling tokens are dropped on success.
  - The token is re-read inside `model_run_slot`.
  - `fetch.cached_snapshot_info` is used by the form.
  - Final: mypy clean, 4995 passed.
- IMPORTANT for piece 11 and for any manual try-out: from piece 1 on, the ORM's `Election` has `source_note` and there's a new `election_seat_status` table. The live DB isn't migrated, so the branch's console, export and importers fail on any elections query until `migrate_add_us_result_status.py` has run.
  - For a hand test before going live: back up the DB, migrate a copy, and point `.env`'s DATABASE_PATH at the copy (`.env` overrides the shell). Or run the migration on the live DB with the user's go-ahead.
- 2026-11-07: piece 5 committed (147ad9d4) and pushed. Piece 6 is still blocked on tooling (ogr2ogr, mapshaper). Piece 7 depends only on 1 and 4, so it can go first if the user wants.
- 2026-11-07: piece 6 unblocked. The user installed GDAL 3.12.2 (`/usr/bin/ogr2ogr`) and mapshaper 0.7.101 (`/usr/local/bin/mapshaper`, global npm). Per the user, piece 6 starts after piece 7 is committed.
- Piece 7: the documentation check found the results schema in README.md (~1015) and the entry table in `electionmaps/data/map-modes.md` (~60-72) need the new fields (`c`, `pc`, `sp`, `rd`, `specials`, `sourceNote`). Those two docs move into piece 7's files, since they're the export contract.
- 2026-11-07 (piece 7) first build: payload fields `c:false` (uncalled), `pc` (uncalled only, 1dp), `sp:true`, `rd`. For an uncalled seat, `w` = the leading party. Manifest `sourceNote` is recomputed each export.
  - Specials are folded into the parent's payload as `specials:[{n, cl, w, p, c, pc, sp, rd}]`. The child gets its own stem/id (`us-senate-{y}-special` / `{y}-us-senate-special`), which fixed a real collision in `models/us/_common.resolve_special_baselines`.
  - Schema stays pf-results-v4. Mypy clean, 5018 passed.
- Piece 7 review round 1. Security: no findings. User decision: House 2026 on map 24 gets NO comparison election (the boundary rule).
- Piece 7 fixes routed:
  - an export preflight refuses before writing anything if any pre-built map is missing;
  - uncalled seats with no votes or a tied top two get no leader (neutral);
  - exporting a folded special by name raises;
  - `resolve_special_baselines` skips folded specials;
  - more tests;
  - the schema docs in README and map-modes.md.
- 2026-11-07 (piece 7) decision REVISED by the user. US House always compares against the previous US House election, whatever the map (2020→2018, 2022→2020, 2024→2022, 2026→2024), as a full per-seat comparison by district key.
  - In redrawn states, a district compares against the same-numbered old district, and the user accepts that.
  - This gives 2026 House seat and vote ± in the totals table, plus gain/hold labels.
  - It replaces the earlier "no comparison for 2026 House" decision.
  - Implemented in `assign_comparison_elections` (us_house ignores mapId; every other type keeps the boundary rule).
- 2026-11-07 (piece 7) note for piece 8: an uncalled seat with no positive votes or a tied lead is exported with no `w`. Today's front end then draws it as Others (colour and count); piece 8 must render `c:false` with no `w` as neutral. A district missing from a cross-map comparison gets no gain and is left out of the comparison totals (no crash).
- 2026-11-07 (piece 7) round 2: all fixes verified. The front-end totals bug with cross-map comparisons (keys matched only) is added to piece 8's What. Last small batch routed: comparisons to earlier years only, preflight branch tests, docs.
- 2026-11-07: piece 7 committed (f90f26d3) and pushed. Next is piece 6 (tools now installed).
- 2026-11-07 (piece 6) deviation, source: no per-state enacted-plan download was needed. Census now publishes the 120th-Congress districts as TIGER/Line files, one per state (`TIGER2026/CD/tl_2026_<fips>_cd120.zip`, dated 2026-10-06), which the plan's "swap to Census 120th-Congress files when they appear" anticipated. All nine redrawn states come from them (`house_2026_sources.py`). They are full-resolution and include water, so the builder clips each state to the cartographic outline of that state in cb_2024 cd119 (keeps borders with unchanged states exact, fills boundary slivers into the adjoining district). The Files paths in the plan lack the `data/` prefix: the scripts live in `data/old_data/scripts/usa/`.
- Piece 6 findings:
  - The 120th-Congress files also carry Missouri's 2025 plan, which the 2026 election did not use, so MO stays on cb_2024 (the source table says so).
  - Redraw check (`check_redistricting.py`, NYT 2026 county results against county overlap): comparing map 21 with map 24, exactly UT, CA, TN, LA, OH, FL, TX, AL and NC drop to 0.00–0.05 mismatch (from 0.11–0.66). CO (0.11→0.12), NV (0.06), OR (0.08), AZ and MA are unchanged, so they are noise as predicted. A single threshold cannot separate NC (0.11) from CO (0.11), so the script flags states by the score falling between two maps.
  - District keys: map 24 matches NYT's 2026 keys and map 25 matches NYT's 2018 and 2020 keys exactly (435 each, no extras either side); map 21 still matches 2022.
  - Map 24 is built with mapshaper `-clean` and 6% simplification (659 KB; 12% kept far more vertices from the TIGER states). It, and map 25, have a few districts that mapshaper could not fully de-intersect (map 24: AL-04, NC-08, OH-01, TN-02, TN-09, UT-02, UT-04; relative area defect at most 5e-6; map 25: CO-07 at 1e-16). Invisible at map scale. Map 21 has none.
  - The 2020 NC inaccuracy stands (NC 2020 shows on the 2018 boundaries).
  - `mapModes` 24 (`us-house-2026`) and 25 (`us-house-2018`) added to `map-modes-shell.json`; the generated `map-modes.json` is not regenerated (export is piece 11's live step). `map-modes.json` rebuilds from the shell on export.
- 2026-11-07 (piece 6) built on Sonnet (experiment), inline in the builder shell. One analysis round: frontier correctness and security found nothing blocking (two low download-hardening notes, a docstring contradiction, a gap-fill edge case); all fixed except the zip-size limit and checksum, which are skipped (constant Census URLs, dev-time tool). Style/common-mistakes: dead `enrich`, unused constants, non-atomic download cache (now temp-then-rename) and a float threshold compare, all fixed.
- 2026-11-07: piece 6 committed (bfe5dc77) and pushed. User confirmed the Census TIGER2026 cd120 source for the nine redrawn states, MO on cb_2024.
- Piece 6 coverage (changed lines, 98% overall): `build_house_topojson.py` 96.9%. Uncovered lines are defensive: skip of an unknown state FIPS, the non-polygon branch of `_polygonal`, an empty gap piece, an empty clipped district. Each only guards against degenerate geometry the Census files don't produce. The new untracked files are not counted by diff-cover; their tests run 45 cases (the CLI of `check_redistricting.py` is the only part not run in tests; I ran it against the real data).
- 2026-11-07 (piece 8) built on Sonnet (experiment): implementation by a Sonnet subagent, one analysis round. Frontier correctness: no blocking findings, five low ones; security: none; style/mistakes: one stale doc row (`neutralFill` in map-modes.md, fixed) and a comment/`Object.hasOwn` nit (fixed). Fixed after review: subtitle named "Others" when nothing was counted yet (now just the election name plus "N uncalled"); the party filter missed parties that appear only in a Senate special. Left alone: popup row building is duplicated between `renderSeatPopup` and the special block (a refactor), and the special block names its leader while a regular uncalled seat's popup doesn't.
- Piece 8 design:
  - `Seat` gains `called`, `pctCounted`, `sameParty`, `round`, `senateClass` (from `c`, `pc`, `sp`, `rd`, `cl`); old payloads default to called.
  - **Uncalled seat with no `w` (item from piece 7):** `winner` is null, `fillKind()` is `neutral`: grey `mapConfig.neutralFill`, else `#d1d5db`. It is never Others, in colour, swatch or tally. An uncalled seat with `w` is hatched in the leader's colour (6px SVG stripe pattern); called seats are unchanged and still default to Others.
  - Tally: uncalled seats credit no party, get a separate `uncalled` count (still in `totalSeats`), and show as an "N seats uncalled" line in the totals table and "· N uncalled" in the subtitle ("Majority undecided" instead of "Hung parliament" while uncalled seats could still carry the leader past half).
  - **Cross-map totals fix:** `AppState.comparisonTotalsInputs()`, called by `recomputeVoteTotalsForMode`, summarises the WHOLE comparison election when no filter is active (`hasActiveFilters()` false); with a filter the old key-matched seats are used. On every committed election/comparison pair the unfiltered result equals the old one (UK pairs have identical key sets); a synthetic House 2024 vs a 2022 file with 8 seats removed now totals +2/+6, not the key-matched figures. Per-seat gain/hold stays key-matched, and an uncalled seat gets no gain label.
  - Source line: `<p id="mapsSourceNote">` under the map, set with `textContent` from the manifest entry's `sourceNote`, hidden when absent or outside the election view.
  - Specials: `ElectionData.specials` (Map by seat key, taking the regular seat's region). The Senate state popup gets a "Special election (Class N)" block; specials count in the chamber tally and, under filters, on their own result. Scoped to the results payload only: the Current Senate and forecast chamber are untouched.
  - Popup: `Seat#statusNotes()` gives "UNCALLED · n% counted" (just "UNCALLED" without `pc`), a same-party note and a decisive-round label. `renderSeatPopup` is now exported for tests.
  - Checks: vitest 47 files, 1368 passed, 3 skipped (+2 files, +51 tests); bundles rebuilt; jsdom harness (US House 2024 and Westminster 2024, synthetic uncalled/no-w/sp/rd House, synthetic Senate with specials, predict, Holyrood, President, Current Senate) shows no console errors and an unchanged left bar and totals.
  - Changed-lines coverage: state.js 97.2%; dom.js 61.5%. The uncovered dom.js lines are the d3 render paths (hatch pattern creation and the fill choice in the map draw, the uncalled totals line, the swatch colour) which vitest can't reach; they were exercised by the jsdom harness instead.
- 2026-11-07 (piece 8) user change before commit: the seat ± in the totals table leaves out uncalled seats ("cant we not include these seats in the delta?"; seat ± only, vote ± unchanged). Implemented as `AppState#comparisonSummary` (called by `recomputeVoteTotalsForMode`): the comparison summary's per-party SEAT counts exclude each comparison seat (and special) whose key is uncalled in the current election; comparison seats with no current counterpart still count, so the reapportionment fix holds; votes and the plain summary are untouched, and with no uncalled seats it equals the plain summary. Same on the filtered path (uncalled among the visible seats). Tests +5 (called-only delta, vote totals unchanged, filtered path, no-uncalled equality, reapportionment-sized sum); vitest 1373 passed, jsdom harness re-run clean.
- 2026-11-07: issue handed over to the new-style planner chat `planner: import-2026-us-midterm-results` (main checkout), which starts the builder. The old worktree `planner-us-midterm-import-eee87c` was switched back to its own branch, freeing `import-2026-us-midterm-results`.
- 2026-11-07 (planner chat): record ingested from the old chat's outbox (plan.md merged by hand: its piece-1 Files line and two piece-1 notes had never reached the record). Draft PR #80 opened. Overlap check against issue 0001 (`poll-errors-simulation`, PR #74): the committed branches already conflict in 6 files (`README.md`, `data/models.py`, `data/scripts/export/payload.py`, `data/scripts/export_elections.py`, `data/tests/test_export_manifest.py`, `data/tests/test_export_payload.py`; `git merge-tree`). Piece 6 shares nothing with 0001. Piece 8 shares `electionmapslogic/state.js`, `dom.js`, `shell.html`, `maps.css`, the tests folder and both rebuilt bundles; piece 9 shares `test_export_manifest.py`; piece 11 shares `README.md`/`AGENTS.md`. 0001 has only its live backfill/export left and ships first, so merging master into this branch after #74 lands (before piece 8) is the suggested order — the user decides.
- 2026-11-07 (planner chat): merge order decided by the user — piece 8 goes ahead now; merge master in after #74 lands, before piece 11 and the final review, then re-run the jsdom harness on a US House and a Westminster page.
- 2026-11-07: piece 8 committed (9ec98204) and pushed. Next is piece 9; it begins by raising the uncalled-baseline options.
- 2026-11-07 (piece 9) built on Sonnet (experiment): implemented by a Sonnet subagent, one analysis round plus a fix round; frontier correctness found one regression and one latent issue (below), security none, style/mistakes only stale docs and a duplicated class-cycle computation (reused now via `senate_class_up_in` in manifest.py).
- Piece 9 design:
  - Shell: House and Senate baselines/anchors/2028 date as specified; Seats-up tab "2028"; `senateSpecialElections` is `[]`.
  - `senateBaselineOverrides` records carry a `class` too (deviation: `{seat, class, baselineElectionId}`), because Florida and Ohio also hold Class-1 seats and the key never expires; an override applies only when its class is the regular class being contested (model `next_senate_class`, front end `model.regularClass`). Validated in `manifest.senate_baseline_overrides` (not year-gated), also in `DERIVED_MODE_KEYS`.
  - Front end: `loadPredictSpecialSeats` loads overrides through `loadSenateSpecialBaseline`; `SenatePredict.setBaselineOverrides` swaps them into the projection base; `regularSenateClass(year)` replaces the hard-coded Class 2 (null when no class votes that year, then members carry over). `member.last` is stamped for override seats.
  - Uncalled baseline seats (user decision A): `cloneBaselineSeat`/`projectedStatus` in predict.js serve every clone and projection path; an uncalled seat with votes is projected from its leader (largest vote row on a tie) and comes out called with `pctCounted` null; a zero-vote seat stays uncalled, winner null, never 'others'; the anchor election view keeps `called:false`.
  - Model runners: Senate runner baseline "2022 US Senate Election", class allowlist generalised (34 states for Class 3) and overrides win over specials; House runner baseline "2026 US House Election" on map "US House Districts 2026", `national_poll_map_name` on the 2024 map (where the generic ballot lives). Models not run.
- Piece 9 open items (not fixed here, for the user/planner):
  - Archived 2026 Senate forecast (`current-us-senate`): tabs "Seats up (2026)" and "Full Senate" are restored (the activation now gates on the forecast being a Senate model, with the cycle year read from its name "2026 Prediction"). But the export only keeps specials for `nextElectionYear` (2028), so its Florida/Ohio comparison shows "Comparison unavailable", and its Full Senate merges into the CURRENT post-2026 chamber, misrepresenting it. Options: (a) a per-forecast record in the manifest (cycle year + specials), (b) keep past-year specials in the export and filter by year in the front end (conflicts with expiring them for `member.up`), (c) archive per-forecast chamber and comparison files. Also every Senate forecast is exported as `current-us-senate` / "2026 Prediction", so a 2028 run would collide with the 2026 one; no archived ids exist yet.
  - `US_MODEL_COMPARISON_IDS` (manifest.py) is per type, not per cycle: the next House/Senate model run would export as `current-us-*` and still compare against 2024-us-house / 2020-us-senate instead of 2026-us-house / 2022-us-senate. Bump it when the 2028 runs are set up.
  - The House runner's output map moves 21 → 24, so `publish_trends` would rebuild `us-house-trends.json` from map 24 only and drop the archived 2026 trend series on the first 2028 run (the Senate, staying on map 23, would run the cycles together as one series).
  - House district polls are stored against map-21 seats, so the House model on map 24 sees no district polls (generic ballot unaffected); the poll importers need repointing before the 2028 House run.
  - The Senate predict landing renders the 2022 baseline until the first projection; House and Senate predict views open on a zero-swing projection (existing pattern, unchanged).
  - Checks: `./run_tests.sh` 5119 passed (mypy strict clean); vitest 1409 passed, 3 skipped; jsdom harness on the House and Senate predict views (synthetic manifest from the real `build_map_modes_with_regions`), the anchor views, Westminster and US President predict: no console errors; the House predict shows 5 of 6 uncalled test seats called and the zero-vote seat uncalled, the anchor keeps all 6 uncalled. Changed-lines coverage: predict*.js and senate-chamber.js 100%, manifest.py 100%, run_us_senate_model.py 93.8% (three defensive lines), export_elections.py 93.4%.
