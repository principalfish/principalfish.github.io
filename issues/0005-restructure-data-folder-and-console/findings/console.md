# Sweep findings: console

Read-only sweep of master at 35e5326c, 2026-11-08. Model output; the planner spot-checked the design-critical claims.

**Findings file not written.** The Write to /tmp/claude-1000/-home-philiph-workspace-principalfish-github-io/2c75bfc9-27de-45d3-9ee8-0cd820f608c2/scratchpad/findings-console.md was refused: subagents here may not write report files, and the error said to return the content instead. I did not work around that, so the file does not exist. This message therefore exceeds the 2000-character limit: the summary comes first, and the full detail you asked to have on file follows it.

## Summary

**Current structure:** 74 rules, 72 endpoints, 14 blueprints, 37 templates. `base.html` has no nav, so home is the only hub. Home is by family then rough stage; URLs/blueprints are by data type for Westminster (unprefixed) and by family for Holyrood/US.

**Top problems:**
- Three URL shapes each for run, outputs and import.
- "View Polls" sits under Westminster but lists all families.
- "Export" has seven names.
- No confirm on poll/output deletes or matchup-page history rebuilds.
- `model_run_slot` is US-only. Queue finish writes on GET, so it requests no backup.
- Near-duplicate run and queue templates; the outputs route quad is written three times.
- Long jobs block the request, and their output is never persisted.
- Holyrood/US have no intermediate-number views beyond stdout (scripts' stdout not read).

No dead links; orphans are mild.

**Recommended:** family at top (Westminster, Holyrood, United States, System), fixed stage order inside (Polls, Results/By-elections, Matchups, Model, Outputs, Uncertainty), driven by one `nav.py`. Rename only 28 misleading rules (optional, separable), keeping endpoints and modules. 302 redirects for seven GET pages only, never POSTs. Rejected: top level by pipeline stage.

**Must stay:** all transparency views, CSRF/host pinning, backup hook, preview tokens, `expected_index`, locks, dry-run defaults, existing confirms.

## Full findings

Read-only: nothing was executed against the app or any database. Paths are relative to `/tmp/claude-1000/-home-philiph-workspace-principalfish-github-io/2c75bfc9-27de-45d3-9ee8-0cd820f608c2/scratchpad/master-35e5326c/data/` unless they start with `README.md`. The route table is a static parse of `@bp.route` plus the `add_url_rule` loop in `console/blueprints/us.py`, not confirmed against a live `url_map`.

Not read in depth: model scripts' stdout content, `services/model_outputs.py` internals, the middle of `uncertainty_calibration.html`. Test line references for the uncertainty run pages were matched to routes by assertion text, so treat those as approximate.

### 1. Route inventory

`SUB` = subprocess (via `services/runner.py`). `export` = `scripts/export_elections.py` (no flags = full).

**home, `blueprints/home.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/`|`home.home` (10)|`home.html`|Read-only, no DB access|`test_console_us.py:148` needles; `:159` exact anchor HTML incl. classes; `:725-733` rebuild checkbox and exact `confirm(` onsubmit; `:841-844` matchup hrefs. `test_console_site.py:35-40` button, action, "Rebuild the DATABASE from source", "polls and model runs are preserved". `test_console_db_admin.py:172-178`. `test_console_us_results.py:286-288`. `test_console_uncertainty.py:307-308`|

**Polls, `blueprints/polls.py` (all families)**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/polls`|`poll_list` (82)|`poll_list.html`|Read-only, no map filter (`polls.py:94`); n, n source, n_eff, MoE for Westminster|`test_console_polls.py` (14 literals), "No polls have been imported yet." `:103`, row HTML `:212-213,243-248,294,306`|
|GET|`/polls/<id>`|`poll_detail` (149)|`poll_detail.html`|Read-only, matrix with per-share MoE|same file (14 literals)|
|GET|`/polls/coverage`|`poll_coverage` (253)|`poll_coverage.html`|Read-only, Westminster map only|`:763` href on list|
|POST|`/polls/<id>/delete`|`delete_poll` (275)|redirect `/polls`|Deletes poll and rows, no confirm|`:432,476,491` Location|
|GET|`/polls/<id>/csv`|`poll_detail_csv` (301)|CSV|Read-only|2 literals|

**Westminster poll import, `blueprints/poll_import.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/import`|`import_poll_form` (123)|`import_form.html`|Read-only|`test_console_poll_import.py` Location ends `/import` (`:276,291,318,467,483,506,621`)|
|POST|`/import/preview`|`import_poll_preview` (132)|`import_preview.html`|Network fetch, caches plan under token, no DB write|`:86` token regex, `:391` `<h3>Respondent Bases</h3>`|
|POST|`/import/confirm/<token>`|`import_poll_confirm` (191)|redirect poll detail|DB write; if `run_model`, SUB `run_uns_model.py` (no args, 1800 s) then SUB export `--current-simulation --output-file` (900 s)|`:538,571,766` Location `/polls/<id>`|
|POST|`/import/wikipedia/start`|`wikipedia_start` (248)|redirect queue|Scrapes Wikipedia, in-memory queue|`test_wikipedia_queue.py` (5 literals)|
|GET|`/import/wikipedia/<token>`|`wikipedia_queue` (299)|`wikipedia_queue.html`|Builds plan lazily, fetches source doc|"Wikipedia Catch-Up", "Sample size differs…", "Retry"|
|POST|`…/<token>/confirm`|`wikipedia_confirm` (339)|redirect|DB write, guarded by `expected_index`|"That step has already been actioned."|
|POST|`…/<token>/skip`|`wikipedia_skip` (403)|redirect|Cursor only, also retry|3 literals|
|GET, POST|`…/<token>/finish`|`wikipedia_finish` (435)|`wikipedia_summary.html`|GET mutates: once per queue, SUB UNS model + export (`csrf.py:17-24` documents why that is safe cross-site); POST `abandon`|"Wikipedia Catch-Up Summary", "UNS model updated.", "UNS model run failed…", "Nothing was imported."|

**Westminster model and outputs, `blueprints/westminster.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/models/run`|`model_run_form` (130)|`model_run.html`|Read-only, dry run default true|`test_console_westminster.py` (11 literals)|
|POST|`/models/run`|`model_run_execute` (151)|`model_run.html`|SUB `run_uns_model.py` with form args (1800 s); if not dry run and rc 0, SUB export `--current-simulation` (900 s)|`:421` "Simulation JSON Export"|
|GET|`/models/outputs`|`model_outputs` (256)|`model_outputs.html`|Read-only, Chart.js trends|`:527-533`, `:544` "Show Recent (30)"|
|GET|`/models/outputs/<id>`|`model_output_detail` (285)|`model_output_detail.html`|Read-only, paged seats|`:598-599,617-623`|
|POST|`/models/outputs/<id>/delete`|`delete_model_output` (318)|redirect|Deletes, no confirm|`:607,639` Location|
|POST|`/models/outputs/delete-selected`|`delete_selected_model_outputs` (340)|redirect|Bulk delete, no confirm|literals|

**Westminster uncertainty (all GET, read-only, no subprocess)**

|URL|Endpoint (file:line)|Template|Shows|Tests|
|-|-|-|-|-|
|`/westminster/uncertainty`|`uncertainty_hub` (`uncertainty.py:335`)|`uncertainty_hub.html`|Stored status, links, in-memory export preview|`test_console_uncertainty.py:210,215,221` href and exact `<p class=…>`|
|`…/calibration`|`uncertainty_calibration` (`:359`)|`uncertainty_calibration.html`|Settings, fitted tables|`:346,409-410,437,444`|
|`…/validation`|`uncertainty_validation` (`:405`)|`uncertainty_validation.html`|Saved offline diagnostics, stale flag|no plain URL literal found; check before changing|
|`…/budget[/<id>]`|`uncertainty_runs.budget` (`uncertainty_runs.py:51-52`)|`uncertainty_budget.html`|SD decomposition, poll provenance|`test_console_uncertainty_runs.py:80-105`|
|`…/results[/<id>]`|`uncertainty_runs.results` (`:74-75`)|`uncertainty_results.html`|Odds, ranges, histograms, competitive seats|`:87-89,551-557`|
|`…/history`|`uncertainty_runs.history` (`:90`)|`uncertainty_history.html`|Odds history with date filter|`:546`|
|`…/sims`|`simulations` (`uncertainty_explorer.py:47`)|`uncertainty_simulations.html`|Filter draws|`:437,692`|
|`…/sims/<i>`|`simulation` (`:62`)|`uncertainty_simulation.html`|One draw|`:161,168,242-244`|
|`…/sims/<i>/seat/<s>`|`simulation_seat` (`:124`)|`uncertainty_simulation_seat.html`|Exact seat decomposition|`:745`|
|`…/sims/seat/<s>`|`seat` (`:141`)|`uncertainty_seat.html`|Seat across simulations|`:510`|

**By-elections, `blueprints/by_elections.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/by-elections`|`by_election_form` (22)|`by_election_form.html`|Read-only|`test_console_by_elections.py:186`|
|POST|`/by-elections/preview`|`by_election_preview` (43)|`by_election_preview.html`|Fetch and plan, no DB write|`:116` token regex|
|POST|`/by-elections/confirm/<token>`|`by_election_confirm` (83)|`command_result.html`|DB write (in-process `scripts/by_election_import`), then SUB full export (900 s)|`:197,215,260,275,302,386,421` Location `== "/by-elections"`; `:351`|

**Holyrood, `blueprints/holyrood.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|POST|`/holyrood/import-polls`|`holyrood_import_polls` (47)|`command_result.html`|SUB `holyrood_wikipedia_import.py` twice (constituency, `--ballot list`, 300 s each). DB write, no preview|`test_console_holyrood.py:267` `<h2>Import Scottish Polls</h2>`, `:236` Location `/`|
|GET|`/holyrood/run-model`|`run_holyrood_model_form` (164)|`holyrood_model_run.html`|Read-only|10 literals|
|POST|`/holyrood/run-model`|`run_holyrood_model` (188)|`holyrood_model_run.html`|SUB `run_holyrood_uns_model.py` (1800 s; dry run adds `--dry-run --no-output`), then if real SUB full export|`:646` "Static Data Export"|
|GET|`/holyrood/outputs`|`holyrood_outputs` (282)|`model_outputs.html`|Read-only|`:748-753,763`|
|GET|`/holyrood/outputs/<id>`|`holyrood_output_detail` (308)|`model_output_detail.html`|Read-only|`:814-857`|
|POST|`/holyrood/outputs/<id>/delete`|`delete_holyrood_output` (338)|redirect|Deletes, no confirm|`:832,873`|
|POST|`/holyrood/outputs/delete-selected`|`delete_selected_holyrood_outputs` (352)|redirect|Bulk delete, no confirm|literals|

**US, `blueprints/us.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|POST|`/us/run-models`|`run_us_models` (92)|`command_result.html`|SUB House, President, Senate runners (300 s each, or 3600 s with `--rebuild-history`), then export. Holds `model_run_slot`|`test_console_us.py:517-518,565-567,654,717-719`; `test_console_csrf.py:96,118`|
|GET, POST|`/us/president/matchup`|`president_matchup` (172)|`us_matchup.html` or `command_result.html`|POST DB write; if ticked, SUB President rebuild + export, no confirm|`:872,884,962,1511-1513`|
|GET|`/us/matchups?chamber=`|`race_matchups` (224)|`us_race_matchups.html`|Read-only, 404 for other chamber|`:1079,1109-1111`|
|POST|`/us/matchups/<map>/<seat>`|`set_race_matchup` (257)|redirect or `command_result.html`|DB write; if ticked, SUB chamber rebuild + export, no confirm|`:1133,1275,1322,1538,1569,1594`|
|GET|`/us/<chamber>/outputs`|`<chamber>_outputs` (`:509`)|`model_outputs.html`|Read-only (3 rules)|`:178-179,190-191,268-269`; rule set `:135-141`|
|GET|`/us/<chamber>/outputs/<id>`|`<chamber>_output_detail` (`:515`)|`model_output_detail.html`|Read-only (3 rules)|as above|
|POST|`/us/<chamber>/outputs/<id>/delete`|`delete_<chamber>_output` (`:521`)|redirect|Deletes, no confirm (3 rules)|`:199`|
|POST|`/us/<chamber>/outputs/delete-selected`|`delete_selected_<chamber>_outputs` (`:527`)|redirect|Bulk delete, no confirm (3 rules)|literals|

**US poll queue, `blueprints/us_poll_import.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/us/import`|`import_form` (161)|`us_poll_import.html`|Read-only|`test_console_us.py:113-118,847-849`; `test_console_csrf.py:72,155`; `test_us_poll_queue.py` (10)|
|POST|`/us/import/start`|`start` (167)|redirect|Scrapes about 90 pages|`test_console_csrf.py:96,118,180`|
|GET|`/us/import/<token>`|`queue` (234)|`us_poll_queue.html`|Plans lazily, per-token lock|33 literals; "Poll 1 of 3", "Approve all 3 remaining in Michigan"|
|POST|`…/<token>/confirm`|`confirm` (283)|redirect|DB write|8 literals|
|POST|`…/<token>/skip`|`skip` (331)|redirect|Cursor only|6 literals|
|POST|`…/<token>/approve-race`|`approve_race` (364)|redirect|Bulk DB write|3 literals|
|GET, POST|`…/<token>/finish`|`finish` (409)|`us_poll_queue_summary.html`|GET mutates: auto matchup tracking DB write, then SUB US models + export once|12 literals|

**US results, `blueprints/us_results.py`**

|Method|URL|Endpoint (line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|GET|`/us/results`|`us_results_form` (298)|`us_results_form.html`|Reads cache listing|`test_console_us_results.py:252`|
|POST|`/us/results/preview`|`us_results_preview` (304)|`us_results_preview.html`|Fetch adds cache snapshot, no DB write, 400 on invalid|`:307-340,410,482`, `:167` token regex, `:523`|
|POST|`/us/results/confirm/<token>`|`us_results_confirm` (395)|`us_results_result.html`|File and DB write in-process, holds slot, never exports|`:648`, `:706,1010,1090` Location|
|POST|`/us/results/export`|`us_results_export` (460)|`command_result.html`|SUB full export, holds slot|`:1060,1075`|
|POST|`/us/results/current-senate`|`us_results_current_senate` (497)|`command_result.html`|Writes `senate-current.json` in-process, holds slot|`:1208`|

**Site and database**

|Method|URL|Endpoint (file:line)|Template|Effect|Tests|
|-|-|-|-|-|-|
|POST|`/site/rebuild`|`rebuild_site_data` (`site.py:18`)|`command_result.html`|SUB full export, no confirm|`test_console_site.py:27-28`|
|POST|`/site/rebuild-database`|`rebuild_database` (`site.py:46`)|`command_result.html`|SUB `rebuild_database.py` (3600 s), confirm on home|`:38,57`; `test_console_csrf.py`|
|POST|`/db/backup`|`backup_database` (`db_admin.py:22`)|`command_result.html`|Archives DB in-process|`test_console_db_admin.py` (8 literals)|
|POST|`/db/restore`|`restore_database` (`db_admin.py:51`)|`command_result.html`|Replaces DB, confirm on home|`test_console_db_admin.py` (6 literals)|

**Cross-cutting**
- `same_origin_only` on all POSTs, with `TRUSTED_HOSTS` pinned (`console/__init__.py`, `console/csrf.py`).
- `backup_after_write` (`__init__.py:63`) requests a backup after POST/PUT/PATCH/DELETE, excluding endpoints starting `db_admin.` (`:79`).
- `model_run_slot` (`services/us_models.py:228`) serialises US runs, the US queue finish, US results import, export and Current Senate. It does not cover Westminster, Holyrood, by-election or site exports (`README.md:1328-1329` acknowledges this).

### 2. How a user navigates today

`base.html` holds one link (`:11`) plus flash messages. CSS still has `.topbar a { margin-left: 14px }` and `justify-content: space-between`, leftovers from a nav that no longer exists. There are no breadcrumbs or current-page marker.

|Home card|Items in order (`home.html` line)|
|-|-|
|Westminster|Import Poll (12), Import By-Election (13), View Polls (14), Run Westminster Model (15), View Westminster Outputs (16), Uncertainty Diagnostics (17)|
|Holyrood|Import Scottish Polls POST (24), Run Holyrood Model (27), View Holyrood Outputs (28)|
|USA|Import US Polls (35), Import US Results (36), Run US Models POST with rebuild box (37-48), President Matchup (50), Senate Race Matchups (51), House Race Matchups (52), View House/President/Senate Outputs (53-55)|
|Site|Rebuild Site Data POST (63), Rebuild Database POST + confirm (67)|
|Database (admin)|Backup now POST (78), Restore newest archive POST + confirm (82)|

|Page|Way out|
|-|-|
|`model_run`, `holyrood_model_run`, `import_form`, `by_election_form`, `poll_list`, `poll_detail`, `model_outputs`|brand link only|
|`model_output_detail`|"Back to Model Outputs" (17), Westminster-only uncertainty links (18)|
|`poll_coverage`|"All polls" (49)|
|`command_result`|one button, default "Back to home"|
|`wikipedia_summary`, `us_poll_queue_summary`|"Back to home" + import page|
|`us_matchup`, `us_race_matchups`|sibling matchup links|
|uncertainty run pages|sub-nav `uncertainty_run_base.html:5-11`|
|calibration, validation|text link to hub only (`uncertainty_calibration.html:466`, `uncertainty_validation.html:5`)|

**Implicit IA.** Home is by family, then roughly by stage, then cross-cutting Site and Database. URLs and blueprints are Westminster by data type at the root (`/import`, `/models/*`, `/by-elections`), Holyrood and US by family, and uncertainty alone under `/westminster/`. Stages exist only by convention; US lists Matchups after Run although matchups are an input to Run.

**Reachability.** Every `url_for` literal resolves, so there are no dead links. `*_output_detail` endpoints only look unreferenced because they are reached via template variables (`model_outputs.html:74-75`). Mild orphans: `/polls/coverage` (only from `poll_list.html:10`); calibration and validation (hub only, absent from sub-nav); `/us/results/export` (only after an import). US model runs have no GET page; the form exists only on home.

**Stale copy.** `import_form.html:12` and `us_poll_import.html:13` warn about "the automatic reload after any code change", and `services/preview.py:15` says the reloader is on, but it is off by default (`server.py:6-10`). `blueprints/westminster.py:1` still says "Wikipedia poll sync". `models/holyrood/run_holyrood_uns_model.py:10` cites `/holyrood/import-polls`.

|#|Inconsistency|Where|
|-|-|-|
|1|"Run model" has three URL shapes|`/models/run`, `/holyrood/run-model`, `/us/run-models`|
|2|Outputs in three shapes|`/models/outputs`, `/holyrood/outputs`, `/us/<chamber>/outputs`|
|3|Poll import: three shapes, three UX models|`/import` (queue + single importer), `/holyrood/import-polls` (one-click, unreviewed), `/us/import` (queue)|
|4|Queue URL naming|`/import/wikipedia/<t>` vs `/us/import/<t>`, both Wikipedia queues|
|5|Westminster unprefixed except uncertainty|`/import`, `/models/*`, `/by-elections` vs `/westminster/uncertainty/*`|
|6|"View Polls" under Westminster lists every family|`home.html:14`, `polls.py:94`|
|7|Global URL, Westminster-only data|`/polls/coverage`, `polls.py:253-271`|
|8|Blueprint names hide family|`poll_import` is Westminster; `westminster` holds only model and outputs; `uncertainty*` are Westminster|
|9|Endpoint naming|`westminster.model_outputs` vs `holyrood.holyrood_outputs` vs `us.house_outputs`|
|10|Template naming|`model_run.html` is Westminster only, `model_outputs.html` is shared; `import_*`, `wikipedia_*` are Westminster|
|11|Labels vs headings|"Run Westminster Model" opens "Run UNS Model"; "Import Poll" opens with "Catch Up from Wikipedia"; "Wikipedia Catch-Up" vs "US Poll Import"|
|12|"Export" under seven names|"Rebuild Site Data", "Export to site", "Simulation JSON Export", "Static Data Export", "Prediction simulation exported.", "Models and Export", "Model and Export"; plus `--current-simulation` vs full export|
|13|Rebuild Database in "Site", Backup/Restore in "Database"|`home.html:60-85`|
|14|Uneven confirms|Present: rebuild DB, restore, US history rebuild on home. Absent: poll delete (`poll_list.html:80`), output delete and bulk (`model_outputs.html:39,75`), matchup-page rebuild (`us_matchup.html:43`, `us_race_matchups.html:82`), site rebuild, abandon queue|
|15|Run forms|Westminster and Holyrood have forms with dry run; US only a home button|
|16|Locks|Slot is US-only; Westminster finish has no per-token lock, US has `_serialised` (`us_poll_import.py:409-410` vs `poll_import.py:435`)|
|17|Backups|`backup_after_write` skips GET, but queue finish writes on GET, so a finish reached by redirect requests no backup|
|18|Name-coupled behaviour|`model_output_detail.html:18` tests `startswith('westminster.')`; `__init__.py:79` tests `startswith("db_admin.")`. A rename silently changes behaviour|
|19|Duplicated constant|`"UK Constituencies post 2022"` in `poll_import.py:74`, `uncertainty_runs.py:20`, `westminster.py:139`; `polls.py` and `uncertainty.py` import it from a blueprint module|
|20|Blueprint cross-imports|`uncertainty_runs` imports `fmt` from `uncertainty`; `uncertainty_explorer` imports `context` from `uncertainty_runs`|

|Concept|Westminster|Holyrood|US|
|-|-|-|-|
|Poll review|Queue + single URL importer (11 pollsters)|None, one-click|Queue, race approval|
|Result import|By-elections only|None in console|NYT flow|
|Run form with dry run|Yes|Yes|No|
|Matchups|No|No|Yes|
|Uncertainty diagnostics|Full, 12 rules|None|None|
|Run lock|No|No|Yes|
|Export after run|`--current-simulation`|Full|Full|
|Outputs|One|One|Three chambers, one combined run|

|Near-duplicates|Size|Difference|
|-|-|-|
|`model_run.html` / `holyrood_model_run.html`|97 / 75|Run-result blocks (76-96, 54-74) differ by one `<h4>`; form differs only in fields; repeated `selectattr` lookups|
|`wikipedia_queue.html` / `us_poll_queue.html`|88 / 104|Same progress line, warnings, failed card, skip/abandon card|
|`wikipedia_summary.html` / `us_poll_queue_summary.html`|215 / 316|Same Imported, Failed, Skipped, Not Reviewed sections|
|`import_preview.html` / Parsed Document in `wikipedia_queue.html`|31 / part|Same metadata and row tables|
|Outputs quad|`westminster.py:256-367`, `holyrood.py:282-372`, `us.py:432-504`|Three copies; US already has a factory|
|Run-result dict|`westminster.py:214-245`, `holyrood.py:244-271`|Same export branch|

### 3. Look and feel

- **CSS:** `static/style.css`, 316 lines, flat, no `:root` tokens, hard-coded colours, light only, one 860 px breakpoint. Unused: `.action-grid-three`. Used but undefined classes are hooks only (`model-select`, `poll-n`, `uncertainty-export*`, `uncertainty-stored`), and tests assert two of them.
- **Inline:** 3 `style=` in `by_election_preview.html`, 1 deliberate bar width in calibration, 3 `onsubmit` confirms on home, one inline script (`model_outputs.html:89-182`), and unpinned Chart.js from a CDN (`:88`), so charts silently vanish offline.
- **Reuse:** one macro module (`_poll_rows_table.html`, 3 users), one include (`_uncertainty_comparison.html`), one sub-base (`uncertainty_run_base.html`, 7 children), 5 local macros. No form-field, key-value or nav partial.
- **Readability:** six uncertainty templates carry 14 lines over 400 characters. These are the key transparency pages and the hardest to edit.
- **Results:** stdout, stderr, exit code and command in `.terminal-output`, on `command_result.html` or at the foot of run forms.
- **Long jobs:** all synchronous. A POST blocks up to 45 min (model + export), 1 h (DB rebuild), or hours (US history rebuild). No progress or job record. Output exists only in that response and no run log is kept. Queues live in memory and die on restart.

### 4. Layout versus folders

|Console piece|Depends on|Note|
|-|-|-|
|`paths.py`|`models/{westminster,holyrood,us}`, `polls/importers/holyrood`, `scripts/*`, site results|The one clean seam|
|`importers_registry.py`|`polls.importers.westminster.*`|Direct imports, Westminster-only despite the name|
|`us_poll_queue`, `forms`, `us_matchups`, `us_poll_import`|`polls.importers.us.*`|Direct imports|
|`by_elections.py`|`scripts.by_election_import`|In-process|
|`us_results.py`|`scripts.us_results.*` (7 modules)|In-process|
|`uncertainty_export.py`|`scripts.export.*`|In-process|
|`polls`, `uncertainty*`|`model_support.*`|Shared by name, Westminster-heavy|

The console integrates two ways: subprocess for models and export, in-process for importers and US results. Moving `polls/` or `scripts/` touches `paths.py` and about a dozen import lines. `blueprints/` and `services/` are flat and mix families. On disk, stage is the top level and family the level below (`models/westminster`), so family is the axis both sides share. Console test files never assert a template filename, so moving templates is free. Moving modules costs about 108 monkeypatch path strings (75 in `test_console_*.py`, 33 in the two queue test files).

Suggested console layout once `data/` moves:
- Root: `__init__.py`, `csrf.py`, `db.py`, `paths.py`, `families.py`, `nav.py`
- `shared/`: `runner`, `preview`, `poll_queue` (today's `wikipedia_queue`), `trends`, `model_outputs`
- `westminster/`: model, outputs, `poll_import`, `by_elections`, `uncertainty*`, `importers_registry`
- `holyrood/`
- `us/`: `us`, `us_poll_import`, `us_results`, `us_models`, `us_matchups`, `us_poll_queue`
- `polls/`, `system/` (`site`, `db_admin`)
- `templates/` mirrored, plus `templates/_macros/`

### 5. Recommended information architecture

**By election family, with a fixed stage order inside each, plus System.** The families have genuinely different stages (11 pollster importers vs one button vs a race-level queue; matchups and NYT results are US-only; uncertainty is Westminster-only). A session is usually about one family, this matches home's existing mental model, and it matches the on-disk family split. Families show only the stages they really have, so there is no empty Diagnostics for Holyrood or US.

- **Home** `/`: links to families and System, no POST forms
- **Westminster** `/westminster`
  - Polls: list `/polls?family=westminster`, Import, Coverage
  - By-elections
  - Model
  - Outputs
  - Uncertainty (all existing views, Calibration and Validation added to sub-nav)
- **Holyrood** `/holyrood`
  - Polls: list `/polls?family=holyrood`, Import button
  - Model
  - Outputs
- **United States** `/us`
  - Polls: list `/polls?family=us`, Import
  - Matchups: President, Senate, House
  - Results (NYT, Current Senate, Export)
  - Model (run, rebuild history)
  - Outputs: House, President, Senate
- **System** `/system`
  - Publish: Rebuild Site Data
  - Database: Rebuild, Backup, Restore
  - All polls `/polls`

`console/nav.py` is one data structure; a context processor exposes it to `base.html`; the current section comes from `request.endpoint`.

**Route mapping.** Rename only rules whose URL misleads: 28 of 74. Endpoints and modules stay, so `url_for` calls and monkeypatch paths are untouched.

|Old|New|Redirect|
|-|-|-|
|`GET /import`|`GET /westminster/polls/import`|Yes|
|`POST /import/preview`, `/import/confirm/<t>`, `/import/wikipedia/start`|Same under `/westminster/polls/import`|No|
|`/import/wikipedia/<t>` and `/confirm`, `/skip`, `/finish`|Same under `/westminster/polls/import/wikipedia/<t>`|No (tokens die on restart)|
|`GET,POST /models/run`|`GET,POST /westminster/model`|GET only|
|`GET /models/outputs`, `/models/outputs/<id>`|`GET /westminster/outputs`, `/<id>`|Yes|
|`POST /models/outputs/<id>/delete`, `/delete-selected`|Same under `/westminster/outputs`|No|
|`GET /by-elections`|`GET /westminster/by-elections`|Yes|
|`POST /by-elections/preview`, `/confirm/<t>`|Same under `/westminster/by-elections`|No|
|`POST /holyrood/import-polls`|`POST /holyrood/polls/import`|No|
|`GET,POST /holyrood/run-model`|`GET,POST /holyrood/model`|GET only|
|`POST /us/run-models`|`POST /us/model`, new `GET /us/model`|No|
|`GET /us/import`|`GET /us/polls/import`|Yes|
|`POST /us/import/start` and token routes|Same under `/us/polls/import`|No|
|New|`GET /westminster`, `/holyrood`, `/us`, `/system`|n/a|
|Unchanged|`/`, all `/polls*`, all `/westminster/uncertainty*` (12), `/holyrood/outputs*`, `/us/<chamber>/outputs*`, `/us/results*`, `/us/president/matchup`, `/us/matchups*`, `/site/*`, `/db/*`|n/a|

**Redirects:** 302 for the seven GET pages only, in a small table to delete later (the repo already removes old routes and asserts 404: `test_console_us.py:121-125`). Never redirect POSTs: a 307 would replay an action from a stale tab, while a 404 is harmless. The rename phase is separable; skipping it leaves a consistent nav over inconsistent URLs. README URLs need updating at `README.md:738,1267,1457`.

**Consolidate:**

|Item|Action|
|-|-|
|`base.html`|Top nav and section nav from `nav.py`, breadcrumbs|
|`_macros/run_result.html`|One block for command, exit code, stdout, stderr, optional export; label is a parameter so tested strings survive|
|`model_run.html`|One form with a data-driven field list; drop the Holyrood copy and `selectattr` lookups|
|Outputs routes|Generalise `_register_chamber_routes` to all five, keeping endpoint names; take care with `get_db` monkeypatch targets|
|`_macros/queue.html`|Progress, warnings, failed card, skip/abandon card, result tables for both queues|
|`_macros/forms.html`, `_macros/meta.html`|Field macros, key-value line|
|`_uncertainty_nav.html`|Extract from run base, include Calibration and Validation|
|Long-line uncertainty templates|Reformat only|
|`model_outputs.html`|Move JS to `static/`, pin or vendor Chart.js|
|`style.css`|`:root` tokens, nav styles, keep one file|
|`runner.py`|`run_model_then_export` helper|
|`families.py`|Single home for map names|
|Copy|Fix stale reload warnings, pick one word for export|

**Must remain exactly:**
- Transparency: every uncertainty page and its numbers; poll n, n source, n_eff, MoE; `/polls/coverage`; every `us_results_preview` section (files read, pipeline, per-target diff, warnings, party mapping, races); both queue summaries including Scrape Diagnostics; raw stdout, stderr, exit code, command on every run; hub export preview; interrupted-run notes (`us.py:65-87`) and `NO_MATCHUP_NOTE`.
- Guards: `same_origin_only`, `TRUSTED_HOSTS`, `backup_after_write` with its `db_admin.` check, preview tokens and `"type"` tags, `expected_index`, `_serialised` locks, `model_run_slot` at every call site, dry-run default true.
- Confirms: Rebuild Database, Restore, Rebuild all US history, including the tested text (`test_console_site.py:40`, `test_console_us.py:731`).
- Never link a queue `/finish` as a plain `<a>`, since GET mutates.
- Replace the `westminster.` prefix test at `model_output_detail.html:18` with an explicit flag before any endpoint rename.

|Change|Kind|
|-|-|
|Nav, section nav, breadcrumbs, family landing pages, `/system`|Re-layout|
|POST forms moved off home, `GET /us/model` hosting the existing form|Re-layout (one more click)|
|Template/CSS consolidation, JS extraction|Re-layout|
|URL renames + GET redirects|Addressing only|
|Outputs route factory|Refactor, same behaviour|
|`/polls?family=` filter|Real, small: new param and map-to-family lookup|
|Confirms on deletes, matchup rebuild, Rebuild Site Data|Real: new friction (recommended)|
|Backup after state-changing GET finish; per-token lock on Westminster finish|Real, small fixes|
|Status numbers on home|Real: home gains DB reads; defer|
|One lock across all exports|Real: new refusals|
|Persisted run log; background jobs with progress|Real; jobs are large and change POST semantics; defer|
|Intermediate-number pages for Holyrood and US|Real, new work|

**Transparency gap to decide on:** Westminster has 12 diagnostic rules. Holyrood and US outputs in the console show final totals, regional diffs and seats; anything intermediate exists only as run stdout, which is not persisted. I did not read those scripts' stdout, so how much they print is unconfirmed.

**Phases:**
1. Shell: `nav.py`, base nav, landing pages, `/system`, move POST forms.
2. Consolidate templates, CSS, outputs factory, `families.py`.
3. URL renames and redirects (optional, mechanical).
4. Restructure `console/` alongside `data/`. Phases 1-3 touch no import paths, so they don't conflict with the `data/` move.
5. Small fixes (issues 14, 16, 17) as separate commits, then deferred items.

**Rejected alternative: top level by pipeline stage** (Import / Model / Outputs / Publish with a family picker). Import would merge a pollster-URL form, a one-click button and a race-level queue. Matchups, NYT results and Uncertainty have no natural stage. A picker adds a click to every step of a one-family session, and conditional form logic becomes where family differences live. Stage stays as the second-level order.

### 6. Decisions for the owner

1. Rename URLs (phase 3), or change only the nav?
2. Add confirms to poll/output deletes and matchup-page rebuilds?
3. `/polls?family=` filter, or one unfiltered list?
4. Are background jobs or a run log in scope, given output disappears today?
5. Does the transparency rule require new Holyrood and US diagnostic views?

### 7. Verification

**Constraints.** With no browser here, the Flask test client covers routes, redirects, HTML and forms, but not Chart.js rendering, `confirm()` dialogs, select-all JS or layout. Run any walk as a pytest test, not an ad-hoc script: `tests/conftest.py`'s autouse fixture is what keeps `Database()` off the live file, because `.env` loads with `override=True`. Any POST that shells out must have its runner monkeypatched, since the subprocess would load `.env` itself and hit the live database.

**Keep unchanged:** `test_console_model_outputs.py`, `test_console_units.py`, `test_console_uncertainty.py` (except `:307`), `test_console_uncertainty_runs.py`, `test_server.py`, queue service-level tests, `test_console_db_admin.py` route tests, `test_console_csrf.py` logic.

**Must change in phase 1:**

|Test|Change|
|-|-|
|`test_console_us.py:148,159,725,841`|Point at `/us` or `/us/model`; `:161` exact anchor and `:731` exact onsubmit need a decision|
|`test_console_site.py:35-40`, `test_console_db_admin.py:172-178`|Point at `/system`|
|`test_console_us_results.py:286`, `test_console_uncertainty.py:307`|Point at the family page|
|Location `/` at `test_console_holyrood.py:236`, `test_console_site.py:57`, `test_console_us.py:679,997`|Only if failure redirects move off home; simplest to leave|

**Must change in phase 3:** about 465 URL literals exist, roughly 240 affected, mostly `test_us_poll_queue.py` (83), `test_wikipedia_queue.py` (39), `test_console_westminster.py` (30), `test_console_poll_import.py` (about 29), `test_console_by_elections.py` (23). Also the rule-set checks (`test_console_us.py:106-141,834`) and `test_console_csrf.py:96,118,155,180`. **Phase 4:** the 108 monkeypatch strings.

**Add:**

|New test|Catches|
|-|-|
|Crawl from `/` over a seeded temp DB following internal `<a href>`, no 4xx/5xx|Dead links|
|Every parameterless GET rule reachable from `/`|Orphans|
|Every `<form method=post action=…>` resolves to a POST rule|Dead actions|
|Every `nav.py` entry resolves and renders|Nav drift|
|Old GET URLs redirect; old POST URLs 404 and run nothing|Redirect rules|
|Destructive actions keep their confirm|Lost guards|
|`/` has no `method="post"`|Forms creeping back|

Run `cd data && ./run_tests.sh` after each phase.

**Owner's manual walk-through (real browser):**
1. Home: all families and System reachable, no forms.
2. Westminster: list, single import preview without confirming, start and abandon a queue, dry-run model, output detail with uncertainty links.
3. Uncertainty: hub, calibration, validation, budget (drill into a party), results, history, sims filter, one draw, one seat replay; numbers match pre-change for the same run.
4. Holyrood: dry-run model, outputs chart renders.
5. US: import form without starting, three matchup pages, results preview from cache without confirming, model page rebuild confirm appears, three outputs charts.
6. System: confirm dialogs on Rebuild Database and Restore (cancel both).
7. Delete and bulk delete on a disposable output; select-all JS works.
8. Stdout, stderr and exit code still display; nav is correct at narrow width.
