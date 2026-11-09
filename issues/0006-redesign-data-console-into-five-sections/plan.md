# Plan: redesign-data-console-into-five-sections

**Build model:** claude-opus-5-5. The routes carry safeguards that are easy to lose, and the background runner is concurrent code. Piece 11 is docs only and marked for Sonnet.

## Context

Redesign the local Flask data console around five uniform sections (GitHub #83).

**Prerequisite:** built after 0005 (GitHub #82) has merged, branching from the master that contains it. Paths below use the post-0005 layout: `model_support` is `models/support`, `scripts/export*` is `export/`, `polls/importers` is `importers/polls`, `console/importers_registry.py` is `importers/polls/westminster/registry.py`. Code line numbers are at `35e5326c`, before that move: re-grep. Docs references are at `bd9d4879`, where the root README became a short overview and the console's docs moved to `data/README.md` and `data/docs/`; the README line numbers in `findings/console.md` predate that.

**Read first, on the planner branch:** `../0005-restructure-data-folder-and-console/findings/console.md` has every route, method, template, test assertion and inconsistency. `decisions.md` has the ledger.

**Facts this plan relies on:**
- `map.parliament` already takes exactly five values: `westminster`, `holyrood`, `us_house`, `us_senate`, `us_presidential` (`export_elections.py:129-132`). Polls have `map_id`. So one value identifies a section for polls, outputs and export.
- The US scraper already filters by contest (`us_wikipedia_polls.py:216-265`): `house_national`, `house_districts`, `senate_races`, `president`.
- `run_us_chamber_and_export` already runs a single chamber (`console/services/us_models.py`).
- `_common.py:2634-2686` gives the US runners the same run parameters as Holyrood.
- The two UK sections share one site page and manifest, as do the three US sections, so a partial export must preserve the other sections' entries.

**Settled decisions (don't re-derive):**
- Five sections: Westminster, Holyrood, House, Senate, President. Family-first was superseded by this.
- Each section: auto import polls, view polls, run models with parameters, view model runs, delete for polls and runs.
- "Import poll" is Westminster only.
- Section-specific tools stay in their section: by-elections for Westminster, matchups per US section, NYT results for House and Senate, Current Senate for Senate.
- URL shape: `/<section>/polls`, `/<section>/polls/import`, `/<section>/model`, `/<section>/outputs`. No redirects for old URLs: the console is local and single-user, so old addresses simply return 404 and run nothing.
- One global export page: tick sections, then results, current forecast or metadata. Dry run on by default.
- Each model run form has "export when done", on by default for real runs. What it exports stays as today: Westminster `--current-simulation`, others full.
- No "run all US" action.
- Rebuild, backup and restore live on an admin page.
- Run log and background job records are files beside the database (`<database folder>/console-runs/`), one per run, pruned to a set number. No schema change.
- Background runs with live progress come last.
- Must stay exactly: every transparency view and number; poll n, n source, n_eff, MoE; `/polls/coverage`; every `us_results_preview` section; queue summaries including Scrape Diagnostics; raw stdout, stderr, exit code and command on every run; hub export preview; interrupted-run notes and `NO_MATCHUP_NOTE`; `same_origin_only`, `TRUSTED_HOSTS`; `backup_after_write`; preview tokens and their `"type"` tags; `expected_index`; `_serialised` locks; `model_run_slot` at every call site; dry-run default true; existing confirms and their tested text.
- Never link a queue `/finish` as a plain `<a>`, because GET mutates.
- Out: new Holyrood and US diagnostic pages (own issue later) and new uncertainty models.

**Assumed, for Phil to correct at plan approval:**
- One background job runs at a time across the console. A second start is refused with a link to the running one.
- Holyrood's auto import stays one-click and unreviewed, as today. A review queue would be new importer work.
- "Rebuild Site Data" and `/us/results/export` are replaced by the export page.
- A run interrupted by a console restart is marked "interrupted" when the console next starts, not resumed.

## Current structure

```
console/
  blueprints/  flat, 14 mixing families: poll_import (Westminster), westminster, holyrood, us,
               us_poll_import, us_results, by_elections, polls, site, db_admin, home, uncertainty*
  services/    flat: runner, preview, trends, model_outputs, wikipedia_queue, us_poll_queue, us_models, us_matchups
  templates/   37 flat; near-duplicate run, queue and summary pages
  static/style.css
```

No navigation in `base.html`. Home holds the only links and several POST forms.

## Proposed structure

```
console/
  __init__.py csrf.py db.py forms.py paths.py
  sections.py   the five sections: slug, label, parliament, capabilities
  nav.py        one nav structure, exposed to base.html
  shared/       runner, run_log, jobs, preview, poll_queue, trends, model_outputs
  westminster/  model, outputs, poll_import, by_elections, uncertainty*
  holyrood/     model, outputs, poll_import
  us/           section views, poll_import, results, matchups, models
  polls/        poll list, detail, coverage, delete
  admin/        admin page, export page, db_admin
  templates/    mirrored, plus _macros/
```

```
Home           links to the sections and Admin
Westminster    Polls (View, Auto import, Import poll, Coverage) · By-elections · Model · Outputs · Uncertainty
Holyrood       Polls (View, Auto import) · Model · Outputs
House          Polls (View, Auto import) · Matchups · Results · Model · Outputs
Senate         Polls (View, Auto import) · Matchups · Results · Current Senate · Model · Outputs
President      Polls (View, Auto import) · Matchup · Model · Outputs
Export         one page
Runs           log of every run
Admin          Rebuild database · Backup · Restore
```

## Approach

Structure first, then behaviour, so each visible change lands on a stable base. A section registry drives navigation, section pages and filters, so "uniform" is enforced by one data structure and not by convention. The queue and preview safeguards are moved, not rewritten.

Rejected:
- **Top level by pipeline stage:** a single Import page would merge a pollster URL form, a one-click button and a race-level queue, and matchups and uncertainty have no natural stage.
- **Four families with US as one:** Phil chose five sections.
- **Run log in a database table:** needs a live schema migration and writes during runs.
- **New diagnostics in this issue:** a separate follow-up keeps this reviewable.

## Pieces

1. **Section registry and internal layout**
   - Files: new `console/sections.py`; `console/blueprints/*` and `console/services/*` moved per the proposed tree; the monkeypatch strings and imports naming `console.blueprints` or `console.services` across `tests/` (about 100 strings at `e64d9889`: `test_console_*.py`, `console_fixtures.py`, the two queue test files, and `test_model_consumers`, `test_presidential_ev_consumers`, `test_model_uncertainty_persistence`, `test_us_model`, `test_project_layout`, `test_export_package`, `test_us_importer_packages`); `console/__init__.py`; `mypy.ini`; docstring paths in `importers/polls/types.py` and `importers/polls/westminster/wikipedia_index.py`.
   - What: move modules with no route, endpoint or template change, each route file whole (`westminster.py` and `holyrood.py` are split into model and outputs in piece 3, when their outputs routes are rewritten). `home.py` goes to `console/home.py` and `site.py` to `console/admin/site.py`. `mypy.ini` gets one `type-var` section per new package holding routes and keeps the `arg-type` exception for the US routes; the pin at `tests/test_project_layout.py:20` follows, with no blanket `console.*` section. Add the registry mapping each section to its slug (`westminster`, `holyrood`, `house`, `senate`, `president`), label, `parliament` value and capabilities. Replace the `startswith('westminster.')` test in `model_output_detail.html:18` with an explicit registry flag, and centralise the "UK Constituencies post 2022" constant, repeated at `poll_import.py:74`, `uncertainty_runs.py:20`, `westminster.py:139`. Keep the `db_admin.` endpoint prefix: `backup_after_write` depends on it (`__init__.py:79`).
   - Functions: `create_app`'s blueprint registration.
   - Depends on: 0005 merged.
   - Acceptance: `url_map` identical to baseline; `backup_after_write` still skips `db_admin.` endpoints.
   - Tests: existing suite with patch paths updated; a new registry test that every section's `parliament` is one of `map.parliament`'s values and that labels are unique.

2. **Navigation and section pages**
   - Files: new `console/nav.py`, `templates/base.html`, new section landing templates, `home.html`, `static/style.css`, `tests/test_console_{us,site,db_admin,us_results,uncertainty}.py`.
   - What: top nav and section nav from `nav.py`, built from `SECTIONS` and `Capability`, breadcrumbs. The current section comes from a table of endpoints, using the `chamber` value for the shared US endpoints, not from the URL. Landing pages at `/westminster`, `/holyrood`, `/house`, `/senate`, `/president`, and an Admin page at `/admin`. Home becomes links only, and its POST forms move to their sections or Admin (the rebuild-all-history option moves with the run form in piece 5). Until piece 5 replaces it, the one "Run US models" form appears on all three US landing pages through one shared partial, confirm text intact. `/polls` and poll detail sit under a top-level "All polls" link with no current section; `/polls/coverage` sits under Westminster. Nav lists only pages that exist: Export arrives in piece 6 and Runs in piece 9.
   - Depends on: 1.
   - Acceptance: every page shows navigation; home has no POST forms; existing confirm text survives the move; the 75 existing URL rules are unchanged and only the six new ones are added.
   - Tests: crawl from `/` over a seeded temporary database with no 4xx or 5xx; every parameterless GET rule reachable from `/`; every `nav.py` entry resolves and renders; `/` has no `method="post"`.
   - Manual review: `cd data && ./election_data/bin/python server.py` from the worktree, with nothing else on port 5055 (`server.py` fixes the port). Walk each section and Admin in a browser, including narrow width.

3. **Template and route consolidation**
   - Files: new `templates/_macros/{run_result,queue,forms,meta}.html`, `_uncertainty_nav.html`; `model_run.html` replacing `holyrood_model_run.html`; `wikipedia_*` and `us_poll_queue*` templates; the outputs route code in `westminster.py:256-367`, `holyrood.py:282-372`, `us.py:432-504`; `shared/runner.py`.
   - What: one data-driven run form; one run-result macro with the label as a parameter so tested strings survive; shared queue macros; generalise `_register_chamber_routes` to all five sections' outputs, keeping endpoint names; add `run_model_then_export`. Move Chart.js and the inline script to `static/`, with Chart.js pinned or vendored so charts don't vanish offline. Reformat the long uncertainty templates only. Calibration and Validation join the uncertainty sub-nav.
   - Depends on: 1.
   - Acceptance: HTML for each consolidated page contains every field and number it had; existing assertions pass unchanged.
   - Choices: vendor one pinned Chart.js into `console/static/`, replacing the unpinned CDN tag at `model_outputs.html:88`. Merge the run form and result macro first, then the queue macros one at a time under the existing assertions, and stop short of anything that would change the rendered HTML. Check the uncertainty reformat by comparing rendered HTML with whitespace collapsed.
   - Tests: existing suite; a new test that every uncertainty page shows the sub-nav; a new test that the run-result macro renders command, exit code, stdout and stderr.
   - Manual review: `cd data && ./election_data/bin/python server.py` from the worktree, with nothing else on port 5055. Check the charts draw on Westminster Outputs and one other section's outputs page.

4. **One URL shape**
   - Files: all section routes, tests with URL literals (about 240 per the findings).
   - What: rename to the settled shape, keeping endpoint names. No redirects: old URLs have no route, so they return 404 and run nothing. New URLs: `/import` and its Wikipedia queue become `/westminster/polls/import` and paths beneath it; `/polls/coverage` becomes `/westminster/polls/coverage`; `/by-elections` becomes `/westminster/by-elections`; `/models/run` becomes `/westminster/model`; `/models/outputs` becomes `/westminster/outputs`; `/holyrood/run-model` becomes `/holyrood/model`; `/holyrood/import-polls` becomes `/holyrood/polls/import`; `/us/<chamber>/outputs` becomes `/<chamber>/outputs`; the matchup pages become `/house/matchups`, `/senate/matchups`, `/president/matchup`. Left for piece 5: `/us/import…`, `/us/results…`, `/us/run-models` and the matchup save action `/us/matchups/<map>/<seat>`. Left alone: `/site/*`, `/db/*`, `/polls`, `/polls/<id>`. Rule-set checks at `test_console_us.py:106-141,834` and `test_console_csrf.py` update with them.
   - Depends on: 2, 3.
   - Acceptance: every section page moved here follows the shape; no old POST route for them exists; the pages render the same apart from their URLs.
   - Tests: every `<form method=post action=…>` resolves to a POST rule.

5. **Section parity**
   - Files: `console/us/*`, `console/polls/*`, `console/holyrood/*`, `shared/runner.py`, the run form, `console/forms.py`.
   - What:
     - `/<section>/polls` lists that section's polls, filtered by `map.parliament`, with the same table and numbers as today. `/polls` stays as the all-polls view.
     - House, Senate and President each get a run form with the US runners' parameters and a dry run default. Remove `/us/run-models`. The "Rebuild all history" option and its confirm move to each form, keeping the tested confirm text.
     - Add "export when done" to all five run forms.
     - Auto import opens the existing US queue limited to that section's contests. Queue finish runs only that section's model; automatic matchup tracking is unchanged.
     - `/us/import…`, `/us/results…`, `/us/run-models` and the matchup save action `/us/matchups/<map>/<seat>` move to the section shape here, with no redirects, as in piece 4.
     - Matchups, NYT results and Current Senate move into their sections. Delete for polls and runs is available in every section.
   - Functions: `run_us_chamber_and_export`, `UsQueueStartForm`, queue `finish`.
   - Depends on: 4.
   - Acceptance: every section offers the agreed actions, with Import poll only on Westminster; `model_run_slot` held on every US run path.
   - Tests: section poll lists include only their own polls; each US run form sends only its chamber's script and parameters; "export when done" off skips the export and on runs what today's flow runs; queue finish runs only the section's model; `model_run_slot` refuses a concurrent run from each form.
   - Manual review: open each section's Polls and Model pages. Do a dry-run model for one US section, and start then abandon one auto import.

6. **Global export page**
   - Files: `export/export_elections.py`, new export view and template under `console/admin/`, `console/paths.py`, export tests.
   - What: add a section filter to the script, using `map.parliament`, working with every content mode. Filtering must preserve other sections' entries in the shared page and manifest. Content options become combinable: results, current forecast, metadata. The form defaults to dry run and shows what would be written. The page replaces `/site/rebuild` and `/us/results/export`, under `model_run_slot`.
   - Functions: `partition`/`export_pages`/`_export_page`, argument parsing.
   - Depends on: 4.
   - Acceptance: with no filter, the script's output is byte-identical to today's; a one-section export leaves other sections' site files and manifest entries unchanged.
   - Tests: golden export unchanged with no filter; a one-section export under `--output-base` changes only that section's files and preserves shared manifest entries; each content combination; the form defaults to dry run; a real export requires an explicit choice.
   - Manual review: run a dry run for one section, and check the listed changes make sense.

7. **Admin page, confirms and small fixes**
   - Files: new `templates/admin.html`, `console/admin/*`, `poll_list.html`, `model_outputs.html`, matchup templates, the queue `finish` routes, `__init__.py`.
   - What: admin page for Rebuild database, Backup, Restore. Add confirms to poll and output deletes (single and bulk), matchup-page rebuilds and queue abandon. Request a backup after queue finishes that write on GET. Add the per-token lock to Westminster's finish. Fix the stale "automatic reload" copy (`import_form.html:12`, `us_poll_import.html:13`, `services/preview.py:15`) and use one word, "Export", everywhere.
   - Depends on: 4.
   - Acceptance: every destructive action has a confirm; backup requested after a GET finish that wrote.
   - Tests: each destructive form has a confirm; existing confirm text unchanged; a GET finish that writes requests a backup and one that doesn't write doesn't; Westminster finish serialises per token.
   - Manual review: in a browser, cancel each confirm on Rebuild database and Restore. Delete a disposable model run.

8. **Looks and readability**
   - Files: `static/style.css`, templates using inline styles.
   - What: `:root` colour and spacing tokens, nav styles, consistent tables and number alignment, one spacing scale, a dark mode, remove dead CSS (`.action-grid-three`, the `.topbar` leftovers) and inline `style=` attributes except the deliberate calibration bar width. Keep the classes tests assert on.
   - Depends on: 2, 3.
   - Acceptance: no inline styles beyond the exception; tables readable at narrow width.
   - Tests: a test that templates hold no `style=` beyond the allowed one. Mostly a visual piece, proven by the manual review.
   - Manual review: look over a dense page in each section (poll list, output detail, uncertainty results, run form) in light and dark, at desktop and narrow width.

9. **Run log**
   - Files: new `shared/run_log.py`, `shared/runner.py`, new `/runs` and `/runs/<id>` views and templates, section pages.
   - What: every console-launched command writes a record to `<database folder>/console-runs/<id>/` holding the command, start and end times, exit code, stdout and stderr, written to a temporary name then moved. Keep the newest N (default 200, configurable by environment variable). `/runs` lists them, filterable by section; each run page shows the full output.
   - Functions: `run_command` and its callers.
   - Depends on: 3, 5.
   - Acceptance: a run's output is viewable after leaving the page and after a restart; the elections database is untouched by logging.
   - Tests: a record is written for success, failure and timeout; pruning keeps the newest N; a corrupt record is skipped with a note, not an error; the log folder follows the configured database path, so tests can't reach the live one.

10. **Background runs with live progress**
    - Files: new `shared/jobs.py`, `shared/runner.py`, run forms, `/runs/<id>` live view, a small script in `static/`, `console/__init__.py`.
    - What: model runs and exports start in a background thread running the subprocess and streaming output into the run record. The POST redirects to `/runs/<id>`, which polls a JSON status route and shows live output, ending with the same result block as today. One job at a time; a second is refused with a link to the running job. `model_run_slot` is held for the job's whole life, and the backup request fires when a job that wrote finishes. On startup, records still marked running become "interrupted". Timeouts still apply.
    - Functions: `run_command` callers, `backup_after_write` for jobs.
    - Depends on: 9.
    - Acceptance: starting a run returns immediately; progress shows while it runs; the result is unchanged; a restart mid-run leaves a clear "interrupted" record.
    - Tests: start returns a redirect without waiting; a real trivial subprocess streams output to the record; a second start is refused while one runs; the slot is released on success, failure, timeout and crash; records left running are marked interrupted at startup; backup requested on completion of a writing job.
    - Manual review: start a Holyrood dry-run model, watch progress, leave and come back.

11. **Docs**
    - Files: new `data/docs/console.md`; `data/README.md` (its topic list, "Import polls" and "Run local server" sections); `data/docs/us-polls-and-forecasts.md:32,67,70,74,79,87,177-181`; `data/docs/us-results.md:42`; `data/docs/export.md`; `data/docs/database.md`; `README.md` (Data section); `AGENTS.md` if a console command changed.
    - What: add a console topic page covering the five sections, URL shape, export page, run log location and retention, following the README's split into topic pages. Update the old URLs and the "Run US Models" instructions, which no longer exist after piece 5.
    - Depends on: 10.
    - Acceptance: docs name only pages and paths that exist; no doc mentions "Run US Models" or an old URL; relative links resolve.
    - Tests: none worth writing; docs only.
    - Model: claude-sonnet-5-5

12. **Favicon**
   - Files: new icon under `console/static/`, `templates/base.html`, a test in `tests/test_console_nav.py`.
   - What: give the console its own favicon, linked from `base.html` so every page has it. It's a small self-contained file served from `static/`, with no external request. The design is settled in this piece's briefing.
   - Depends on: 11.
   - Acceptance: every page's HTML links the icon, and the icon is served.
   - Tests: a rendered page links the icon, and requesting the linked URL returns 200 with an image content type.
   - Manual review: `cd data && ./election_data/bin/python server.py` from the worktree, with nothing else on port 5055. Check the icon in the browser tab, in light and dark browser themes.

## Checkpoints

- [x] 1: Section registry and internal layout
- [x] 2: Navigation and section pages
- [x] 3: Template and route consolidation
- [ ] 4: One URL shape
- [ ] 5: Section parity
- [ ] 6: Global export page
- [ ] 7: Admin page, confirms and small fixes
- [ ] 8: Looks and readability
- [ ] 9: Run log
- [ ] 10: Background runs with live progress
- [ ] 11: Docs
- [ ] 12: Favicon

## Verification

Run from `data/` after every piece:
- `./run_tests.sh` (strict mypy gates it, then pytest).
- `./election_data/bin/python -c "from console import create_app; create_app()"`.
- `url_map` dump: piece 1 must match the baseline exactly. Later pieces change it only as they state.
- `git diff --check` and `npm test` from the repo root. Stage explicitly.

Rules for tests and walks:
- Any walk over the app is a pytest test, because `tests/conftest.py`'s autouse fixture is what keeps `Database()` off the live file.
- Any POST that shells out must have its runner monkeypatched, except piece 10's trivial subprocess, which doesn't touch the database.
- No browser here: the Flask test client covers routes, redirects, HTML and forms. Chart rendering, `confirm()` dialogs, select-all and layout are the manual reviews, in Phil's browser.
- No piece needs a live database write. Anything Phil does in a manual review against the live database, such as a real delete or run, is his choice at the time.

**Transparency check:** before and after pieces 3, 4 and 5, the same seeded test data must render the same numbers on each transparency page listed in Settled.

## Notes

- Follow-up, not part of this issue: full stage-by-stage diagnostics for Holyrood and US.
- 2026-11-08: Updated for `bd9d4879`, which split the root README. Piece 11 now targets `data/README.md` and `data/docs/*.md`, and adds a `data/docs/console.md` topic page.
- 2026-11-09: Rechecked piece 1 against master `e64d9889`, which contains 0005. Facts: 75 URL rules; the "UK Constituencies post 2022" constant is at `blueprints/poll_import.py:74`, `services/uncertainty_runs.py:20` and `blueprints/westminster.py:138` (the mention in `services/wikipedia_queue.py:155` is a docstring example and stays). Capture a fresh `url_map` baseline from this master at the start of piece 1.
- 2026-11-09 piece 1 (built on Sonnet, experiment): analysis loop took 2 rounds; frontier correctness and security checks came back clean, style found one over-long line and one stale "console services" phrase (fixed). Changed-lines coverage 100% (78 lines). `url_map` is identical to a fresh master baseline (75 rules).
- Piece 1 layout choices: `westminster.py` and `holyrood.py` moved whole as `westminster/model.py` and `holyrood/model.py`; US routes are `us/views.py`, `us/poll_import.py`, `us/results.py`, with services `us/{models,matchups,poll_queue}.py`; the three uncertainty route files and their three services sit in `westminster/uncertainty/` (`hub`, `runs`, `explorer`, and `run_data`, `explorer_data`, `export_preview`) because the route and service names collided; `wikipedia_queue` is in `shared/` since both Westminster and the US queue import it; `polls.py` is `polls/views.py`. `mypy.ini` globs (`console.westminster.*`, `console.us.*`, `console.admin.*`, `console.holyrood.*`, `console.polls.*`, `console.home`) also cover route-free modules inside those packages; `console.shared` has no exemption.
- Registry carries a `Capability` set per section (uncertainty, poll_import, by_elections, matchups, nyt_results, current_senate), and `model_output_detail.html` takes a `show_uncertainty_links` flag instead of testing the endpoint prefix. `BLUEPRINTS` in `test_us_importer_packages.py` now comes from the registered blueprints.
- 2026-11-09 piece 2 (built on Sonnet, experiment): 2 analysis-loop rounds. Frontier correctness and security came back clean; the fixes were from style (stale "home page" wording in `us/poll_queue.py`'s user-facing message, with its test, and in two comments), coverage (more nav tests) and common-mistakes (dropped the unused `view_args` parameter, reworded two build-process comments, pinned literal test oracles). The fresh verifier confirmed them and found two leftover comments, now fixed. `url_map`: the 75 existing rules unchanged, six GET rules added (`/westminster`, `/holyrood`, `/house`, `/senate`, `/president`, `/admin`).
- Piece 2 deliberately left the doc references to the old home page (`data/docs/us-polls-and-forecasts.md:34-35,79,87`, `data/docs/us-results.md:42-43`, `data/docs/database.md:34`, `data/README.md:84`) for piece 11, which already lists those files. Piece 11 should also name the Admin page for Backup now / Restore.
- Piece 2 choices: the registry gained `Capability.POLL_COVERAGE` and `Capability.QUEUE_IMPORT`; `nav.current_area` returns None for the US import queue, US results, `us.run_us_models` and `us.set_race_matchup`, which serve several chambers and stay sectionless until piece 5; the landing pages for the three US sections list Import polls, Matchup(s), Results and Outputs plus the shared Run US Models form.
- 2026-11-09 piece 2 follow-up: after the manual review the US section titles became "US House", "US Senate" and "US President" (slugs unchanged).
- 2026-11-09 piece 3 (built on Sonnet, experiment; implemented through two Sonnet subagents plus fixes): two analysis-loop rounds. Round 1: correctness and security clean; style, coverage and common-mistakes raised items; the fresh verifier kept eight (dead `padded` parameter, `OutputsSpec.blueprint_name`, optional `export_cwd`, a redundant validation-page link, a shadowed name, an election-id assertion, a moved `saved` fixture, and the untested Wikipedia summary sections) and dropped the rest. All eight were applied; round 2 (fresh verifier) came back clean.
- Piece 3 proof of equal output: every template the suite renders (about 650 renders) was recorded before and after, whitespace-collapsed with tokens and timestamps masked. Differences are only the deliberate ones: the model_outputs script block, the uncertainty sub-nav (new on hub, calibration and validation; two new links elsewhere), the removed duplicate "Uncertainty overview" link on the validation page, and the whitespace inside the Westminster output_csv `<option>` text.
- Piece 3 left unmerged: the queue pages' approve cards and approve-race form, the Matchup and Parsed-document cards, the "Model and Export" cards, the US Scrape Diagnostics tables (own markup), and `_macros/meta.html` (nothing worth extracting). The uncertainty templates were only reflowed, not moved onto `data_table`. `run_model_then_export` has one caller (Westminster poll import) and is the helper pieces 5's "export when done" will use.
- Piece 3 Chart.js: vendored 4.6.1 (the version the unpinned CDN tag served, published 2026-10-22), file `chart.umd.min.js` (210,082 bytes, the build jsdelivr serves), MIT licence file alongside, in `console/static/vendor/chart.js-4.6.1/`. The page's data is embedded as JSON (`outputs-page-data`) and read by `console/static/model_outputs.js`.
- 2026-11-09 piece 4 (built on Sonnet, experiment; implemented through a Sonnet subagent plus fixes): two analysis-loop rounds; correctness and security clean, the verifier kept six small items (single `<any(house,senate):chamber>/matchups` rule so `url_for` without a chamber fails, unused `LegacyUrl.values` removed, OPTIONS at old addresses now 404 like every other verb, lines pushed over 88 columns wrapped, `crawl` helper moved to `console_fixtures.py`, a pinned-literal filter in the redirect test). Proof of unchanged pages: whole-suite render comparison with the old URLs rewritten to the new shape in the baseline: 671 renders, 0 different, 60 extra from the new test file.
- Piece 4 notes: `us/views.py` keeps the `if map_name is None: abort(404)` branch in `race_matchups` only to narrow `race_map_name`'s `str | None` for mypy; the route's `any` converter makes it unreachable, so changed-lines coverage reports that one line (99%). The docs still name the old URLs (`data/README.md:84`, `data/docs/us-polls-and-forecasts.md:67,74,175,177`); the docs piece (11) owns them.
- 2026-11-09: The legacy redirect table was dropped from piece 4 before it was committed, at the user's request. Old console URLs return 404. Anything below that mentions `console/legacy_urls.py` or redirects describes that dropped version.
