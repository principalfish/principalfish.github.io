# Plan: restructure-data-folder

**Build model:** claude-opus-5-5. Most pieces carry silent-failure hazards (path depth, import order, double module identity). Two purely mechanical pieces are marked for Sonnet.

## Context

Reorganise `data/` by pipeline role, then jurisdiction, with no change in behaviour (GitHub #82). The console redesign is a separate issue that follows this one. Code line numbers are at master `35e5326c`; `bd9d4879` since then changed only docs, so they still hold. Docs references are at `bd9d4879`: the root README is now a short overview, and the data detail lives in `data/README.md` and `data/docs/*.md`.

**Read these on the planner branch, in this issue's folder:**
- `callers.md`: generated list of every Python import and text reference for each file that moves, grouped by piece. Re-grep when building, since lines drift.
- `findings/us-importers.md`, `findings/data-structure.md`: what each file is, coupling counts, and hazards.
- `decisions.md`: the ledger.

**Settled decisions (don't re-derive):**
- Organise by role first, jurisdiction second. One spelling per jurisdiction: `westminster`, `holyrood`, `us`.
- Two issues; folders first. This issue changes console imports only, not pages, routes or templates.
- Issues 0003 and 0004 aren't a constraint. Don't plan around them or edit their records.
- `model_support/` and the Westminster calibrate/validate scripts move now.
- The Westminster validation report is regenerated after that move, with Phil's go-ahead at the time. Its numbers must be identical apart from source fingerprints, paths and the report's own hash.
- Finished one-offs go in `data/archive/`, outside mypy and pytest. Archived scripts are a record: no upkeep, and they may not run after later moves. Tests that only cover archived scripts are retired. Test files that also cover live code are split, not dropped.
- `data/` becomes an editable install (`pyproject.toml`), replacing the `sys.path` adjustments. Scripts keep running by path, so commands, `console/paths.py`, `console/services/runner.py` and `rebuild_database.py`'s subprocess calls keep their shape.
- `models.py` becomes `schema.py`. Shared root modules move to `core/` as the last structural piece.
- TopoJSON builders stay live.
- Live operations are never implied by approving a piece.

## Current structure

```
data/
  README.md docs/                 runbook and topic pages
  models.py db.py config.py electoral_votes.py backup.py server.py init-test-db.sql
  model_support/                  shared model library, plus Westminster-only modules
  models/{westminster,holyrood,us}/  runners; shadowed by models.py, so not importable
  polls/importers/{westminster,holyrood,us}/
  old_data/
    files/{westminster,holyrood,usa}/   live inputs, read and written by current code
    scripts/{westminster,holyrood,usa}/ live seed and results importers, plus one-shots
    import_all.sh                       broken and unreferenced
  scripts/
    rebuild_database.py rebuild_model_trends.py backup_db.py export_elections.py
    export/  us_results/  by_election_import.py  calibrate/validate  migrate_*  repair_*
  console/  tests/
```

## Proposed structure

```
data/
  README.md docs/ pyproject.toml server.py run_tests.sh mypy.ini requirements.txt
  core/      schema.py db.py config.py electoral_votes.py backup.py us_geography.py migration_helpers.py
  inputs/    westminster/ holyrood/ us/
  importers/
    seed/      import_parties.py import_region_populations.py migrate_us_electoral_votes.py us_electoral_votes.py
    results/
      westminster/  import_general_elections.py import_topojson.py by_election_import.py
      holyrood/     import_holyrood_elections.py import_holyrood_seats.py
      us/           us_import.py import_{house,senate,presidential}_elections.py regions.py
        nyt/        today's scripts/us_results/
        geometry/   build_*_topojson.py house_2026_sources.py check_redistricting.py
    polls/     bases.py sanity.py types.py wikipedia_common.py
      westminster/ (pollsters, wikipedia_index.py, registry.py)  holyrood/  us/
  models/    __init__.py
    support/   shared library (today's model_support/ minus westminster_*)
    westminster/  runner, JSON artefacts, westminster_* modules, calibrate/validate/build_final_poll_errors
    holyrood/  us/
  export/    export_elections.py + today's scripts/export/*
  console/   internals unchanged here
  scripts/   rebuild_database.py rebuild_model_trends.py backup_db.py
  archive/   README.md + finished one-offs
  tests/     flat, unchanged in shape
```

**Goes to `archive/`:** `init-test-db.sql`; `old_data/import_all.sh`; `migrate_rename_model_uns.py`; `convert_538_{house,senate,presidential}.py`; `convert_wiki_presidential.py`; `convert_senate_current.py`; `migrate_add_{poll_bases,model_uncertainty,us_poll_scope,us_result_status,electoral_votes}.py`; `migrate_drop_seat_geometry.py`; `backfill_poll_bases.py`; `repair_poll_records.py`; `repair_regional_poll_rows.py`; `reset_us_poll_matchups.py`; `poll_sources.py`; `scrape_holyrood_2026.py`.

**Deleted:** the two `data/election_data/` files tracked by accident (`launcher manifest.xml`, `script (dev).tmpl`).

## Approach

One piece per move, each leaving the tree working. Each piece moves files with `git mv`, updates every caller listed in `callers.md`, and removes the `sys.path` lines and path-based test loads for the files it moves. So the editable install comes first and no file is touched twice.

Alternatives rejected:
- **Jurisdiction first** (`uk/`, `us/`, `shared/`): six of the seven shared model modules serve all three engines, and export and rebuild cut across every jurisdiction.
- **`python -m`:** would change every command, the console runner, the rebuild steps and about 39 test assertions.
- **A kept one-line bootstrap:** leaves the path adjustments the issue removes.
- **Moving `model_support/` after 0003:** Phil chose to move it now.

## Pieces

Every piece also meets the standard proof in **Verification**.

1. **Editable install, archive folder, dead files**
   - Files: `data/pyproject.toml` (new), `.github/workflows/*` (add the install step), `data/README.md` ("Python environment" section), `data/archive/README.md` (new), `data/mypy.ini`, `data/run_tests.sh` if needed; `git mv` of `init-test-db.sql`, `old_data/import_all.sh`, `old_data/scripts/migrate_rename_model_uns.py`; `git rm` of the two tracked `election_data/` files.
   - What: add a `pyproject.toml` with `packages = []` and `py-modules = []`, installed with `pip install --no-deps -e . --config-settings editable_mode=compat`. That puts `data/` on the path, and later new top-level folders need no reinstall (confirmed in isolation on 2026-11-08). Add `archive/` to mypy's exclude. Remove mypy's ghost `geoalchemy2` and `psycopg2` sections. Capture the baselines listed in Verification. Add the install command to `data/README.md`'s setup steps, with the one-time step for an existing venv (`cd data && ./election_data/bin/pip install --no-deps -e . --config-settings editable_mode=compat`), so the setup docs are right from the first commit.
   - Functions: none.
   - Depends on: none.
   - Acceptance: from outside `data/`, the venv's Python can `import db`. CI config installs the package. Baselines saved.
   - Tests: a new test that `archive/` is excluded from pytest collection and mypy's file set; existing suite unchanged.
   - Manual review: start the console from the builder's worktree (`cd data && ./election_data/bin/python server.py` on another port if 5055 is taken) and check the home page loads.

2. **Rename `models.py` to `schema.py`; make `models/` a package**
   - Files: `data/models.py` to `data/schema.py`, new `data/models/__init__.py`, `data/mypy.ini` (`files = .`, drop bare-runner sections as they go), callers in `callers.md` Piece 2, `models/{westminster,holyrood,us}/*`, tests importing runners or `_common`.
   - What: rename first, then add the package, in one commit: adding `__init__.py` while `models.py` exists breaks 90 imports. Convert bare imports of `run_uns_model`, `run_holyrood_uns_model`, `run_us_*_model`, `_common` to `models.<x>.<module>` everywhere at once, and remove the runners' `sys.path` lines. Update patch strings that name those bare modules.
   - Functions: none changed.
   - Depends on: 1.
   - Acceptance: `import models.westminster.run_uns_model` works; no bare runner imports remain; runner dry-runs still work by path.
   - Tests: existing runner suites, unchanged in what they assert; a new test that each runner imports as a package module and that one module object is shared (no double identity).

3. **`old_data/files` to `inputs/`**
   - Files: `old_data/files/{westminster,holyrood,usa}` to `inputs/{westminster,holyrood,us}`; path references in `callers.md` ("`old_data/files`"), including `scripts/rebuild_database.py:34-36`, `export_elections.py:119`, `us_results/cli.py:103`, `current_senate.py:67-70`, `scrape_holyrood_2026.py:53`.
   - What: move the data, rename `usa` to `us`, and update every path. No code logic changes.
   - Depends on: 1.
   - Acceptance: `rebuild_database.py --dry-run` differs from baseline only in paths; `grep -r old_data/files` finds only history docs.
   - Tests: existing importer tests; path-literal tests updated.
   - Model: claude-sonnet-5-5

4. **UK importers and poll importers**
   - Files: as `callers.md` Piece 4: `old_data/scripts/{import_parties,import_region_populations}.py`, `old_data/scripts/{westminster,holyrood}/*`, `scripts/by_election_import.py`, `polls/importers/**` (with `us_geography.py` to `core/`, and a new `core/__init__.py`), `console/importers_registry.py` to `importers/polls/westminster/registry.py`.
   - What: move, add `__init__.py` files, convert imports, and remove the 18 poll-importer `sys.path` lines and the old_data ones in moved files. Replace `spec_from_file_location` test loads with normal imports. `test_import_topojson.py:380-386` fakes the old depth and needs rewriting. `import_topojson.py:271`, `import_general_elections.py:48`, `import_holyrood_elections.py:72`, `import_holyrood_seats.py:36` are `__file__`-depth lines to check.
   - Depends on: 1, 3.
   - Acceptance: console poll pages and importer tests pass; `create_app` builds; rebuild proof passes.
   - Tests: existing poll-importer and console poll tests, with loads converted; a new test that `importers/polls/westminster/registry.py` still lists all 11 pollsters.

5. **US importers**
   - Files: as `callers.md` Piece 5: `old_data/scripts/usa/*`, `scripts/us_results/*`, `scripts/migrate_us_electoral_votes.py`, `console/blueprints/us_results.py`, `mypy.ini`.
   - What, in order:
     - Move `UNOPPOSED_NOMINAL_VOTES` into the NYT `convert.py` first, then remove the import at `convert.py:71-76`. `test_us_results_convert.py:249-252` pins it to the 538 constant, so change that to pin the value.
     - Archive the five converters. `convert_wiki_presidential` chains through `convert_538_presidential` to `convert_538_house.aggregate_unit`; they stay together in `archive/` and aren't kept running.
     - Move the live modules per the proposed tree. Remove `mypy_path = old_data/scripts/usa`.
     - Fix import-order reliance: `console/blueprints/us_results.py:104-105` works today only because an earlier import extends `sys.path`.
     - Update the depth lines (`cli.py:54`, `convert.py:71`, `current_senate.py:56`, `importers/results/us/*`), `test_us_results_current_senate.py:766` (pins the folder name "usa"), and `rebuild_database.py:51-53,161`.
     - Split `test_usa_{house,senate,presidential}.py`: converter tests retire; importer tests stay. Retire `test_convert_wiki_presidential.py`.
   - Functions: none changed.
   - Depends on: 3.
   - Acceptance: importing `console` in a fresh interpreter works with no `sys.path` setup, in either blueprint import order; no live code imports from `archive/`; the 21 unopposed House seats still produce their totals.
   - Tests: `test_us_results_*` and `test_console_us_results.py` unchanged in what they assert; a new test that `us_import` imports cleanly in a fresh subprocess; a new test pinning the `UNOPPOSED_NOMINAL_VOTES` value.

6. **Export package**
   - Files: `scripts/export_elections.py` and `scripts/export/*` to `export/`, `console/paths.py`, `rebuild_database.py:40`, `console/services/uncertainty_export.py:37-39`, `models/us/_common.py:108`, `models/us/run_us_senate_model.py:53`, callers in `callers.md` Piece 6.
   - What: move and convert imports. Keep the file name `export_elections.py`: tests compare basenames.
   - Depends on: 1.
   - Acceptance: `export_elections.py --dry-run` matches baseline; `test_export_golden.py` unchanged.
   - Tests: existing export suites.
   - Model: claude-sonnet-5-5

7. **Clear out `scripts/`**
   - Files: `scripts/migrate_*` except `migrate_us_electoral_votes` (moved in piece 5), `backfill_poll_bases.py`, `repair_*.py`, `reset_us_poll_matchups.py`, `poll_sources.py`, `scrape_holyrood_2026.py`, new `core/migration_helpers.py`, matching tests, `console/blueprints/uncertainty.py:310`, `model_support/persistence.py:317`, `old_data/`-origin `import_holyrood_elections.py:309`.
   - What:
     - Extract `open_database`, `Step`, `is_applied`, `column_exists` and `schema_object_exists` from `migrate_add_us_poll_scope.py` into `core/migration_helpers.py`. Live users are `migrate_us_electoral_votes.py` and `calibrate_uncertainty.py:93`.
     - Archive the rest. Point user-facing error messages that name archived scripts at their new `archive/` paths.
     - Retire the tests that only cover archived scripts: `test_migrate_add_{poll_bases,model_uncertainty,us_poll_scope}.py`, `test_migrate_us_result_status.py`, `test_migrate_drop_seat_geometry.py`, `test_backfill_poll_bases.py`, `test_repair_*.py`, `test_reset_us_poll_matchups.py`, `test_scrape_holyrood_2026.py`.
     - `old_data/` should now be empty: remove it.
   - Depends on: 4, 5.
   - Acceptance: `scripts/` holds only `rebuild_database.py`, `rebuild_model_trends.py`, `backup_db.py` and modules still to move in piece 8; `old_data/` is gone; pytest IDs differ from baseline only by the retired tests.
   - Tests: a new `test_migration_helpers.py` covering the extracted helpers; existing `test_holyrood_elections_import.py`.

8. **`model_support/` into `models/`**
   - Files: `model_support/*` to `models/support/*`; `westminster_*.py` to `models/westminster/`; `scripts/{calibrate_uncertainty,validate_westminster_uncertainty,build_final_poll_errors}.py` to `models/westminster/`; callers in `callers.md` Piece 8; the 17 `model_support.*` patch strings; `models/westminster/uncertainty_validation_report.json`.
   - What: move and convert imports. Update the hashed list at `validate_westminster_uncertainty.py:99-108` to the new paths. Depth hazards: `trends.py:131` (`parents[2]` becomes `parents[3]`, and `io.py:78` mkdirs silently, so a wrong depth creates a wrong tree), `uncertainty.py:41`, `calibration.py:56-58`, `westminster_validation.py:45`, `westminster_validation_inputs.py:22`, `validate_westminster_uncertainty.py:98`. Also fix `westminster_validation.py:42`, which imports a CLI script from `scripts`.
   - Functions: `validate_westminster_uncertainty.main`'s path list only.
   - Depends on: 2, 7.
   - Acceptance: every path constant resolves to the same files as before; the regenerated report's numbers are identical apart from `identity.source_sha256`, paths and `report_hash`.
   - Tests: a new test that `trends.py`'s and each validation path constant resolve under the repo; existing model suites.
   - Manual review: compare the regenerated report with the committed one (`git diff --stat` and a JSON diff excluding `identity.source_sha256` and `report_hash`), and open Uncertainty → Validation on the builder's console.
   - **Live step (needs Phil's go-ahead at the time):** `cd data && ./election_data/bin/python models/westminster/validate_westminster_uncertainty.py`. Reads the live database read-only and writes only the report file.

9. **`core/`**
   - Files: `schema.py`, `db.py`, `config.py`, `electoral_votes.py`, `backup.py` to `core/`; 271 import lines per `callers.md` Piece 9; `.env_example`.
   - What: move and convert imports across prod and tests. `config.py:11` must become a repo-root path that still finds `<repo>/.env`, because a wrong depth fails silently and falls back to built-in defaults. `electoral_votes.py` stays one module; it isn't merged with the seed tables.
   - Depends on: 2 through 8.
   - Acceptance: the proof tool's guard finds `core.config` and passes; no root library modules remain, only `server.py`.
   - Tests: a new test pinning `core.config`'s `.env` path to the repo root; a new test that `test_us_results_fetch.py:206`'s root derivation still holds.

10. **Final path-hack sweep, config and docs**
    - Files: remaining tests with `sys.path` lines, `data/mypy.ini`, `README.md` (basic commands and Data section), `data/README.md` (the Layout table, rewritten to the new tree), `data/docs/{database,us-results,us-polls-and-forecasts,westminster-uncertainty,export,models}.md`, `electionmapslogic/README.md`, `uselectionmaps/README.md`, `AGENTS.md:33,34,53`, `.env_example:16,24`, `electionmaps/data/map-modes.md:7,85`, usage docstrings, the comment at `polltracker.js:158`. `callers.md` lists each mention.
    - What: remove every remaining `sys.path` adjustment (prod and tests) and mypy's bare-module sections. Update paths and commands across the docs. Where a doc gives an archived script as a step to run (`data/docs/us-polls-and-forecasts.md:163-164,174-175`, `data/docs/westminster-uncertainty.md:75-79`, `data/docs/us-results.md:100-101`, `uselectionmaps/README.md:20`), say it is archived and may no longer run, and don't present it as a current step. Fresh setups rely on the rebuild.
    - Depends on: 9.
    - Acceptance: `git grep -n "sys.path\.\(insert\|append\)" data -- ':!data/archive'` finds nothing; docs name only paths that exist; relative links in `README.md`, `data/README.md` and `data/docs/*.md` resolve.
    - Tests: a new test failing if a `sys.path` adjustment returns outside `archive/`.
    - Manual review: run the main commands in `README.md` and `data/README.md` from the builder's worktree (console start, `export_elections.py --dry-run`, `rebuild_database.py --dry-run`, a runner dry-run).

## Checkpoints

- [x] 1: Editable install, archive folder, dead files, baselines
- [x] 2: `schema.py` and `models/` package
- [x] 3: `inputs/`
- [x] 4: UK and poll importers
- [ ] 5: US importers
- [ ] 6: `export/`
- [ ] 7: `scripts/` cleared, `old_data/` removed
- [ ] 8: `models/support/`, report regenerated with go-ahead
- [ ] 9: `core/`
- [ ] 10: Path hacks gone, docs updated

## Verification

**Baselines, captured in piece 1 from unmodified master behaviour:** `pytest --collect-only -q` IDs; `create_app().url_map` as sorted rules; `export_elections.py --dry-run`; `rebuild_database.py --dry-run`.

**After every piece, from the worktree:**
- `cd data && ./run_tests.sh` (strict mypy gates it, then pytest).
- `"$(python3 ~/.claude/lib/planner.py tools)/rebuild-proof.sh"`. Rebuilds a scratch restore of the newest archive from a copy of the worktree, then requires 43 of 43 steps, unchanged table counts, identical per-election vote counts and totals, and byte-identical `electionmaps/data` and `uselectionmaps/data`. About 76 seconds. It never touches the live database: the copy has its own `.env`, `PYTHONPATH` makes the copy win over the editable install, and a guard refuses to run otherwise. If it fails on a difference that is newly-published upstream data rather than the change, run it on master at the same moment to tell them apart.
- `./election_data/bin/python -c "from console import create_app; create_app()"`; compare `url_map` with baseline.
- Compare pytest IDs with baseline; only tests the piece retires may disappear.
- `export_elections.py --dry-run` and `rebuild_database.py --dry-run` differ only in paths.
- From the repo root: `npm test`, `git diff --check`. Stage explicitly: `run_tests.sh` rewrites the tracked `model_output_trends.json`.

**What the rebuild proof doesn't cover:** poll importers and model runs. Those are covered by the tests, runner dry-runs and the import checks in each piece. `--dry-run` is the only way to run a real importer or model here: never redirect a script with `DATABASE_PATH`, since a `.env` overrides it.

**After merging:** Phil's main venv needs the one-time editable install given in `data/README.md` (added in piece 1); until then, scripts fail loudly with `ModuleNotFoundError`.

## Notes

- Coverage target for each piece is its moved lines plus any new tests; the moves themselves are exercised by the existing suite.
- 2026-11-08: Updated for `bd9d4879`, which split the root README into `data/README.md`, `data/docs/*.md` and per-area READMEs. Piece 1 now documents the install, piece 10's docs list follows the new files, and `callers.md` was regenerated at that commit. `findings/*.md` keep their older README line numbers as history.
- 2026-11-08, piece 1: built on Sonnet (experiment), inline (small piece). One analysis round: style raised 3 doc fixes (root README install line, `archive/` row in the Layout table, Python version), correctness raised the same version mismatch and an over-claim in the data/README install paragraph; all fixed (`requires-python` loosened to `>=3.10` to match the docs). Security and common-mistakes clean. Verifier not spawned: every finding was a checked doc fact, fixed rather than disputed. No changed-lines coverage run: the only changed Python is the new test file.
- Baselines for later pieces are in `baselines/` in this folder: `pytest-ids.txt` (6569 IDs), `url-map.txt` (75 rules), `export-dry-run.txt`, `rebuild-dry-run.txt`. Piece 1 adds 4 tests (`test_project_layout.py`), so pytest IDs are 6573 now.
- `tests/conftest.py:15` still does a `sys.path.insert` that masks the editable install in tests; piece 10 removes it. `tests/test_project_layout.py` uses `import db`; piece 9 must update it when `db.py` moves. (The planner tools' `setup-worktree.sh` runs the editable install once `data/pyproject.toml` exists.)
- 2026-11-08, piece 2: built on Sonnet (experiment), inline. Dropping mypy's bare-runner `ignore_missing_imports` sections made mypy type the runners for the first time, which surfaced ~35 latent test typing errors (redundant `cast`s, invariant dict annotations, `SimpleNamespace` passed as `Namespace`, names read via `runner.X` that the runner only re-exports). Fixed in the tests with no assertion changed (two `is not None` asserts added). `scripts/calibrate_uncertainty.py` imported `run_uns_model` by bare name and wasn't in `callers.md`; converted here. Analysis loop: one round of five checks. Style found unsorted rewritten imports and leftover indentation (fixed); correctness, security, common-mistakes clean; coverage adequate. Verifier not spawned (nothing disputed).
- Hazard for later: the editable install's `.pth` sits last on `sys.path`, after site-packages. A pip package named `schema`, `config` or `db` would silently shadow ours when a script runs by path. None is installed now; piece 9's `core/` removes the top-level collision risk for `config` and `db`.
- 2026-11-08, piece 3: built on Sonnet (experiment), inline. Beyond the plan's list: `current_senate.py:70`, `us_results/cli.py:103` and `scrape_holyrood_2026.py:52` constants, usage docstrings in the importers and converters, and `test_us_results_current_senate.py:764` (it pins the data folder's name, so it became "us" here rather than in piece 5). Added `tests/test_inputs_paths.py` (11 tests) pinning each default path and the three `parents[3]` constants. One round of the five checks: style asked for line widths, a Layout-table row for `inputs/` and a stale comment (fixed), coverage asked for a pin on the scraper's output folder (added); security, correctness and common-mistakes clean (common-mistakes appended one general entry about `__file__`-depth paths). `data/archive/import_all.sh` still names the old folder: left as an archived record.
- The rebuild proof reads `inputs/` directly, so a wrong path fails its 43-step check. `rebuild_database.py --dry-run` differs from the baseline only in those paths (checked by rewriting the baseline's paths and diffing: empty).
- 2026-11-08, piece 3: final full run on the committed tree: 6597 passed (6586 earlier plus the 11 new tests in test_inputs_paths.py).
- 2026-11-08, piece 4: built on Sonnet (experiment), inline. Moves per the plan; `core/` holds only `__init__.py` and `us_geography.py`. 24 `sys.path.insert` lines deleted from moved files; the six path-based test loads (`test_import_parties`, `test_import_region_populations`, `test_import_topojson`, `test_westminster_import`, `test_holyrood_elections_import`, `test_holyrood_seats_import`) now import by package name (the Protocol typing shims stay, with `cast`); the US loads are left for piece 5. `rebuild_database.py` gained `IMPORTERS_DIR`; `console/paths.py`'s Holyrood poll script path and the by-election blueprint follow. New `tests/test_importer_packages.py` (35 tests: registry lists the 11 pollsters, every importer imports in a fresh interpreter with no module under a non-package name, runnable importers run `--help` by path, old locations gone, every rebuild step and console script exists). Final run on the committed tree: strict mypy clean, 6632 passed; rebuild proof 43/43. Committed 2e2111c0. One round of the five checks: security and correctness clean; style asked for import re-sorting in 7 files, unused imports, stale comments and two docs (fixed); coverage asked for pins on rebuild-step/console script existence and a stricter fresh-interpreter check (added); common-mistakes appended one general entry ("Leftovers after deleting a workaround").
- 2026-11-09, piece 5: built on Sonnet (experiment), inline. Moves per the plan: `importers/results/us/{us_import,regions,import_*_elections}.py`, `nyt/` (today's `scripts/us_results/`), `geometry/` (TopoJSON builders, `house_2026_sources`, `check_redistricting`), `importers/seed/{us_electoral_votes,migrate_us_electoral_votes}.py`; the five converters are in `archive/` unchanged, with their reasons in the archive README. `UNOPPOSED_NOMINAL_VOTES = 100` now lives in `nyt/convert.py` and is pinned by value. `old_data/` is gone entirely (it held only `scripts/usa`), so piece 7 no longer has it to remove. Depth lines `parents[2]` become `parents[4]` in `nyt/cli.py` and `nyt/current_senate.py`; `DEFAULT_SNAPSHOT` is now pinned too. `console/blueprints/us_results.py` no longer relies on an earlier import extending `sys.path`. `mypy.ini`: `mypy_path` removed; the builder exclusion follows the new path. One non-move fix: `build_presidential_topojson.py` and `build_senate_topojson.py` crashed on `--help` (bare `%` in an argparse help string, already escaped in the house builder); now `%%`. Retired 38 test IDs, all converter tests of archived code (see the review request); added `tests/test_us_importer_packages.py` (26) and one `DEFAULT_SNAPSHOT` pin. Final run on the staged tree: strict mypy clean, 6631 passed; rebuild proof 43/43. The rebuild dry-run differs from baseline only in the piece 3 input paths. `scripts/migrate_us_electoral_votes` still imports `scripts.migrate_add_us_poll_scope` until piece 7 extracts `core/migration_helpers.py`.
- Two stale path comments remain on purpose: `scripts/poll_sources.py` and `scripts/calibrate_uncertainty.py` keep their own `sys.path` inserts (they move or are archived in later pieces).
