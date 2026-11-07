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
   - Files: `data/models.py`, `data/db.py`, `data/scripts/migrate_add_us_result_status.py` (new), `data/tests/test_migrate_us_result_status.py` (new)
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
   - Files: `data/scripts/us_results/convert.py` (new), `data/scripts/us_results/cli.py` (new), `old_data/scripts/usa/us_import.py`, `old_data/scripts/usa/import_{house,senate}_elections.py`, `data/scripts/rebuild_database.py`, `data/tests/test_us_results_convert.py` (new), `data/tests/test_usa_house.py`, `data/tests/test_usa_senate.py`
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
   - Functions: `loadSenateSpecialBaseline` (generalised), predict baseline loading, manifest passthrough
   - Depends on: 7, 8
   - Acceptance: tests show the 2028 class-3 seat list with FL/OH swinging from 2026 and the rest from 2022; the predict view opens on the 2026 result in the jsdom harness; the 2026 forecasts are still listed.

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
      - Then, only with go-ahead, run against the live DB:
        - the migration;
        - imports for 2018–2026 House + Senate, with the NYT-vs-538 diff reported for 2020–24;
        - the Current Senate rebuild;
        - the export.
      - Commit only the files the piece owns. Never `git add -A`: the test suite dirties `model_output_trends.json`.
    - Depends on: 1–10
    - Acceptance: the runbook is reviewed; each live step is run only after approval, and its output (counts, diff summary) is reported.

## Checkpoints
- [x] 1. Seat status/source schema + migration (ec682969)
- [x] 2. NYT fetch + cache (2a8f90e5)
- [ ] 3. Schema adapters A/B/C + race model
- [ ] 4. Converter, importer extensions, CLI, diff report
- [ ] 5. Console results tool
- [ ] 6. House maps 24 (2026) and 25 (2012 cycle)
- [ ] 7. Export: status, sourceNote, specials, naming
- [ ] 8. Front end: uncalled, source line, specials
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
